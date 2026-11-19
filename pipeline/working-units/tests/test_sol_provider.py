import copy,json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from bauvo_working_units.sol import SolModel, SolProviderError, request_for
from bauvo_working_units.gemini import IncompleteResponse
from bauvo_working_units.cache import CachedModel, provider_request
from bauvo_working_units.schemas import RESPONSE_SCHEMAS, generation_for, response_schema_for
from bauvo_working_units.spend_budget import SpendLedger, SpendLimitedModel, price, PRICE_POLICIES
from bauvo_working_units.trace import RawRunRecorder


class SolProviderTests(unittest.TestCase):
    prompt='PAGE_CONSEQUENCES. Fictional source.\nSOURCE DATA:\n'+json.dumps({'working_units':{'U1':{}}})

    def envelope(self, text='{"assessments": []}', status='completed', **extra):
        return {'model':'gpt-6.1-sol','id':'synthetic-response','status':status,
                'output':[{'type':'message','content':[{'type':'output_text','text':text}]}],
                'usage':{'input_tokens':100,'output_tokens':40,
                         'output_tokens_details':{'reasoning_tokens':30}},**extra}

    def backend(self,raw):
        client=Mock();client.responses.create.return_value.model_dump.return_value=copy.deepcopy(raw)
        return SolModel(client=client),client

    def test_medium_request_keeps_original_text_schema_and_disables_tools(self):
        raw=self.envelope();model,client=self.backend(raw)
        self.assertEqual(model.extract(self.prompt),{'assessments':[]})
        body=client.responses.create.call_args.kwargs
        self.assertEqual(body['input'],self.prompt)
        self.assertEqual(body['reasoning'],{'effort':'medium'})
        self.assertEqual(body['max_output_tokens'],65536)
        self.assertEqual(body['text']['format']['schema'],response_schema_for(self.prompt))
        self.assertTrue(body['text']['format']['strict']);self.assertEqual(body['tools'],[])
        self.assertFalse(body['store']);self.assertNotIn('temperature',body)
        self.assertEqual(model.last_diagnostics['provider_response'],raw)
        self.assertEqual(client.responses.create.call_count,1)

    def test_failed_incomplete_and_refused_answers_preserve_original_envelope(self):
        for raw,finish in [
            (self.envelope(status='incomplete',incomplete_details={'reason':'max_output_tokens'}),'MAX_TOKENS'),
            (self.envelope(status='failed'),'FAILED'),
            ({**self.envelope(), 'output':[{'type':'message','content':[{'type':'refusal','refusal':'Synthetic refusal'}]}]},'SAFETY')]:
            with self.subTest(finish=finish):
                model,client=self.backend(raw)
                with self.assertRaises(IncompleteResponse):model.extract(self.prompt)
                self.assertEqual(model.last_finish_reason,finish)
                self.assertEqual(model.last_diagnostics['provider_response'],raw)
                self.assertEqual(client.responses.create.call_count,1)

    def test_invalid_json_is_preserved_before_validation(self):
        model,_=self.backend(self.envelope(text='unfinished {'))
        with TemporaryDirectory() as tmp:
            raw=Path(tmp)/'raw.json';recorder=RawRunRecorder(model,raw)
            with self.assertRaises(IncompleteResponse):recorder.extract(self.prompt)
            self.assertEqual(json.loads(raw.read_text()),['unfinished {'])
            self.assertEqual(json.loads(raw.with_suffix('.attempts.json').read_text())[0]['diagnostics']['provider_response'],self.envelope(text='unfinished {'))

    def test_transport_failure_does_not_expose_message_or_credentials(self):
        model,client=self.backend({})
        client.responses.create.side_effect=RuntimeError('secret must not escape')
        with self.assertRaises(SolProviderError) as failure:model.extract(self.prompt)
        self.assertNotIn('secret',str(failure.exception))
        self.assertNotIn('secret',json.dumps(model.last_diagnostics))

    def test_sdk_transport_failure_uses_the_existing_bounded_outer_retry(self):
        from openai import APIConnectionError
        from bauvo_working_units.trace import RetryingModel, ModelRequestFailed
        from unittest.mock import patch
        model,client=self.backend({})
        client.responses.create.side_effect=APIConnectionError(request=Mock())
        with patch('bauvo_working_units.trace.time.sleep'), self.assertRaises(ModelRequestFailed):
            RetryingModel(model).extract(self.prompt)
        self.assertEqual(client.responses.create.call_count,2)
        self.assertIsNone(model.last_diagnostics['provider_error']['status_code'])

    def test_cache_identity_matches_actual_sol_generation_and_keeps_gemini_identity(self):
        identity={'provider':'sol','model':'gpt-6.1-sol','reasoning':'medium'}
        request=provider_request(self.prompt,identity)
        body=request_for(self.prompt)
        self.assertEqual(request['generation'],{k:body[k] for k in ('reasoning','max_output_tokens','service_tier','store','tools')})
        gemini=provider_request(self.prompt,{'project':'p','location':'global','model':'gemini-3.5-flash'})
        self.assertEqual(gemini['generation'],generation_for('PAGE_CONSEQUENCES'))
        self.assertEqual(set(gemini['identity']),{'project','location','model'})
        self.assertNotEqual(request,provider_request(self.prompt,{**identity,'reasoning':'low'}))
        self.assertNotEqual(request,gemini)

    def test_sol_cache_replay_preserves_full_response_without_provider_or_spending(self):
        with TemporaryDirectory() as tmp:
            raw=self.envelope();provider,client=self.backend(raw)
            ledger=SpendLedger(Path(tmp)/'spend.json','2','gpt-6.1-sol','openai')
            cached=CachedModel(SpendLimitedModel(lambda:provider,ledger),Path(tmp)/'cache',
                               {'provider':'sol','model':'gpt-6.1-sol','reasoning':'medium'})
            cached.extract(self.prompt);before=ledger.path.read_bytes()
            cached.model=SimpleNamespace(extract=lambda _:self.fail('No provider call on replay'))
            self.assertEqual(cached.extract(self.prompt),{'assessments':[]})
            self.assertEqual(cached.last_diagnostics['provider_response'],raw)
            self.assertEqual(ledger.path.read_bytes(),before)
            self.assertEqual(client.responses.create.call_count,1)

    def test_cost_counts_reasoning_once_and_uses_native_usage(self):
        with TemporaryDirectory() as tmp:
            ledger=SpendLedger(Path(tmp)/'spend.json','2','gpt-6.1-sol','openai')
            i=ledger.reserve(self.prompt)
            usage={'provider':'openai','input_tokens':1000,'output_tokens':2000,
                   'input_tokens_details':{'cache_write_tokens':1000},
                   'output_tokens_details':{'reasoning_tokens':1500}}
            ledger.settle(i,{'usage_metadata':usage},True)
            saved=json.loads(ledger.path.read_text())
            self.assertEqual(float(saved['accounted_upper_bound_usd']),.0225)
            self.assertEqual(saved['attempts'][0]['usage'],usage)
            policy=PRICE_POLICIES[('gpt-6.1-sol','openai')]
            self.assertEqual(float(price(272001,1000,policy)),1.375005)
