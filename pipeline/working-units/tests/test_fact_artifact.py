"""Artifact handoffs preserve prior evidence and never recollect accepted facts."""
import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

from bauvo_working_units.fact_artifact import load_facts, validate_facts
from bauvo_working_units.project_facts import collect_project_facts
from bauvo_working_units.project_flow import extract_project_units, load_documents
from bauvo_working_units.cli import main
from test_project_facts import document, proposal
from test_schema6_flow import fixture


def collection(doc):
    return collect_project_facts([doc], SimpleNamespace(extract=lambda p:{'verpflichtungen':[proposal(doc)]}))


class FactArtifactTests(unittest.TestCase):
    def test_load_exact_collection_and_findings_without_mutating_input(self):
        doc=document('contract','The company supplies a heat pump.')
        facts=collection(doc)
        facts['review_findings'].append({'code':'unresolved_interpretation'})
        facts['status']='needs_review'
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'facts.json';path.write_text(json.dumps(facts));before=path.read_bytes()
            loaded=load_facts(path,[doc]);self.assertEqual(loaded,facts)
            loaded['facts'][0]['gemini']['verpflichtung']='changed copy'
            self.assertEqual(path.read_bytes(),before)
            self.assertNotEqual(loaded,facts)

    def test_wrong_format_missing_source_and_changed_snapshot_fail(self):
        doc=document('contract','Original');facts=collection(doc)
        for value,sources in [({'verpflichtungen':[]},[doc]),(facts,[]),
                              (facts,[replace(doc,pages=({'pdf_page':1,'text':'Changed'},))]),
                              (facts,[replace(doc,source_type='blb')])]:
            with self.subTest(value=value,sources=sources),self.assertRaises(ValueError):
                validate_facts(value,sources)

    def test_forged_verified_quote_and_source_are_rejected(self):
        doc=document('contract','Original');facts=collection(doc)
        for field,value in [('quote','Invented'),('source_key','other')]:
            bad=copy.deepcopy(facts);bad['facts'][0]['derived']['verified_citations'][0][field]=value
            with self.assertRaises(ValueError):validate_facts(bad,[doc])

    def test_saved_facts_skip_provider_and_preserve_failures_while_stopping_at_actions(self):
        contract=document('contract','Contractor installs a heat pump.')
        blb=replace(document('blb','Owner supplies water.'),source_type='blb')
        facts=collection(contract);facts['status']='request_failed'
        facts['review_findings'].append({'code':'model_request_failed','pass':'PAGE_PROJECT_FACTS','attempts':[]})
        before=copy.deepcopy(facts);calls=[];saved=[]
        def extract(prompt):
            calls.append(prompt.split('.',1)[0])
            self.assertEqual(calls[-1],'PAGE_EXTRACTION')
            return {'working_units':[{'action':'Supply water','citations':[{'pdf_page':1,'quote':blb.pages[0]['text']}]}], 'carry_forward':''}
        result=extract_project_units([blb],SimpleNamespace(extract=extract),context_documents=[contract],
            project_facts=facts,stop_after='PAGE_EXTRACTION',on_pass=lambda p,r:saved.append((p,r)))
        self.assertEqual(calls,['PAGE_EXTRACTION'])
        self.assertEqual([p for p,r in saved],['PAGE_PROJECT_FACTS','PAGE_EXTRACTION'])
        self.assertEqual(result['project_facts'],before);self.assertEqual(facts,before)
        self.assertEqual(result['status'],'request_failed')
        self.assertEqual(result['completed_passes'],['PAGE_EXTRACTION'])
        self.assertEqual([r['pass'] for r in result['gemini_responses']],['PAGE_EXTRACTION'])
        self.assertEqual(result,saved[-1][1])

    def test_cli_reuses_full_artifact_writes_separate_snapshot_and_new_raw_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);local=root/'blb';context=root/'contract';local.mkdir();context.mkdir()
            fixture(local,count=1);fixture(context,count=1)
            payload=json.loads((context/'pages.json').read_text());payload['source']='signed-contract.pdf'
            (context/'pages.json').write_text(json.dumps(payload))
            facts=collection(load_documents(context/'pages.json',context/'documents.json')[0])
            path=root/'saved-facts.json';path.write_text(json.dumps(facts));before=path.read_bytes();calls=[]
            class Model:
                def extract(self,prompt):
                    calls.append(prompt.split('.',1)[0])
                    if calls[-1]!='PAGE_EXTRACTION':raise AssertionError('Unexpected paid stage')
                    return {'working_units':[],'carry_forward':''}
            argv=['units','--pages',str(local/'pages.json'),'--documents',str(local/'documents.json'),
                '--source-type','blb','--context-pages',str(context/'pages.json'),'--context-documents',str(context/'documents.json'),
                '--project-facts',str(path),'--stop-after','aufgaben','--project','unused','--output',str(root/'tasks.json')]
            with patch.object(sys,'argv',argv),patch('bauvo_working_units.cli.GeminiModel',return_value=Model()):main()
            self.assertEqual(calls,['PAGE_EXTRACTION']);self.assertEqual(path.read_bytes(),before)
            result=json.loads((root/'tasks.json').read_text())
            self.assertEqual(result['project_facts'],facts)
            self.assertEqual(json.loads((root/'tasks.units-only.json').read_text()),
                             {'working_units':[r['gemini'] for r in result['working_units']]})
            self.assertEqual(json.loads((root/'tasks.project-facts.json').read_text()),facts)
            self.assertEqual(json.loads((root/'tasks.gemini-raw.json').read_text()),[r['gemini'] for r in result['gemini_responses']])
            self.assertEqual(sorted(p.name for p in (root/'tasks.passes').glob('*')),['00-project-facts.json','01-aufgaben.json'])
            self.assertEqual(result,json.loads((root/'tasks.passes/01-aufgaben.json').read_text()))
            # Invalid handoff is rejected before constructing a provider or creating outputs.
            path.write_text('{"verpflichtungen": []}')
            argv[-1]=str(root/'invalid.json')
            with patch.object(sys,'argv',argv),patch('bauvo_working_units.cli.GeminiModel') as provider:
                with self.assertRaises(ValueError):main()
                provider.assert_not_called()
            self.assertFalse((root/'invalid.passes').exists())

    def test_fact_only_stage_produces_input_for_next_stage_without_task_calls(self):
        doc=document('contract','Contractor prepares plans.');calls=[]
        def extract(prompt):
            calls.append(prompt.split('.',1)[0]);return {'verpflichtungen':[proposal(doc)]}
        result=extract_project_units([doc],SimpleNamespace(extract=extract),stop_after='PAGE_PROJECT_FACTS')
        self.assertEqual(calls,['PAGE_PROJECT_FACTS'])
        self.assertEqual(validate_facts(result,[doc]),result)

    def test_legacy_schema_and_unknown_stages_cannot_silently_run_a_full_analysis(self):
        doc=document('contract','Original')
        for source,kwargs in [(doc,{'stop_after':'TYPO'}),(replace(doc,schema_version=5),{'project_facts':collection(doc)})]:
            with self.assertRaises(ValueError):
                extract_project_units([source],SimpleNamespace(extract=lambda p:self.fail('Paid request')),**kwargs)
