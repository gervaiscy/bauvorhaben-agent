from historical_facts import collect_historical_facts
from clause_fixtures import labeled_answer
from clause_fixtures import decode_prompt
"""Incomplete indexes cannot leak into analysis, caches or resumable successes."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bauvo_working_units import pipeline
from bauvo_working_units.cache import CachedModel
from bauvo_working_units.fact_artifact import validate_facts
from bauvo_working_units.page_flow import _prompt, extract_page_units
from bauvo_working_units.project_facts import collect_project_facts, facts_view
from bauvo_working_units.trace import RawRunRecorder
from clause_fixtures import answer_for, entries_for, index_answer, historical_index, interpreted
from test_project_facts import document
import test_modular_pipeline as modular


class ClauseIndexBoundaryTests(unittest.TestCase):
    def test_fact_only_cli_saves_incomplete_accounting_and_exits_unsuccessfully(self):
        from bauvo_working_units.cli import main
        from test_schema6_flow import fixture
        with TemporaryDirectory() as tmp:
            root=Path(tmp);inputs=root/'inputs';inputs.mkdir();fixture(inputs,count=1)
            calls=[]
            def extract(prompt):
                calls.append(prompt)
                return {'verpflichtungen':[], 'nicht_als_fakt_erfasst':[]}
            argv=['units','--pages',str(inputs/'pages.json'),'--documents',str(inputs/'documents.json'),
                  '--project','unused','--output',str(root/'facts.json'),'--stop-after','project-facts']
            with patch('sys.argv',[*argv,'--max-new-calls','1']), patch('bauvo_working_units.cli.GeminiModel',
                    return_value=SimpleNamespace(extract=extract)):
                with self.assertRaisesRegex(SystemExit,'Incomplete project facts'):main()
            self.assertEqual(len(calls),1)
            self.assertEqual(json.loads((root/'facts.json').read_text())['status'],'incomplete')
            self.assertTrue((root/'facts.passes/00-project-facts.json').is_file())
            self.assertEqual(len(json.loads((root/'facts.gemini-raw.json').read_text())),1)

    def test_completed_but_incomplete_answer_replays_and_still_blocks_without_paid_retry(self):
        fixture=modular.ModularPipelineTests();fixture.setUp();self.addCleanup(fixture.tmp.cleanup)
        fixture.page_count=11
        class Model(modular.FakeModel):
            broken=True
            last_raw_response=None
            last_finish_reason=None
            def extract(self,prompt):
                answer=super().extract(prompt)
                if (prompt.startswith('PAGE_PROJECT_FACTS.') and self.broken
                        and self.calls[-1][1]['source_pages'][-1]['pdf_page']==11):
                    answer={'verpflichtungen':[], 'nicht_als_fakt_erfasst':[]}
                self.last_raw_response=json.dumps(answer);self.last_finish_reason='STOP'
                return answer
        model=Model()
        with self.assertRaisesRegex(RuntimeError,'Incomplete project facts'):
            fixture.run_pipeline(model)
        first=fixture.output/'03-project-facts/attempt-01'
        original={p:p.read_bytes() for p in first.rglob('*') if p.is_file()}
        manifest=pipeline.read(fixture.output/'pipeline.json')
        self.assertEqual(manifest['steps']['03-project-facts']['status'],'failed')
        self.assertFalse(any(k.startswith('04-') for k in manifest['steps']))
        result=pipeline.read(first/'result.json')
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['gemini_responses'][0]['response_validation']['status'], 'invalid')
        self.assertEqual(len(list((first/'requests').glob('*.json'))),1)
        self.assertEqual(len(pipeline.read(first/'raw.json')),1)
        pages=fixture.output/manifest['steps']['01-ocr/contract-01']['artifact'];before=pages.read_bytes()
        model.broken=False;model.calls.clear();fixture.commands.clear()
        with self.assertRaisesRegex(RuntimeError,'Incomplete project facts'):
            fixture.run_pipeline(model,resume=True)
        self.assertFalse(any(name=='PAGE_PROJECT_FACTS' for name,_ in model.calls))
        self.assertEqual(fixture.commands,[])
        self.assertEqual(pages.read_bytes(),before)
        self.assertEqual(original,{p:p.read_bytes() for p in original})
        self.assertEqual(pipeline.read(fixture.output/'03-project-facts/attempt-02/result.json')['status'],'incomplete')

    def test_model_cache_preserves_and_reuses_raw_incomplete_answers(self):
        doc=document('contract','Eine konkrete Regel.');calls=[]
        data={'source_key':doc.source_key,'document':{'id':doc.id,'title':doc.title},
              'evidence_protocol':'source-backed-interpretations-v6',
              'source_pages':list(doc.pages),'evidence_catalog':entries_for(doc)}
        prompt=_prompt('PAGE_PROJECT_FACTS',data,'de')
        def extract(_):
            calls.append(prompt)
            return {'verpflichtungen':[],'nicht_als_fakt_erfasst':[]} if len(calls)==1 else answer_for(doc)
        with TemporaryDirectory() as tmp:
            root=Path(tmp);model=CachedModel(SimpleNamespace(extract=extract),root/'cache',{'model':'fixture'})
            recorder=RawRunRecorder(model,root/'raw.json')
            recorder.extract(prompt)
            self.assertEqual(len(list((root/'cache').glob('*.json'))),1)
            second=recorder.extract(prompt)
            self.assertEqual(recorder.extract(prompt),second)
            self.assertEqual(len(calls),1)
            self.assertEqual(len(json.loads((root/'raw.json').read_text())),3)

    def test_legacy_artifacts_remain_readable_but_their_paraphrases_are_not_forwarded(self):
        doc=document('contract','Die Bürgschaft gilt ohne erhebliche Mängel.')
        legacy=historical_index(doc,schema=2)
        legacy['facts'][0]['gemini']['verpflichtung']='Erfundene uneingeschränkte Zusage'
        self.assertEqual(validate_facts(legacy,[doc]),legacy)
        payload={'source_pages':[], 'project_facts':facts_view(legacy)}
        prompt=_prompt('PAGE_TIMING',payload,'de')
        self.assertNotIn('Erfundene uneingeschränkte Zusage',prompt)
        actual=decode_prompt(prompt)
        self.assertEqual(actual['project_facts']['facts'][0]['text'],doc.pages[0]['text'])

    def test_exclusion_is_visible_and_remains_citable_by_downstream_reasoning(self):
        contract=document('contract','Eine vertragliche Frist beträgt vier Wochen.')
        answer=labeled_answer([contract], index_answer({'evidence_catalog':entries_for(contract)},include=lambda e:False))
        facts=collect_historical_facts([contract],SimpleNamespace(extract=lambda p:answer))
        blb=document('blb','Der Bauherr bestätigt den Plan.')
        before=copy.deepcopy(facts)
        def extract(prompt):
            phase=prompt.split('.',1)[0];data=decode_prompt(prompt)
            if phase=='PAGE_EXTRACTION':
                return {'working_units':[{'action':'Plan bestätigen','citations':[
                    {'pdf_page':1,'quote':blb.pages[0]['text']}]}],'carry_forward':''}
            if phase=='PAGE_OFFERS':return {'offers':[]}
            excluded=data['project_facts']['excluded_passages'][0]
            self.assertEqual(excluded['document_title'],'Contract')
            self.assertTrue(excluded['reason'])
            identifier=excluded['citation']['evidence_id']
            self.assertIn(identifier,{e['evidence_id'] for e in data['evidence_catalog']})
            return {'assessments':[{'unit_id':u['id'],'answer':'Vier Wochen',
                    'citations':[{'evidence_id':identifier}]} for u in data['working_units']]}
        result=extract_page_units([blb],SimpleNamespace(extract=extract),project_facts=facts,
                                  context_documents=[contract],stop_after='PAGE_TIMING')
        self.assertEqual(result['review_findings'],[])
        self.assertEqual(facts,before)

    def test_shared_condition_can_support_multiple_clauses(self):
        doc=document('contract','Eine erste Pflicht.\n\nEine zweite Pflicht.\n\nBeide gelten nur nach Freigabe.')
        a,b,c=[{'evidence_id':e['evidence_id']} for e in entries_for(doc)]
        answer=labeled_answer([doc], {'verpflichtungen':[interpreted([a,c]),interpreted([b,c])],'nicht_als_fakt_erfasst':[]})
        result=collect_historical_facts([doc],SimpleNamespace(extract=lambda p:answer))
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(len(result['gemini_responses'][0]['coverage']['indexed_ids']),3)
        self.assertTrue(all('nur nach Freigabe' in f['derived']['text'] for f in result['facts']))
