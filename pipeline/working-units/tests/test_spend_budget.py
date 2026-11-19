import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from bauvo_working_units.spend_budget import SpendLedger, SpendLimitedModel, CostLimitExceeded, price
from bauvo_working_units.cache import CachedModel


class SpendBudgetTests(unittest.TestCase):
    prompt = 'PAGE_CONSEQUENCES. Small fictional request.\nSOURCE DATA:\n'+json.dumps({'working_units':{'U1':{}}})

    def test_too_small_limit_blocks_before_client_construction(self):
        with TemporaryDirectory() as tmp:
            model = SpendLimitedModel(lambda: self.fail('No client allowed'), SpendLedger(Path(tmp)/'spend.json', '.01'))
            with self.assertRaises(CostLimitExceeded): model.extract(self.prompt)
            self.assertEqual(json.loads((Path(tmp)/'spend.json').read_text())['attempts'], [])

    def test_output_and_reasoning_both_count_and_unused_reservation_is_released(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'spend.json'; ledger = SpendLedger(path, '1')
            backend = SimpleNamespace(last_raw_response='{}', last_finish_reason='STOP',
                last_diagnostics={'usage_metadata': {'prompt_token_count':100, 'candidates_token_count':20,
                                                    'thoughts_token_count':30}}, extract=lambda _: {})
            model = SpendLimitedModel(lambda: backend, ledger)
            for _ in range(3): self.assertEqual(model.extract(self.prompt), {})
            state = json.loads(path.read_text())
            self.assertEqual(len(state['attempts']), 3)
            self.assertEqual(float(state['accounted_upper_bound_usd']), float(price(300,150)))
            self.assertEqual(model.last_raw_response, '{}')

    def test_unknown_failure_keeps_reservation_across_restart(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'spend.json'
            def failure(_): raise TimeoutError()
            model = SpendLimitedModel(lambda: SimpleNamespace(extract=failure), SpendLedger(path, '1'))
            with self.assertRaises(TimeoutError): model.extract(self.prompt)
            ledger = SpendLedger(path, '1')
            with self.assertRaises(CostLimitExceeded): ledger.reserve(self.prompt)
            state = json.loads(path.read_text())
            self.assertFalse(state['attempts'][0]['usage_complete'])
            self.assertGreater(float(state['accounted_upper_bound_usd']), .5)

    def test_failed_but_reported_usage_is_also_charged(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'spend.json'
            ledger = SpendLedger(path, '1'); index = ledger.reserve(self.prompt)
            ledger.settle(index, {'usage_metadata': {'prompt_token_count': 10, 'candidates_token_count': 5,
                                                     'thoughts_token_count': 100}}, False)
            state = json.loads(path.read_text())
            self.assertEqual(float(state['accounted_upper_bound_usd']), float(price(10,105)))
            self.assertEqual(state['attempts'][0]['status'], 'failed')

    def test_missing_reasoning_is_charged_from_reported_total_or_remains_reserved(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'spend.json';ledger=SpendLedger(path,'3')
            index=ledger.reserve(self.prompt)
            ledger.settle(index,{'usage_metadata':{'prompt_token_count':100,'candidates_token_count':20,
                                                 'total_token_count':170}},True)
            state=json.loads(path.read_text())
            self.assertEqual(float(state['attempts'][index]['accounted_usd']),float(price(100,70)))
            index=ledger.reserve(self.prompt)
            ledger.settle(index,{'usage_metadata':{'prompt_token_count':100,'candidates_token_count':20}},True)
            state=json.loads(path.read_text());row=state['attempts'][index]
            self.assertFalse(row['usage_complete']);self.assertNotIn('accounted_usd',row)

    def test_cache_replay_does_not_reserve_or_contact_provider(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'spend.json'; ledger = SpendLedger(path, '1')
            backend = SimpleNamespace(extract=lambda _: {}, last_raw_response='{}', last_finish_reason='STOP')
            model = CachedModel(SpendLimitedModel(lambda:backend,ledger), Path(tmp)/'cache', {'model':'fixture'})
            model.extract(self.prompt)
            before = path.read_bytes()
            model.model = SimpleNamespace(extract=lambda _: self.fail('No paid replay'))
            model.extract(self.prompt)
            self.assertEqual(path.read_bytes(), before)

    def test_limits_prices_and_location_cannot_silently_change(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'spend.json'; SpendLedger(path, '1')
            for value in ('2','NaN','0','-1'):
                with self.assertRaises(ValueError): SpendLedger(path, value)
            with self.assertRaises(ValueError): SpendLedger(path,'1',location='other')
            with self.assertRaises(ValueError): SpendLedger(path,'1',model='unpriced')

    def test_pro_long_context_threshold_prices_the_entire_request_including_thinking(self):
        from bauvo_working_units.spend_budget import PRICE_POLICIES
        policy=PRICE_POLICIES[('gemini-3.1-pro-preview','global')]
        self.assertEqual(float(price(200000,1000,policy)),.412)
        self.assertEqual(float(price(200001,1000,policy)),.818004)

    def test_different_verified_model_cannot_reuse_an_existing_ledger_policy(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'spend.json';SpendLedger(path,'4')
            with self.assertRaises(ValueError):SpendLedger(path,'4',model='gemini-3.1-pro-preview')

    def test_pro_reservation_settles_against_actual_context_tier(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'spend.json';ledger=SpendLedger(path,'4',model='gemini-3.1-pro-preview')
            index=ledger.reserve('PAGE_CONSEQUENCES. '+'x'*210000+'\nSOURCE DATA:\n'+json.dumps({'working_units':{'U1':{}}}))
            reserved=json.loads(path.read_text())['attempts'][0]
            self.assertGreater(float(reserved['reserved_usd']),2)
            ledger.settle(index,{'usage_metadata':{'prompt_token_count':50000,'candidates_token_count':1000,
                                                 'thoughts_token_count':2000}},True)
            self.assertEqual(float(json.loads(path.read_text())['accounted_upper_bound_usd']),.136)

    def test_parallel_reservations_share_one_ceiling(self):
        from concurrent.futures import ThreadPoolExecutor
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'spend.json'; SpendLedger(path, '1')
            def reserve(_):
                try: return SpendLedger(path,'1').reserve(self.prompt)
                except CostLimitExceeded: return None
            with ThreadPoolExecutor(max_workers=4) as pool: values = list(pool.map(reserve,range(4)))
            self.assertEqual(sum(v is not None for v in values),1)
            self.assertLessEqual(float(json.loads(path.read_text())['accounted_upper_bound_usd']),1)

    def test_closing_freezes_new_spending_without_releasing_unknown_failure_cost(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'spend.json';ledger=SpendLedger(path,'2');index=ledger.reserve(self.prompt)
            with self.assertRaises(ValueError):ledger.close()
            ledger.settle(index,None,False);before=json.loads(path.read_text())['accounted_upper_bound_usd']
            ledger.close()
            restarted=SpendLedger(path,'2')
            with self.assertRaisesRegex(CostLimitExceeded,'closed') as error:restarted.reserve(self.prompt)
            self.assertEqual(error.exception.attempts[0]['reason'],'ledger_closed')
            self.assertEqual(json.loads(path.read_text())['accounted_upper_bound_usd'],before)
