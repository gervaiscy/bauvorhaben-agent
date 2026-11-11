"""Preserve provider token telemetry through failures, caches and recovery."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from google.genai import types

from bauvo_working_units.cache import CachedModel
from bauvo_working_units.gemini import GeminiModel
from bauvo_working_units.recovery import RequestRecovery
from bauvo_working_units.schemas import RESPONSE_SCHEMAS, generation_for
from bauvo_working_units.trace import (RawRunRecorder, TracingModel, RetryingModel,
                                      ModelRequestFailed, RequestCheckpoints)


PROMPT='PAGE_TIMING. private source content must not appear in diagnostics'
IDENTITY={'project':'unused','location':'global','model':'gemini-3.5-flash'}


def adapter(generate):
    model=GeminiModel.__new__(GeminiModel)
    model._client=SimpleNamespace(models=SimpleNamespace(generate_content=generate))
    model._model=IDENTITY['model'];model._types=types
    return model


def response(text='{"assessments":[]}',finish='STOP',identifier='response-1',usage=True):
    return SimpleNamespace(text=text,candidates=[SimpleNamespace(finish_reason=finish)],
        model_version='gemini-3.5-flash-001',response_id=identifier,
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=12500,candidates_token_count=1700,thoughts_token_count=63836,
            total_token_count=78036,cached_content_token_count=0) if usage else None)


class RequestDiagnosticsTests(unittest.TestCase):
    def test_identical_retry_preserves_settings_usage_and_each_provider_response_id(self):
        calls=[];raw='{"verpflichtungen":['
        def generate(**kwargs):
            calls.append(kwargs)
            return response(raw,'MAX_TOKENS',f'response-{len(calls)}')
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            cache=CachedModel(adapter(generate),root/'cache',IDENTITY)
            model=RetryingModel(TracingModel(RawRunRecorder(cache,root/'raw.json'),root/'trace'))
            prompt='PAGE_PROJECT_FACTS. private source content'
            with self.assertRaises(ModelRequestFailed):model.extract(prompt)
            self.assertEqual(len(calls),2)
            self.assertEqual(calls[0]['config'],calls[1]['config'])
            self.assertEqual(calls[0]['contents'],calls[1]['contents'])
            self.assertEqual(json.loads((root/'raw.json').read_text()),[raw,raw])
            attempts=json.loads((root/'raw.attempts.json').read_text())
            for number,attempt in enumerate(attempts,1):
                diagnostic=attempt['diagnostics']
                self.assertEqual(attempt['finish_reason'],'MAX_TOKENS')
                self.assertEqual(diagnostic['requested_model'],'gemini-3.5-flash')
                self.assertEqual(diagnostic['model_version'],'gemini-3.5-flash-001')
                self.assertEqual(diagnostic['response_id'],f'response-{number}')
                self.assertEqual(diagnostic['generation_config'],
                                 calls[number-1]['config'].model_dump(mode='json',exclude_none=True))
                self.assertEqual(diagnostic['generation_config']['max_output_tokens'],65536)
                self.assertEqual(diagnostic['generation_config']['thinking_config']['thinking_level'],'MEDIUM')
                self.assertEqual(diagnostic['generation_config']['response_json_schema'],RESPONSE_SCHEMAS['PAGE_PROJECT_FACTS'])
                self.assertEqual(diagnostic['usage_metadata']['candidates_token_count'],1700)
                self.assertEqual(diagnostic['usage_metadata']['thoughts_token_count'],63836)
                self.assertEqual(diagnostic['usage_metadata']['prompt_token_count'],12500)
                self.assertEqual(diagnostic['usage_metadata']['total_token_count'],78036)
                self.assertEqual(diagnostic['usage_metadata']['cached_content_token_count'],0)
                self.assertNotIn('private source content',json.dumps(diagnostic))
                self.assertEqual(json.loads((root/f'trace/{number:04d}-attempt.json').read_text()),attempt)
                self.assertEqual((root/f'trace/{number:04d}-response.json').read_text(),raw)
            self.assertEqual(list((root/'cache').glob('*.json')),[])

    def test_transport_failure_keeps_request_settings_without_stale_response_usage(self):
        calls=[]
        def generate(**kwargs):
            calls.append(kwargs)
            if len(calls)==2:raise TimeoutError('connection timed out')
            return response()
        with TemporaryDirectory() as tmp:
            root=Path(tmp);model=RawRunRecorder(CachedModel(adapter(generate),root/'cache',IDENTITY),root/'raw.json')
            model.extract(PROMPT)
            with self.assertRaises(TimeoutError):model.extract(PROMPT+' changed')
            attempts=json.loads((root/'raw.attempts.json').read_text())
            diagnostic=attempts[1]['diagnostics']
            self.assertEqual(diagnostic['requested_model'],IDENTITY['model'])
            self.assertEqual(diagnostic['generation_config']['max_output_tokens'],65536)
            self.assertIsNone(diagnostic['model_version'])
            self.assertIsNone(diagnostic['response_id'])
            self.assertIsNone(diagnostic['usage_metadata'])
            self.assertIsNone(attempts[1]['finish_reason'])
            self.assertIsNone(attempts[1]['raw_index'])
            self.assertEqual(len(json.loads((root/'raw.json').read_text())),1)

    def test_cache_replay_preserves_original_diagnostics_without_a_provider_call(self):
        calls=[]
        def generate(**kwargs):calls.append(kwargs);return response()
        with TemporaryDirectory() as tmp:
            root=Path(tmp);cache=CachedModel(adapter(generate),root/'cache',IDENTITY)
            recorder=RawRunRecorder(cache,root/'raw.json')
            recorder.extract(PROMPT);first=copy.deepcopy(recorder.last_attempt['diagnostics'])
            saved={p:p.read_bytes() for p in (root/'cache').glob('*.json')}
            recorder.extract(PROMPT)
            self.assertEqual(len(calls),1)
            self.assertEqual(recorder.last_attempt['diagnostics'],first)
            recorder.last_attempt['diagnostics']['usage_metadata']['thoughts_token_count']=0
            recorder.extract(PROMPT)
            self.assertEqual(recorder.last_attempt['diagnostics'],first)
            self.assertEqual(saved,{p:p.read_bytes() for p in saved})

    def test_missing_provider_metadata_is_recorded_as_unknown_instead_of_invented_counts(self):
        sdk_response=SimpleNamespace(text='{}',candidates=[SimpleNamespace(finish_reason='STOP')])
        model=adapter(lambda **kwargs:sdk_response)
        self.assertEqual(model.extract(PROMPT),{})
        diagnostic=model.last_diagnostics
        self.assertIsNone(diagnostic['model_version'])
        self.assertIsNone(diagnostic['response_id'])
        self.assertIsNone(diagnostic['usage_metadata'])
        self.assertEqual(diagnostic['generation_config']['max_output_tokens'],generation_for('PAGE_TIMING')['max_output_tokens'])

    def test_atomic_analysis_recovery_retains_original_telemetry(self):
        calls=[]
        def generate(**kwargs):calls.append(kwargs);return response()
        with TemporaryDirectory() as tmp:
            root=Path(tmp);provider=adapter(generate)
            old=RawRunRecorder(RequestRecovery(provider,root/'old',[],IDENTITY),root/'old/raw.json')
            old.extract(PROMPT);diagnostic=copy.deepcopy(old.last_attempt['diagnostics'])
            saved={p:p.read_bytes() for p in (root/'old').rglob('*') if p.is_file()}
            cache=RequestRecovery(provider,root/'new',[root/'old'],IDENTITY)
            new=RawRunRecorder(cache,root/'new/raw.json');new.extract(PROMPT)
            self.assertEqual(len(calls),1)
            self.assertEqual(cache.reused,1)
            self.assertEqual(new.last_attempt['diagnostics'],diagnostic)
            self.assertEqual(saved,{p:p.read_bytes() for p in saved})

    def test_timing_checkpoint_recovery_retains_original_telemetry(self):
        calls=[]
        def generate(**kwargs):calls.append(kwargs);return response()
        with TemporaryDirectory() as tmp:
            root=Path(tmp);provider=adapter(generate)
            old=RequestCheckpoints(provider,root/'old/checkpoints',IDENTITY)
            old.extract(PROMPT);diagnostic=copy.deepcopy(old.last_diagnostics)
            new=RequestCheckpoints(provider,root/'new/checkpoints',IDENTITY,resume_from=root/'old/checkpoints')
            new.extract(PROMPT)
            self.assertEqual(len(calls),1)
            self.assertEqual(new.last_diagnostics,diagnostic)

    def test_changed_cached_diagnostics_are_rejected(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);cache=CachedModel(adapter(lambda **kwargs:response()),root/'cache',IDENTITY)
            cache.extract(PROMPT)
            path=next((root/'cache').glob('*.json'));saved=json.loads(path.read_text())
            saved['diagnostics']['usage_metadata']['thoughts_token_count']=0
            path.write_text(json.dumps(saved))
            with self.assertRaisesRegex(ValueError,'diagnostics modified'):cache.extract(PROMPT)
