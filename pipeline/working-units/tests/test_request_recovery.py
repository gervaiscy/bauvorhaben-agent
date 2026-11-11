"""Recovery exercises real recorder/checkpoint boundaries with an offline provider."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from bauvo_working_units.core import digest
from bauvo_working_units.gemini import IncompleteResponse
from bauvo_working_units.recovery import (POLICY, RequestRecovery, generation_settings,
                                         resume_compatibility)
from bauvo_working_units.schemas import GENERATION
from bauvo_working_units.trace import RawRunRecorder, TracingModel
from bauvo_working_units import pipeline
from test_modular_pipeline import FakeModel
import test_modular_pipeline as modular

IDENTITY={'project':'unused','location':'global','model':'gemini-3.5-flash'}
PROMPT='PAGE_EXTRACTION. source window'


class Provider:
    def __init__(self):
        self.calls=[]
        self.last_raw_response=None
        self.last_finish_reason=None

    def extract(self, prompt):
        self.calls.append(prompt)
        value={'working_units':[], 'carry_forward':'original continuation'}
        self.last_raw_response=json.dumps(value)
        self.last_finish_reason='STOP'
        return value


class RequestRecoveryTests(unittest.TestCase):
    def test_exact_requests_replay_under_higher_limit_and_keep_raw_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);provider=Provider();old=root/'attempt-01'
            with patch.dict(GENERATION,{'max_output_tokens':16384}):
                cache=RequestRecovery(provider,old,[],IDENTITY)
                TracingModel(RawRunRecorder(cache,old/'raw.json'),old/'trace').extract(PROMPT)
            originals={p:p.read_bytes() for p in old.rglob('*') if p.is_file()}
            new=root/'attempt-02';cache=RequestRecovery(provider,new,[old],IDENTITY)
            recorder=RawRunRecorder(cache,new/'raw.json')
            answer=recorder.extract(PROMPT)
            self.assertEqual(answer,{'working_units':[], 'carry_forward':'original continuation'})
            self.assertEqual(len(provider.calls),1)
            self.assertEqual(cache.reused,1)
            provenance=json.loads((new/'raw.attempts.json').read_text())[0]['recovery']
            self.assertEqual(provenance['generation']['max_output_tokens'],16384)
            self.assertEqual(originals,{p:p.read_bytes() for p in old.rglob('*') if p.is_file()})
            cache.extract(PROMPT+' changed preceding context')
            self.assertEqual(len(provider.calls),2)

    def test_atomic_checkpoint_survives_interruption_before_trace_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);provider=Provider()
            old=root/'attempt-01';cache=RequestRecovery(provider,old,[],IDENTITY)
            cache.extract(PROMPT)
            # No aggregate output or trace was written yet.
            self.assertFalse((old/'result.json').exists())
            trace=old/'trace';trace.mkdir()
            incomplete=trace/'0001-attempt.json';incomplete.write_text('{')
            recovered=RequestRecovery(provider,root/'attempt-02',[old],IDENTITY)
            recovered.extract(PROMPT)
            self.assertEqual(len(provider.calls),1)
            self.assertEqual(incomplete.read_text(),'{')

    def test_completed_null_is_reused_but_failed_unfinished_attempt_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);trace=root/'old/trace';trace.mkdir(parents=True)
            for n,status,finish,raw in ((1,'request_failed','MAX_TOKENS','{"unfinished'),
                                       (2,'recorded','STOP','null')):
                stem=f'{n:04d}'
                prompt=PROMPT if n==2 else PROMPT+' unfinished'
                (trace/(stem+'-prompt.txt')).write_text(prompt)
                (trace/(stem+'-response.json')).write_text(raw)
                (trace/(stem+'-attempt.json')).write_text(json.dumps({
                    'status':status,'finish_reason':finish,
                    'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
                    'response_sha256':hashlib.sha256(raw.encode()).hexdigest()}))
            provider=Provider()
            cache=RequestRecovery(provider,root/'new',[root/'old'],IDENTITY,generation_settings())
            self.assertIsNone(cache.extract(PROMPT))
            self.assertEqual(provider.calls,[])
            self.assertEqual(cache.reused,1)
            cache.extract(PROMPT+' unfinished')
            self.assertEqual(provider.calls,[PROMPT+' unfinished'])
            self.assertEqual(cache.reused,1)

    def test_checkpoint_and_trace_tampering_stop_before_provider_requests(self):
        for target in ('checkpoint','response','prompt'):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);provider=Provider();old=root/'old'
                cache=RequestRecovery(provider,old,[],IDENTITY)
                TracingModel(RawRunRecorder(cache,old/'raw.json'),old/'trace').extract(PROMPT)
                if target=='checkpoint':
                    path=next((old/'requests').glob('*.json'))
                    value=json.loads(path.read_text());value['response']='{}';path.write_text(json.dumps(value))
                else:
                    path=old/'trace'/('0001-response.json' if target=='response' else '0001-prompt.txt')
                    path.write_text('{}' if target=='response' else 'modified source')
                before=path.read_bytes()
                with self.assertRaisesRegex(ValueError,'checksum mismatch'):
                    RequestRecovery(provider,root/'new',[old],IDENTITY)
                self.assertEqual(len(provider.calls),1)
                self.assertEqual(path.read_bytes(),before)

    def test_recovery_rejects_changed_identity_and_does_not_replay_different_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);provider=Provider();old=root/'old'
            RequestRecovery(provider,old,[],IDENTITY).extract(PROMPT)
            with self.assertRaisesRegex(ValueError,'model/project settings changed'):
                RequestRecovery(provider,root/'other',[old],{**IDENTITY,'model':'other'})
            with patch.dict(GENERATION,{'max_output_tokens':16384}):
                cache=RequestRecovery(provider,root/'lower',[old],IDENTITY)
                cache.extract(PROMPT)
            self.assertEqual(len(provider.calls),2)

    def test_compatibility_rejects_model_source_prompt_and_reasoning_changes(self):
        current={'code':'new','model':'gemini-3.5-flash','sources':['fixed'],
                 'phase_thinking_levels':{'PAGE_EXTRACTION':'MEDIUM'},
                 'recovery':{'code':'fixed','generation':generation_settings()}}
        previous=copy.deepcopy(current);previous['code']='old'
        for settings in previous['recovery']['generation'].values():settings['max_output_tokens']=16384
        self.assertIsNotNone(resume_compatibility(previous,current))
        for change in ('model','sources','thinking','code','temperature','decrease'):
            changed=copy.deepcopy(current)
            if change=='model':changed['model']='other'
            elif change=='sources':changed['sources']=['changed']
            elif change=='thinking':changed['phase_thinking_levels']['PAGE_EXTRACTION']='LOW'
            elif change=='code':changed['recovery']['code']='changed prompt/code'
            elif change=='temperature':changed['recovery']['generation']['PAGE_EXTRACTION']['temperature']=1
            else:changed['recovery']['generation']['PAGE_EXTRACTION']['max_output_tokens']=100
            with self.subTest(change=change):self.assertIsNone(resume_compatibility(previous,changed))


class RecoveringProvider(FakeModel):
    def __init__(self, failing_source, fail=True, interrupt=False):
        super().__init__()
        self.failing_source=failing_source;self.fail=fail;self.interrupt=interrupt
        self.last_raw_response=None;self.last_finish_reason=None

    def extract(self,prompt):
        self.last_raw_response=None;self.last_finish_reason=None
        value=super().extract(prompt)
        kind,data=self.calls[-1]
        if (self.fail and kind=='PAGE_EXTRACTION' and data['source_key']==self.failing_source
                and data['source_pages'][0]['pdf_page']==10):
            if self.interrupt:raise KeyboardInterrupt()
            self.last_raw_response='{"working_units":[';self.last_finish_reason='MAX_TOKENS'
            raise IncompleteResponse('unfinished_generation','MAX_TOKENS')
        self.last_raw_response=json.dumps(value);self.last_finish_reason='STOP'
        return value


class PipelineRecoveryTests(unittest.TestCase):
    # Reuse only the fixture helpers, not the inherited test cases.
    setUp=modular.ModularPipelineTests.setUp
    command=modular.ModularPipelineTests.command
    run_pipeline=modular.ModularPipelineTests.run_pipeline

    def prepare(self, legacy=False, interrupt=False):
        self.page_count=21
        original_command=self.command
        def split_documents(argv):
            original_command(argv)
            if Path(argv[1]).name=='document_pipeline.py':
                path=Path(argv[argv.index('--output')+1])/'documents.json'
                value=pipeline.read(path)
                value['documents']=[{'document_id':'D1','document_title':'Main',
                    'pdf_pages':list(range(1,21)),'uncertain':False},
                    {'document_id':'D2','document_title':'Separate', 'pdf_pages':[21],'uncertain':False}]
                value['page_assignments']=[{'pdf_page':n,'document_id':'D1' if n<=20 else 'D2'}
                                           for n in range(1,22)]
                pipeline.write(path,value)
        self.command=split_documents
        source=digest({'source':str(self.blb.resolve()),'pdf_sha256':pipeline.sha(self.blb)})[:16]
        provider=RecoveringProvider(source,interrupt=interrupt)
        with patch.dict(GENERATION,{'max_output_tokens':16384}):
            with self.assertRaises(KeyboardInterrupt if interrupt else RuntimeError):self.run_pipeline(provider)
        manifest=pipeline.read(self.output/'pipeline.json')
        if legacy:
            manifest['binding'].pop('recovery')
            manifest['binding']['code']=next(k for k,v in pipeline.read(POLICY)['legacy_code_limits'].items() if v==16384)
            pipeline.write(self.output/'pipeline.json',manifest)
            for path in self.output.rglob('request-settings.json'):path.unlink()
            for path in self.output.rglob('requests'):
                if path.is_dir():shutil.rmtree(path)
        return provider

    def test_legacy_failed_run_reuses_completed_stages_and_matching_windows_then_finishes(self):
        provider=self.prepare(legacy=True)
        originals={p:p.read_bytes() for p in self.output.rglob('*') if p.is_file() and p.name!='pipeline.json'}
        provider.fail=False;provider.calls.clear();self.commands.clear()
        # This synthetic run uses current prompts. Its fixture policy permits only
        # the simulated generation-ceiling migration, not real historical prompts.
        policy={**pipeline.read(POLICY), 'target_compatible_code':pipeline.compatible_code(pipeline.ROOT)}
        def read_fixture(path):
            return policy if Path(path)==POLICY else json.loads(Path(path).read_text(encoding='utf-8'))
        with patch('bauvo_working_units.recovery.read',side_effect=read_fixture):
            result=self.run_pipeline(provider,resume=True)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(self.commands,[])
        task_calls=[data for kind,data in provider.calls if kind=='PAGE_EXTRACTION']
        # Retry the missing window, then the next window whose accumulated context changed.
        self.assertEqual([[p['pdf_page'] for p in d['source_pages']] for d in task_calls],
                         [list(range(10,20)),[19,20]])
        self.assertFalse(any(kind=='PAGE_PROJECT_FACTS' for kind,data in provider.calls))
        self.assertEqual(originals,{p:p.read_bytes() for p in originals})
        self.assertEqual(result['steps']['04-aufgaben/blb-01']['attempt'],2)
        self.assertEqual(result['steps']['04-aufgaben/contract-01']['attempt'],1)
        self.assertEqual(result['steps']['04-aufgaben/blb-01']['history'][0]['status'],'failed')
        attempts=pipeline.read(self.output/'04-aufgaben/blb-01/attempt-02/raw.attempts.json')
        self.assertEqual(sum('recovery' in a for a in attempts),2)
        self.assertTrue(all(a['recovery']['generation']['max_output_tokens']==16384
                            for a in attempts if 'recovery' in a))
        self.assertTrue(any(kind=='PAGE_APPLICABILITY' for kind,data in provider.calls))

    def test_real_legacy_migration_does_not_accept_changed_consequence_prompts(self):
        provider=self.prepare(legacy=True)
        provider.fail=False;provider.calls.clear()
        with self.assertRaisesRegex(ValueError,'inputs/configuration/code changed'):
            self.run_pipeline(provider,resume=True)
        self.assertEqual(provider.calls,[])

    def test_interrupted_stage_recovers_without_final_result_or_raw_response_trace(self):
        provider=self.prepare(interrupt=True)
        folder=self.output/'04-aufgaben/blb-01/attempt-01'
        self.assertFalse((folder/'result.json').exists())
        # The atomic request checkpoint is sufficient even if tracing was interrupted.
        (folder/'trace/0001-response.json').unlink()
        provider.fail=False;provider.calls.clear();self.commands.clear()
        result=self.run_pipeline(provider,resume=True)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(self.commands,[])
        calls=[d for kind,d in provider.calls if kind=='PAGE_EXTRACTION']
        self.assertTrue(calls)
        self.assertTrue(all(d['source_pages'][0]['pdf_page']!=1 for d in calls))
        self.assertEqual(result['steps']['04-aufgaben/blb-01']['history'][0]['status'],'running')

    def test_legacy_unknown_code_or_changed_artifact_rejected_before_calls(self):
        provider=self.prepare(legacy=True);provider.fail=False;provider.calls.clear()
        path=self.output/'pipeline.json';manifest=pipeline.read(path)
        manifest['binding']['code']='unrecognized old code';pipeline.write(path,manifest)
        with self.assertRaisesRegex(ValueError,'inputs/configuration/code changed'):
            self.run_pipeline(provider,resume=True)
        self.assertEqual(provider.calls,[])

    def test_completed_artifact_tampering_is_rejected_before_resuming_failed_work(self):
        provider=self.prepare(legacy=True);provider.fail=False;provider.calls.clear()
        manifest=pipeline.read(self.output/'pipeline.json')
        path=self.output/manifest['steps']['04-aufgaben/contract-01']['artifact'];path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'Saved phase output changed'):
            self.run_pipeline(provider,resume=True)
        self.assertEqual(provider.calls,[])
