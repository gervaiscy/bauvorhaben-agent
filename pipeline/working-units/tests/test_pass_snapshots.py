"""Explicit BLB exclusion and cumulative snapshots are independently observable."""
import copy
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bauvo_working_units.project_flow import Document, extract_project_units
from bauvo_working_units.project_facts import collect_project_facts
from bauvo_working_units.pass_snapshots import PassSnapshots, PASS_NAMES
from bauvo_working_units.trace import ModelRequestFailed
from bauvo_working_units.cli import main
from test_schema6_flow import fixture


def document(key, text, source_type='project'):
    return Document('D1','Contract',key+'.pdf',schema_version=6,source_key=key,
                    source_type=source_type,pages=({'pdf_page':1,'text':text},))


class Model:
    def __init__(self, fail_timing=False):
        self.calls=[];self.fail_timing=fail_timing
    def extract(self,prompt):
        kind=prompt.split('.',1)[0];data=json.loads(prompt.split('SOURCE DATA:\n')[1]);self.calls.append((kind,data))
        if kind=='PAGE_PROJECT_FACTS':return {'verpflichtungen':[]}
        c={'pdf_page':data['source_pages'][-1]['pdf_page'],'quote':data['source_pages'][-1]['text']}
        if kind=='PAGE_EXTRACTION':return {'working_units':[{'name':'Planung','action':'Planung bestätigen',
            'actor':'Kunde','performed_by':'Kunde','citations':[c]}],'carry_forward':''}
        if kind=='PAGE_OFFERS':return {'offers':[{'name':'Zusatzpaket','provider':'Anbieter','description':'Zusatzpaket',
                                               'terms':[],'citations':[c]}]}
        if kind=='PAGE_TIMING' and self.fail_timing:
            raise ModelRequestFailed([{'finish_reason':'MAX_TOKENS','raw_response':'{unfinished'}])
        rows=[]
        for unit in data['working_units']:
            row={'unit_id':unit['id']}
            if kind=='PAGE_TIMING':row.update(status='NOT_STATED',timing_rules=[],missing_evidence=[])
            elif kind=='PAGE_CONSEQUENCES':row.update(status='NOT_STATED',consequences=[],citations=[])
            else:row.update(applicability='UNKNOWN',modality='UNCLEAR',modality_citations=[],
                            reason='Selection missing',applicability_rules=[],missing_evidence=['Selection'])
            rows.append(row)
        return {'assessments':rows}


class BlbSourceTests(unittest.TestCase):
    def test_excludes_explicit_blb_even_with_contract_filename_and_keeps_project_with_blb_filename(self):
        blb=document('contract','Vereinbart ist ein bestimmtes Paket.','blb')
        contract=document('BLB-named-file','Ein konkretes Paket ist vereinbart.')
        model=Model();result=collect_project_facts([blb,contract],model)
        self.assertEqual([data['source_pages'][0]['text'] for kind,data in model.calls],['Ein konkretes Paket ist vereinbart.'])
        self.assertEqual(result['excluded_sources'][0]['source_key'],'contract')
        self.assertEqual(result['excluded_sources'][0]['reason'],'general_blb')
        self.assertEqual(len(result['source_snapshots']),1)

    def test_blb_only_has_no_fact_requests_but_still_has_tasks_and_unknown_project_scope(self):
        model=Model();result=extract_project_units([document('source','Der Kunde bestätigt die Planung.','blb')],model)
        self.assertNotIn('PAGE_PROJECT_FACTS',[kind for kind,data in model.calls])
        self.assertEqual(len(result['working_units']),1)
        self.assertEqual(result['project_facts']['status'],'no_project_sources')
        app=next(data for kind,data in model.calls if kind=='PAGE_APPLICABILITY')
        self.assertEqual(app['project_facts']['facts'],[])
        self.assertNotIn('excluded_sources',app['project_facts'])
        self.assertEqual(result['project_facts']['excluded_sources'][0]['reason'],'general_blb')

    def test_conflicting_roles_fail_before_any_provider_request(self):
        source=document('same','Source text')
        with self.assertRaisesRegex(ValueError,'Conflicting source snapshots'):
            collect_project_facts([source,replace(source,source_type='blb')],SimpleNamespace(extract=lambda p:self.fail('paid request')))


class CumulativeSnapshotTests(unittest.TestCase):
    def test_each_snapshot_extends_previous_units_and_final_matches_last_snapshot(self):
        source=document('source','Der Kunde bestätigt die Planung.','blb');model=Model();saved=[]
        result=extract_project_units([source],model,on_pass=lambda name,data:saved.append((name,data)))
        self.assertEqual([name for name,data in saved],list(PASS_NAMES))
        states=[data for name,data in saved[1:]]
        ids=[state['working_units'][0]['id'] for state in states]
        self.assertEqual(len(set(ids)),1)
        self.assertEqual([len(state['offers']) for state in states],[0,1,1,1,1])
        self.assertEqual([len(state['working_units'][0]['reviews']) for state in states],[0,0,1,2,3])
        self.assertTrue(all(state['working_units'][0]['gemini']==states[0]['working_units'][0]['gemini'] for state in states))
        self.assertEqual(states[-1],result)
        self.assertEqual(states[0]['working_units'][0]['reviews'],[])  # Later passes never mutated it.
        self.assertEqual([kind for kind,data in model.calls],list(PASS_NAMES)[1:])

    def test_snapshots_are_written_before_next_pass_and_not_overwritten(self):
        source=document('source','Der Kunde bestätigt die Planung.','blb')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);writer=PassSnapshots(root/'passes',root/'facts.json');model=Model();before={}
            def extract(prompt):
                kind=prompt.split('.',1)[0];previous=list(PASS_NAMES)[:list(PASS_NAMES).index(kind)]
                for prior in previous:self.assertTrue((root/'passes'/PASS_NAMES[prior]).is_file())
                for file,contents in before.items():self.assertEqual(file.read_bytes(),contents)
                return model.extract(prompt)
            def save(name,data):
                writer(name,data)
                before.update({p:p.read_bytes() for p in (root/'passes').glob('*.json')})
            result=extract_project_units([source],SimpleNamespace(extract=extract),on_pass=save)
            self.assertEqual(json.loads((root/'passes/05-applicability.json').read_text()),result)
            original=(root/'passes/01-aufgaben.json').read_bytes()
            with self.assertRaises(FileExistsError):writer('PAGE_EXTRACTION',{'changed':True})
            self.assertEqual((root/'passes/01-aufgaben.json').read_bytes(),original)
            with self.assertRaises(FileExistsError):PassSnapshots(root/'passes',root/'other-facts.json')

    def test_snapshot_callback_cannot_mutate_raw_facts_in_later_snapshots(self):
        source=document('source','Der Kunde bestätigt die Planung.');saved=[]
        def save(name,data):
            saved.append((name,copy.deepcopy(data)))
            if name=='PAGE_EXTRACTION':data['gemini_responses'][0]['gemini']['verpflichtungen'].append({'invented':True})
        result=extract_project_units([source],Model(),on_pass=save)
        self.assertEqual(result['project_facts']['gemini_responses'][0]['gemini'],{'verpflichtungen':[]})
        self.assertEqual(result,saved[-1][1])

    def test_failed_pass_retains_earlier_snapshots_and_visible_partial_results(self):
        saved=[];source=document('source','Der Kunde bestätigt die Planung.','blb')
        result=extract_project_units([source],Model(fail_timing=True),on_pass=lambda name,data:saved.append((name,data)))
        self.assertEqual(saved[1][1]['status'],'recorded')
        self.assertEqual(saved[3][1]['status'],'request_failed')
        self.assertEqual(result['status'],'request_failed')
        self.assertEqual(len(result['working_units'][0]['reviews']),2)
        self.assertEqual(saved[1][1]['working_units'][0]['reviews'],[])
        self.assertEqual(next(f for f in result['review_findings'] if f['code']=='model_request_failed')['pass'],'PAGE_TIMING')

    def test_cli_marks_blb_explicitly_and_writes_final_identical_to_last_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inputs=root/'inputs';inputs.mkdir();fixture(inputs,count=1)
            model=Model()
            argv=['units','--pages',str(inputs/'pages.json'),'--documents',str(inputs/'documents.json'),
                  '--source-type','blb','--project','unused','--output',str(root/'working-units.json')]
            with patch.object(sys,'argv',argv),patch('bauvo_working_units.cli.GeminiModel',return_value=model):main()
            self.assertNotIn('PAGE_PROJECT_FACTS',[kind for kind,data in model.calls])
            folder=root/'working-units.passes'
            self.assertEqual((root/'working-units.json').read_bytes(),(folder/'05-applicability.json').read_bytes())
            self.assertEqual(len(list(folder.glob('*.json'))),6)
            self.assertEqual(json.loads((root/'working-units.project-facts.json').read_text())['status'],'no_project_sources')
            with patch.object(sys,'argv',argv),patch('bauvo_working_units.cli.GeminiModel') as provider:
                with self.assertRaises(SystemExit):main()
                provider.assert_not_called()

    def test_cli_context_source_types_are_applied_and_validated_before_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a=root/'a';b=root/'b';a.mkdir();b.mkdir();fixture(a,count=1);fixture(b,count=1)
            model=Model()
            argv=['units','--pages',str(a/'pages.json'),'--documents',str(a/'documents.json'),
                  '--context-pages',str(b/'pages.json'),'--context-documents',str(b/'documents.json'),
                  '--context-type','blb','--project','unused','--output',str(root/'units.json')]
            # Same source identity with contradictory roles is rejected before requests.
            with patch.object(sys,'argv',argv),patch('bauvo_working_units.cli.GeminiModel',return_value=model):
                with self.assertRaisesRegex(ValueError,'Conflicting source snapshots'):main()
            self.assertEqual(model.calls,[])
