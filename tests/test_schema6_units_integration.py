import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'pipeline'),str(ROOT/'pipeline/working-units/src'),str(ROOT/'scripts')]
from bauvo_working_units.cli import main
from bauvo_working_units.core import digest
from bauvo_working_units.schemas import GENERATION
import bauvo

class Schema6CLIIntegrationTests(unittest.TestCase):
    def test_cli_source_to_output_with_cache_raw_trace_and_new_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);text='The owner supplies construction water.'
            pages=[{'pdf_page':1,'text':text,'text_sha256':hashlib.sha256(text.encode()).hexdigest(),'checkboxes':[]}]
            (root/'pages.json').write_text(json.dumps({'schema_version':6,'source':'house.pdf','page_count':1,'pages':pages}))
            (root/'documents.json').write_text(json.dumps({'schema_version':6,'pages_sha256':digest(pages),'status':'proposed',
                'documents':[{'document_id':'D1','document_title':'Contract','pdf_pages':[1],'uncertain':False}],
                'page_assignments':[{'pdf_page':1,'document_id':'D1'}]}))
            before=(root/'pages.json').read_bytes();calls=[];models=[]
            proposal={'name':'Supply construction water','dedupe_key':'water','actor':'Owner','action':'Supply water',
                      'citations':[{'pdf_page':1,'quote':text.replace('water.','waters.')}],'tasks':[],'depends_on':[]}
            class Fake:
                last_raw_response=None
                def __init__(self,project,location,model):models.append(model)
                def extract(self,prompt):
                    calls.append(prompt);data=json.loads(prompt.split('SOURCE DATA:\n')[1])
                    if prompt.startswith('PAGE_PROJECT_FACTS'):response={'verpflichtungen':[]}
                    elif prompt.startswith('PAGE_EXTRACTION'):response={'working_units':[proposal],'offers':[],'carry_forward':''}
                    elif prompt.startswith('PAGE_OFFERS'):response={'offers':[]}
                    elif prompt.startswith(('PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY')):
                        row={'unit_id':data['working_units'][0]['id'],'citations':[]}
                        if prompt.startswith('PAGE_APPLICABILITY'):
                            row.update(applicability='UNKNOWN',reason='Selection unavailable.',applicability_rules=[],missing_evidence=['House selection'])
                        elif prompt.startswith('PAGE_TIMING'):
                            row.update(status='NOT_STATED',timing_rules=[],missing_evidence=[])
                        else:row.update(status='NOT_STATED',consequences=[])
                        response={'assessments':[row]}
                    else:raise AssertionError('Unexpected pass: '+prompt.split('.',1)[0])
                    self.last_raw_response=json.dumps(response);return copy.deepcopy(response)
            for attempt in (1,2):
                argv=['units','--pages',str(root/'pages.json'),'--documents',str(root/'documents.json'),'--project','p',
                      '--output',str(root/f'output-{attempt}.json'),'--trace-dir',str(root/f'trace-{attempt}'),'--cache-dir',str(root/'cache')]
                with patch.object(sys,'argv',argv),patch('bauvo_working_units.cli.GeminiModel',Fake):main()
            self.assertEqual(models,['gemini-3.5-flash']*2);self.assertEqual(len(calls),6)
            self.assertEqual((root/'pages.json').read_bytes(),before)
            result=json.loads((root/'output-1.json').read_text());self.assertEqual(result['schema_version'],17)
            self.assertEqual(result['working_units'][0]['gemini'],proposal)
            self.assertEqual(result['status'],'needs_review')
            self.assertEqual(result['working_units'][0]['gemini']['citations'],proposal['citations'])
            self.assertEqual(result['citation_validation'],'page_and_quote_checks')
            self.assertEqual(len(json.loads((root/'output-1.gemini-raw.json').read_text())),6)
            self.assertEqual(len(list((root/'trace-1').glob('*-prompt.txt'))),6)
            for p in (root/'cache').glob('*.json'):
                identity=json.loads(p.read_text())['request']['identity'];self.assertEqual(identity['generation'],GENERATION)
                self.assertIn('response_schemas',identity)

    def test_cache_detects_modified_request_metadata_as_well_as_response_tampering(self):
        from bauvo_working_units.cache import CachedModel
        with tempfile.TemporaryDirectory() as tmp:
            backend=SimpleNamespace(extract=lambda prompt:{'working_units':[]},last_raw_response=None)
            cached=CachedModel(backend,tmp,{'model':'gemini-3.5-flash','generation':GENERATION})
            cached.extract('SOURCE')
            path=next(Path(tmp).glob('*.json'));saved=json.loads(path.read_text())
            saved['request']['identity']['model']='modified-model';path.write_text(json.dumps(saved))
            with self.assertRaisesRegex(ValueError,'cache modified'):cached.extract('SOURCE')

    def test_working_unit_default_is_independent_of_report_model(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(bauvo,'ROOT',Path(tmp)),patch.dict(os.environ,{},clear=True):
            (Path(tmp)/'.env').write_text('BAUVO_MODEL=legacy-report-model\n')
            config=bauvo.load_environment();self.assertEqual(config['working_unit_model'],'gemini-3.5-flash')
            self.assertEqual(config['model'],'legacy-report-model')

    def test_make_units_completes_schema6_without_entering_legacy_report_assembly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);case={'sources':[{'key':'contract'}],'contract_signed':'2025-01-01'}
            folder=root/'extraction/contract';folder.mkdir(parents=True)
            (folder/'pages.json').write_text('{"schema_version":6}')
            config={'project':'p','location':'global','working_unit_model':'gemini-3.5-flash','model':'legacy-report-model','thinking_budget':2048};commands=[]
            def command(args,log):
                commands.append(args);Path(args[args.index('--output')+1]).write_text('{"schema_version":12,"working_units":[]}')
            with patch('extraction_acceptance.require_approved'),patch.object(bauvo,'command',side_effect=command),patch.object(bauvo,'complete') as complete,patch.object(bauvo,'load_manifest') as legacy:
                bauvo.execute_stage('units',root,case,config)
                legacy.assert_not_called();complete.assert_called_once()
                self.assertEqual(complete.call_args.args[1],'units')
            self.assertEqual(commands[0][commands[0].index('--model')+1],'gemini-3.5-flash')
            self.assertTrue((root/'input-manifest.json').exists());self.assertFalse((root/'input-snapshot.json').exists())
            with self.assertRaisesRegex(ValueError,'report-stage migration'):bauvo.execute_stage('reconcile',root,case,config)

    def test_make_units_refuses_to_reuse_a_stale_schema6_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'extraction/contract';folder.mkdir(parents=True)
            (folder/'pages.json').write_text('{"schema_version":6}')
            output=root/'working-units/contract';output.mkdir(parents=True)
            target=output/'working-units.json';before='{"schema_version":10,"protocol":"page-working-units-v6"}'
            target.write_text(before)
            with patch('extraction_acceptance.require_approved'),patch.object(bauvo,'command') as command:
                with self.assertRaisesRegex(ValueError,'different schema/protocol'):
                    bauvo.execute_stage('units',root,{'sources':[{'key':'contract'}]}, {'project':'p','location':'global','model':'legacy-report','thinking_budget':2048})
                command.assert_not_called()
            self.assertEqual(target.read_text(),before)

    def test_make_units_does_not_mark_failed_fact_collection_as_complete_on_retry(self):
        from bauvo_working_units.schemas import PROTOCOL
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'extraction/contract';folder.mkdir(parents=True)
            (folder/'pages.json').write_text('{"schema_version":6}')
            output=root/'working-units/contract';output.mkdir(parents=True)
            target=output/'working-units.json'
            payload={'schema_version':17,'protocol':PROTOCOL,'status':'request_failed',
                     'project_facts':{'status':'request_failed','facts':[]}}
            target.write_text(json.dumps(payload));before=target.read_bytes()
            with patch('extraction_acceptance.require_approved'),patch.object(bauvo,'command') as command,patch.object(bauvo,'complete') as complete:
                with self.assertRaisesRegex(ValueError,'unresolved model requests'):
                    bauvo.execute_stage('units',root,{'sources':[{'key':'contract'}]},
                                        {'project':'p','location':'global','model':'legacy-report','thinking_budget':2048})
                command.assert_not_called();complete.assert_not_called()
            self.assertEqual(target.read_bytes(),before)

    def test_case_runner_marks_primary_and_context_blb_sources_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sources=[{'key':'contract'},{'key':'blb'}]
            for source in sources:
                folder=root/'extraction'/source['key'];folder.mkdir(parents=True)
                (folder/'pages.json').write_text('{"schema_version":6}')
            calls=[]
            def command(args,log):
                calls.append(args)
                Path(args[args.index('--output')+1]).write_text('{"working_units":[]}')
            config={'project':'p','location':'global','model':'report','working_unit_model':'gemini-3.5-flash','thinking_budget':2048}
            with patch('extraction_acceptance.require_approved'),patch.object(bauvo,'command',side_effect=command),patch.object(bauvo,'complete'):
                bauvo.execute_stage('units',root,{'sources':sources,'contract_signed':'2025-01-01'},config)
            self.assertEqual([args[args.index('--source-type')+1] for args in calls],['project','blb'])
            self.assertEqual([args[args.index('--context-type')+1] for args in calls],['blb','project'])
