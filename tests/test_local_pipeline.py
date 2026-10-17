import contextlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'pipeline'),str(ROOT/'pipeline/pdf-extractor'),str(ROOT/'pipeline/extraction_support')]
spec=importlib.util.spec_from_file_location('bauvo_cli',ROOT/'scripts/bauvo.py')
cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
from customer_report import fingerprint
from test_timing_assessment import sample
from timing_assessment import calculate
from document_review import validate_annotations

class LocalPipelineTests(unittest.TestCase):
    def test_case_paths_are_data_and_baseline_is_required(self):
        with tempfile.TemporaryDirectory() as d,patch.object(cli,'ROOT',Path(d)):
            root=Path(d);contract=root/'contract $(touch dangerous).pdf';contract.write_bytes(b'%PDF')
            blb=root/'blb.pdf';blb.write_bytes(b'%PDF')
            with patch.dict(os.environ,{'CONTRACT':str(contract),'BLB':str(blb),'SIGNED':'2025-07-03'}):cli.new_case('test')
            case=cli.load_case('test');self.assertEqual(case['sources'][0]['pdf'],str(contract.resolve()))
            self.assertFalse((root/'dangerous').exists())
            case['contract_signed_verified']=False;cli.write_json(cli.case_path('test'),case)
            with self.assertRaisesRegex(ValueError,'confirm'):cli.load_case('test')
            with self.assertRaises(ValueError):cli.case_path('../outside')

    def test_resume_checks_pdf_content_and_preserves_prior_run(self):
        with tempfile.TemporaryDirectory() as d,patch.object(cli,'ROOT',Path(d)):
            pdf=Path(d)/'a.pdf';pdf.write_bytes(b'one');case={'sources':[{'key':'contract','pdf':str(pdf)}]}
            first=cli.get_run('demo',case,{});self.assertEqual(first,cli.get_run('demo',case,{}))
            pdf.write_bytes(b'two')
            with self.assertRaisesRegex(ValueError,'changed'):cli.get_run('demo',case,{})
            second=cli.get_run('demo',case,{},True);self.assertNotEqual(first,second);self.assertTrue(first.exists())

    def test_modified_checkpoint_does_not_silently_pass(self):
        with tempfile.TemporaryDirectory() as d,patch.object(cli,'ROOT',Path(d)):
            run=Path(d);f=run/'result.json';f.write_text('{}');cli.complete(run,'extract',[f])
            self.assertTrue(cli.stage_done(run,'extract'));f.write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError,'changed'):cli.stage_done(run,'extract')

    def test_no_price_clause_keeps_start_targets_undated(self):
        p,q,a,r,put=sample();put(0,r('planned_start','BEFORE',{'unit':'weeks','min':12,'max':12}))
        put(1,r('contract_signed','AFTER',{'unit':'days','min':14,'max':14}))
        result=calculate(p,q,a,price_rule_id=None)
        self.assertIsNone(result['price_boundary']);self.assertFalse(result['units'][0]['candidates'])
        self.assertTrue(result['units'][1]['candidates'])

    def test_review_quote_coverage_and_optional_scope(self):
        p,q,_,_,_=sample();u=q['units'][0];cs=u['citations'];statement={'text':'Source-based statement','citations':cs}
        a={'id':u['id'],'category':'CONTRACT_SCOPE','reason':'Evidence','responsibility':statement,'performance':statement,'timing':[],'consequences':[],'qualifications':[]}
        self.assertEqual(validate_annotations([a],[u],p),[a])
        with self.assertRaises(ValueError):validate_annotations([], [u],p)
        u['kind']='OPTION'
        with self.assertRaisesRegex(ValueError,'Optional'):validate_annotations([a],[u],p)

    def test_env_file_is_not_shell_executed(self):
        with tempfile.TemporaryDirectory() as d,patch.object(cli,'ROOT',Path(d)),patch.dict(os.environ,{},clear=True):
            (Path(d)/'.env').write_text('GOOGLE_CLOUD_PROJECT=$(touch no)\n')
            self.assertEqual(cli.load_environment()['project'],'$(touch no)');self.assertFalse((Path(d)/'no').exists())

    def test_pdf_transcription_default_is_independent_of_analysis_model(self):
        with tempfile.TemporaryDirectory() as d,patch.object(cli,'ROOT',Path(d)),patch.dict(os.environ,{'BAUVO_MODEL':'gemini-2.5-flash'},clear=True):
            config=cli.load_environment()
            self.assertEqual(config['ocr_provider'],'gemini')
            self.assertEqual(config['ocr_model'],'gemini-3.5-flash')
            self.assertEqual(config['model'],'gemini-2.5-flash')
            (Path(d)/'.env').write_text('BAUVO_OCR_PROVIDER=google-document-ai\nBAUVO_OCR_MODEL=custom-model\n')
            config=cli.load_environment()
            self.assertEqual(config['ocr_provider'],'google-document-ai')
            self.assertEqual(config['ocr_model'],'custom-model')

    def test_auth_check_probes_default_transcription_model_with_minimal_reasoning(self):
        from unittest.mock import Mock
        client=Mock();client.models.generate_content.return_value.text='OK'
        with patch('google.auth.default',return_value=(object(),'p')),patch('google.genai.Client',return_value=client):
            cli.doctor({'project':'p','location':'global','model':'gemini-2.5-flash','ocr_provider':'gemini','ocr_model':'gemini-3.5-flash'},live=True)
        request=client.models.generate_content.call_args.kwargs
        self.assertEqual(request['model'],'gemini-3.5-flash')
        self.assertEqual(request['config']['thinking_config'],{'thinking_level':'MINIMAL'})

    def test_document_proposal_transport_sends_text_only_with_adc(self):
        import grouping_client as extract
        from types import SimpleNamespace
        seen={}
        class Client:
            def __init__(self,**kwargs):seen['client']=kwargs;self.models=self
            def generate_content(self,**kwargs):seen['request']=kwargs;return SimpleNamespace(text='{"pages":[]}',candidates=[SimpleNamespace(finish_reason='STOP')])
        with patch('google.genai.Client',Client):
            response=extract.gemini_sdk('p','global','m',[{'text':'Complete OCR page text'}])
        self.assertEqual(response['pages'],[]);self.assertTrue(seen['client']['vertexai'])
        self.assertEqual(seen['request']['contents'][0].text,'Complete OCR page text')
        self.assertIsNone(seen['request']['contents'][0].inline_data)
        from document_grouping import RESPONSE_SCHEMA
        self.assertEqual(seen['request']['config'].response_json_schema,RESPONSE_SCHEMA)
        self.assertFalse(RESPONSE_SCHEMA['additionalProperties'])

    def test_grouping_checkpoint_reuse_and_tamper_detection(self):
        import grouping_client as extract
        from types import SimpleNamespace
        args=SimpleNamespace(project='p',location='global',model='m');calls=[]
        def invoke():calls.append(1);return {'spans':[]}
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);parts=[{'text':'Grouping prompt with exact source lines'}]
            self.assertEqual(extract.cached_group_call(args,parts,folder,invoke),{'spans':[]})
            extract.cached_group_call(args,parts,folder,invoke);self.assertEqual(len(calls),1)
            path=next(folder.glob('*.json'));saved=cli.read(path);saved['response']={'changed':True};cli.write_json(path,saved)
            with self.assertRaisesRegex(ValueError,'modified'):extract.cached_group_call(args,parts,folder,invoke)
            args.model='another';extract.cached_group_call(args,parts,folder,invoke);self.assertEqual(len(calls),2)


    def test_price_selection_cannot_choose_unknown_rule(self):
        from price_anchor import select_price_rule
        p,q,_,_,_=sample();q['rules'][0].update(anchor='contract_signed',kind='PLANNING_TARGET',target='anchor:boundary',offset={'unit':'months','min':12,'max':12})
        class Model:
            def extract(self,_):return {'rule_id':'invented','reason':'bad'}
        def factory():return Model()
        factory.identity={'model':'fake'}
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'Unknown'):select_price_rule(p,q,factory,d)

class WorkingUnitRuntimeTests(unittest.TestCase):
    def test_completed_calls_resume_and_cache_tampering_is_rejected(self):
        from bauvo_working_units.cache import CachedModel
        calls=[]
        class Model:
            last_raw_response='{"working_units":[]}'
            def extract(self,prompt):calls.append(prompt);return {'working_units':[]}
        with tempfile.TemporaryDirectory() as d:
            cache=CachedModel(Model(),d,{'model':'test'})
            cache.extract('prompt');cache.extract('prompt');self.assertEqual(calls,['prompt'])
            path=next(Path(d).glob('*.json'));saved=cli.read(path);saved['raw_text']='changed';cli.write_json(path,saved)
            with self.assertRaisesRegex(ValueError,'modified'):cache.extract('prompt')
            CachedModel(Model(),d,{'model':'another'}).extract('prompt');self.assertEqual(len(calls),2)

    def test_rate_limit_retries_are_bounded_and_nonretryable_errors_propagate(self):
        from bauvo_working_units.gemini import GeminiModel
        from types import SimpleNamespace
        class Limit(Exception):code=429
        calls=[]
        def generate(**kwargs):
            calls.append(1)
            if len(calls)<3:raise Limit('capacity')
            return SimpleNamespace(text='{}',candidates=[SimpleNamespace(finish_reason='STOP')])
        model=GeminiModel.__new__(GeminiModel);model._model='test'
        model._client=SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        model._types=SimpleNamespace(GenerateContentConfig=lambda **kwargs:kwargs)
        with patch('bauvo_working_units.gemini.time.sleep') as sleep:
            self.assertEqual(model.extract('prompt'),{});self.assertEqual([x.args[0] for x in sleep.call_args_list],[15,30])
        model._client.models.generate_content=lambda **kwargs:(_ for _ in ()).throw(Limit('capacity'))
        with patch('bauvo_working_units.gemini.time.sleep') as sleep:
            with self.assertRaises(Limit):model.extract('prompt')
            self.assertEqual(sleep.call_count,4)
        model._client.models.generate_content=lambda **kwargs:SimpleNamespace(text='{}',candidates=[SimpleNamespace(finish_reason='MAX_TOKENS')])
        with self.assertRaisesRegex(ValueError,'Incomplete'):model.extract('prompt')

    def test_stage_fingerprint_ignores_unrelated_stage_edits(self):
        from cache_binding import stage_binding
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);file=root/'pipeline/pdf-extractor/ocr_pipeline.py';file.parent.mkdir(parents=True);file.write_text('pdf extraction')
            initial=stage_binding(root,'extract');other=root/'pipeline/timing_assessment.py';other.write_text('timing change')
            self.assertEqual(stage_binding(root,'extract'),initial)
            file.write_text('new extraction');self.assertNotEqual(stage_binding(root,'extract'),initial)

class BatchResponseFormatTests(unittest.TestCase):
    def test_detail_schema_is_sent_to_vertex_and_keeps_raw_evidence(self):
        from batched_report import call_saved
        from enrich_working_units import GeminiModel
        from report_schema import batch_schema
        schema=batch_schema();seen=[]
        def factory():
            model=GeminiModel.__new__(GeminiModel)
            def extract(prompt):
                seen.append(model.response_json_schema)
                return {'units':[],'unresolved_questions':[]}
            model.extract=extract
            return model
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'attempt.json'
            call_saved(factory,'data',path,schema)
            self.assertEqual(seen,[schema]);self.assertTrue(path.exists())
            self.assertEqual(cli.read(path.with_suffix('.schema.json')),schema)
        edge=schema['properties']['units']['items']['properties']['requirements']['items']['properties']['members']['items']
        self.assertEqual(edge['properties']['lag_days']['properties']['min'],{'type':'integer','minimum':0})

    def test_unknown_dependency_diagnostic_names_rejected_id(self):
        from batched_report import validate_batch
        from test_batched_report import fixture,canonical_registry
        payload=fixture()[1];registry=canonical_registry(payload)
        from test_batched_report import Factory
        factory=Factory(payload)
        # Reuse the valid fixture response, then introduce an invented milestone.
        from batched_report import batch_context,BATCH_PROMPT
        response=factory().extract(BATCH_PROMPT+'\nDATA:\n'+json.dumps(batch_context(payload,registry,registry)))
        response['units'][0]['requirements']=[{'mode':'ALL','members':[{'unit_id':'invented_milestone'}]}]
        with self.assertRaisesRegex(ValueError,'invented_milestone'):
            validate_batch(response,registry,registry,payload)


if __name__=='__main__':unittest.main()

class PageOrchestrationTests(unittest.TestCase):
    def test_extraction_checkpoints_only_page_and_document_artifacts(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp,patch.object(cli,'ROOT',Path(tmp)):
            root=Path(tmp);run=root/'runs/sample/new';run.mkdir(parents=True)
            case={'sources':[{'key':'contract','pdf':'contract.pdf'}]}
            config={'project':'p','location':'global','model':'m','ocr_processor':'pinned','ocr_min_confidence':.98,'thinking_budget':0,'document_boundaries':'off'}
            calls=[]
            def command(args,log):
                calls.append(args)
                out=Path(args[args.index('--output')+1]);out.mkdir(parents=True,exist_ok=True)
                if Path(args[0]).name=='ocr_pipeline.py':
                    self.assertNotIn('--document-boundaries',args)
                    self.assertNotIn('--document-boundary-model',args)
                    self.assertNotIn('--model',args)
                    self.assertEqual(args[args.index('--ocr-provider')+1],'gemini')
                    self.assertEqual(args[args.index('--ocr-model')+1],'gemini-3.5-flash')
                    names=('page-images.json','pages.json')
                else:
                    self.assertEqual(Path(args[0]).name,'document_pipeline.py')
                    self.assertEqual(Path(args[args.index('--input')+1]),out/'pages.json')
                    self.assertTrue((out/'pages.json').exists())
                    self.assertEqual(args[args.index('--document-boundary-model')+1],'m')
                    self.assertEqual(args[args.index('--document-boundaries')+1],'off')
                    names=('documents.json','quality-report.json','duplicate-pages.json')
                    (out/'source-review.html').write_text('review')
                for name in names:(out/name).write_text('{}')
            with patch.object(cli,'command',command):cli.execute_stage('extract',run,case,config)
            self.assertEqual([Path(c[0]).name for c in calls],['ocr_pipeline.py','document_pipeline.py'])
            checkpoint=cli.read(run/'extract.done.json');self.assertTrue(any(p.endswith('/pages.json') for p in checkpoint['files']))
            self.assertFalse(any('sections.json' in p or 'blocks.json' in p or 'lines.json' in p for p in checkpoint['files']))


class ProductionOCRProviderTests(unittest.TestCase):
    def test_non_gemini_provider_is_rejected_before_launching_any_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            config={'project':'p','location':'global','model':'m','thinking_budget':0,
                    'ocr_provider':'google-document-ai'}
            case={'sources':[{'key':'contract','pdf':'contract.pdf'}]}
            with patch.object(cli,'command') as command:
                with self.assertRaisesRegex(ValueError,'Production extraction uses Gemini'):
                    cli.execute_stage('extract',Path(tmp),case,config)
                command.assert_not_called()
