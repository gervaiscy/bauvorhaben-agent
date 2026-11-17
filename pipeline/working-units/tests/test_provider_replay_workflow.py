from historical_facts import collect_historical_facts
"""Offline end-to-end controls for response preservation, planning and paid-call limits."""
import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from bauvo_working_units import cache, cli
from bauvo_working_units.cache import CachedModel, provider_request, digest
from bauvo_working_units.call_budget import BudgetedModel, CallBudget, CallLimitExceeded
from bauvo_working_units.gemini import IncompleteResponse
from bauvo_working_units.page_flow import _prompt
from bauvo_working_units.phases.project_facts.index import complete_response
from bauvo_working_units.phases.project_facts.payload import expand_payload
from bauvo_working_units.phases.project_facts.revalidation import replay_project_facts
from bauvo_working_units.project_facts import collect_project_facts, facts_view
from bauvo_working_units.project_flow import Document
from bauvo_working_units.trace import RawRunRecorder, RetryingModel
from clause_fixtures import decode_prompt, index_answer
from test_project_facts import document
from test_schema6_flow import fixture

IDENTITY = {'project':'unused', 'location':'global', 'model':'gemini-3.5-flash'}


def answer(prompt):
    value = index_answer(decode_prompt(prompt))
    for row in value['verpflichtungen']:
        row.pop('interpretation')
        row['zitate'] = []
    return value


class Provider:
    def __init__(self, respond=answer):
        self.respond, self.calls = respond, []
        self.last_raw_response = self.last_finish_reason = None

    def extract(self, prompt):
        self.calls.append(prompt)
        value = self.respond(prompt)
        self.last_raw_response, self.last_finish_reason = json.dumps(value), 'STOP'
        return value


def sources():
    return [Document('contract', 'Contract', 'test.pdf', schema_version=6, source_key='contract',
                     pages=({'pdf_page':1, 'text':'\n\n'.join(f'Provision {n}.' for n in range(102))},))]


class ProviderReplayWorkflowTests(unittest.TestCase):
    def test_compact_full_context_is_lossless_and_ids_remain_source_bound(self):
        docs = sources() + [document('amendment', 'The credit requires timely payment.')]
        prompts = []
        def provider(prompt):
            prompts.append(prompt)
            return answer(prompt)
        result = collect_historical_facts(docs, SimpleNamespace(extract=provider))
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(len(prompts), 3)
        contexts = []
        for prompt in prompts:
            compact = json.loads(prompt.split('SOURCE DATA:\n', 1)[1])
            self.assertNotIn('evidence_catalog', compact)
            self.assertTrue(all('text' not in page and 'segments' in page for page in compact['source_pages']))
            expanded = expand_payload(compact)
            self.assertEqual([p['text'] for p in expanded['source_pages']], [d.pages[0]['text'] for d in docs])
            self.assertLess(len(json.dumps(compact)), len(json.dumps(expanded)))
            contexts.append(compact['source_pages'])
        self.assertTrue(all(c==contexts[0] for c in contexts))
        tampered = json.loads(prompts[0].split('SOURCE DATA:\n')[1])
        tampered['source_pages'][0]['segments'][0][0] = 'invented'
        with self.assertRaisesRegex(ValueError, 'IDs do not match'):
            expand_payload(tampered)

    def test_one_authoritative_interpretation_normalizes_repetition_and_blank_uncertainty(self):
        doc = document('contract', 'Guarantee without significant defects.\nDue before the second instalment.')
        raw = answer(self._prompt_for(doc))
        row = raw['verpflichtungen'][0]
        row['interpretation'] = 'An absolute guarantee is due immediately. WRONG OLD SUMMARY'
        row['claims'][0]['statement'] = 'Guarantee without significant defects, due before the second instalment.'
        row['claims'][0]['evidence_ids'] *= 2
        row['zitate'] = [{'evidence_id':row['claims'][0]['evidence_ids'][0]}]*2
        row['uncertainty'] = '  '
        before = copy.deepcopy(raw)
        result = collect_historical_facts([doc], SimpleNamespace(extract=lambda _:copy.deepcopy(raw)))
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['gemini_responses'][0]['gemini'], before)
        derived = result['facts'][0]['derived']
        self.assertEqual(len(derived['claims'][0]['verified_citations']), 2)
        self.assertEqual(derived['interpretation'], row['claims'][0]['statement'])
        self.assertIsNone(derived['uncertainty'])
        for phase in ('PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'):
            prompt = _prompt(phase, {'source_pages':[], 'project_facts':facts_view(result)}, 'de')
            self.assertNotIn('WRONG OLD SUMMARY', prompt)
            if phase == 'PAGE_TIMING':
                self.assertEqual(json.loads(prompt.split('SOURCE DATA:\n', 1)[1])['project_facts'],
                                 {'facts': [{'id': f['id'], 'text': f['text']} for f in facts_view(result)['facts']]})
            else:
                self.assertIn(row['claims'][0]['statement'], prompt)
        self.assertEqual(raw, before)

    def _prompt_for(self, doc):
        prompts = []
        collect_historical_facts([doc], SimpleNamespace(extract=lambda p:(prompts.append(p), answer(p))[1]))
        return prompts[0]

    def test_completed_invalid_answers_are_immutable_and_replayed_without_validator_dependency(self):
        doc = document('contract', 'One requirement.')
        invalid = {'verpflichtungen':[], 'nicht_als_fakt_erfasst':[]}
        provider = Provider(lambda _: copy.deepcopy(invalid))
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = CachedModel(BudgetedModel(lambda:provider, CallBudget(1)), root, IDENTITY)
            first = collect_historical_facts([doc], model)
            self.assertEqual(first['status'], 'incomplete')
            saved = {p:p.read_bytes() for p in root.glob('*.json')}
            offline = CachedModel(BudgetedModel(lambda:self.fail('Client created offline'),CallBudget()), root, IDENTITY)
            # Caching never consults application validation, even if it changes or raises.
            with patch('bauvo_working_units.phases.project_facts.index.complete_response', side_effect=AssertionError('validator consulted')):
                second = collect_historical_facts([doc], offline)
            self.assertEqual(second, first)
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(saved, {p:p.read_bytes() for p in saved})

    def test_full_stage_preflight_blocks_before_first_call_if_limit_is_insufficient(self):
        with TemporaryDirectory() as tmp:
            budget = CallBudget(2)
            backend = BudgetedModel(lambda:self.fail('No client should be constructed'), budget)
            model = CachedModel(backend, Path(tmp)/'store', IDENTITY, plan_path=Path(tmp)/'plan.json')
            with self.assertRaisesRegex(CallLimitExceeded, 'Planned 3 new provider calls'):
                collect_historical_facts(sources(), RetryingModel(RawRunRecorder(model,Path(tmp)/'raw.json')))
            self.assertEqual(budget.used, 0)
            plan = json.loads((Path(tmp)/'plan.json').read_text())[0]
            self.assertEqual(plan['planned_new_calls'], 3)
            self.assertEqual(plan['saved_responses'], 0)

    def test_retry_is_an_actual_attempt_and_cannot_exceed_shared_limit(self):
        calls = []
        class Unfinished:
            last_raw_response = '{unfinished'
            last_finish_reason = 'MAX_TOKENS'
            def extract(self, prompt):
                calls.append(prompt)
                raise IncompleteResponse('unfinished_generation','MAX_TOKENS')
        with TemporaryDirectory() as tmp:
            budget = CallBudget(1)
            provider = BudgetedModel(Unfinished, budget)
            model = RetryingModel(RawRunRecorder(CachedModel(provider,Path(tmp)/'store',IDENTITY),Path(tmp)/'raw.json'))
            result = collect_historical_facts([document('contract','One requirement.')], model)
            self.assertEqual(result['status'], 'request_failed')
            self.assertEqual(len(calls), 1)
            self.assertEqual(budget.used, 1)
            attempts = json.loads((Path(tmp)/'raw.attempts.json').read_text())
            self.assertEqual(attempts[0]['finish_reason'], 'MAX_TOKENS')
            self.assertEqual(attempts[1]['error_type'], 'CallLimitExceeded')
            self.assertEqual(list((Path(tmp)/'store').glob('*.json')), [])

    def test_only_explicitly_selected_bad_request_is_regenerated_and_old_answer_is_preserved(self):
        docs = sources()
        calls = []
        def initial(prompt):
            calls.append(prompt)
            return {'verpflichtungen':[], 'nicht_als_fakt_erfasst':[]} if len(calls)==2 else answer(prompt)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_model = CachedModel(BudgetedModel(lambda:Provider(initial),CallBudget(3)),root,IDENTITY)
            first = collect_historical_facts(docs, first_model)
            self.assertEqual(first['status'], 'incomplete')
            old = {p:p.read_bytes() for p in root.glob('*.json')}
            key = digest(provider_request(calls[1], IDENTITY))
            fresh_provider = Provider()
            budget = CallBudget(1)
            fresh = CachedModel(BudgetedModel(lambda:fresh_provider,budget),root,IDENTITY,refresh_requests=[key])
            second = collect_historical_facts(docs, fresh)
            self.assertEqual(second['status'], 'recorded')
            self.assertEqual(fresh_provider.calls, [calls[1]])
            self.assertEqual(budget.used, 1)
            self.assertEqual(old, {p:p.read_bytes() for p in old})
            self.assertEqual(len(list((root/'versions'/key).glob('*.json'))),1)
            offline = CachedModel(BudgetedModel(lambda:self.fail('Offline call'),CallBudget()),root,IDENTITY)
            self.assertEqual(collect_historical_facts(docs, offline), second)

    def test_request_identity_excludes_unrelated_validators_phases_and_global_protocol(self):
        prompt = self._prompt_for(document('contract','One requirement.'))
        identity = {**IDENTITY, 'protocol':'old', 'phase_thinking_levels':{'PAGE_TIMING':'LOW'}}
        original = provider_request(prompt, identity)
        with patch.dict(cache.RESPONSE_SCHEMAS, {'PAGE_TIMING':{'new':'schema'}}):
            self.assertEqual(provider_request(prompt,{**identity,'protocol':'new'}), original)
        with patch.dict(cache.RESPONSE_SCHEMAS, {'PAGE_PROJECT_FACTS':{'new':'schema'}}):
            self.assertNotEqual(provider_request(prompt,identity), original)
        for key in ('project','location','model'):
            self.assertNotEqual(provider_request(prompt,{**identity,key:'different'}), original)
        self.assertNotEqual(provider_request(prompt+' ',identity), original)
        with patch('bauvo_working_units.cache.generation_for',return_value={'max_output_tokens':1}):
            self.assertNotEqual(provider_request(prompt,identity), original)

    def test_replay_keeps_genuine_missing_unknown_and_empty_claim_evidence_blocking(self):
        doc = document('contract','First requirement.\nSecond requirement.')
        original = answer(self._prompt_for(doc))
        for kind in ('unknown','empty','missing'):
            raw = copy.deepcopy(original)
            if kind=='unknown':raw['verpflichtungen'][0]['claims'][0]['evidence_ids'].append('invented')
            elif kind=='empty':raw['verpflichtungen'][0]['claims'][0]['evidence_ids']=[]
            else:raw['verpflichtungen'][0]['claims'][0]['evidence_ids'].pop()
            saved = collect_historical_facts([doc], SimpleNamespace(extract=lambda _:copy.deepcopy(raw)))
            replayed = replay_project_facts(saved,[doc])
            with self.subTest(kind=kind):
                self.assertEqual(replayed['status'],'incomplete')
                self.assertEqual(replayed['gemini_responses'][0]['gemini'],raw)
                self.assertTrue(replayed['review_findings'])

    def test_cli_is_offline_by_default_and_plan_only_creates_no_fake_results(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); inputs=root/'inputs';inputs.mkdir();fixture(inputs,count=1)
            argv=['units','--pages',str(inputs/'pages.json'),'--documents',str(inputs/'documents.json'),
                  '--stop-after','project-facts','--output',str(root/'result.json')]
            with patch('socket.socket.connect',side_effect=AssertionError('Network contacted')),patch.object(cli,'GeminiModel',side_effect=AssertionError('Client constructed')):
                with patch.object(sys,'argv',argv+['--plan-only']):cli.main()
                self.assertFalse((root/'result.json').exists())
                self.assertFalse((root/'result.gemini-raw.json').exists())
                self.assertFalse((root/'result.passes').exists())
                self.assertEqual(json.loads((root/'result.request-plan.json').read_text())[0]['planned_new_calls'],1)
                with patch.object(sys,'argv',argv),self.assertRaisesRegex(SystemExit,'Planned 1 new provider calls'):cli.main()

    def test_cli_replay_requires_no_cloud_project_credentials_client_or_network(self):
        from bauvo_working_units.project_flow import load_documents
        with TemporaryDirectory() as tmp:
            root=Path(tmp);inputs=root/'inputs';inputs.mkdir();fixture(inputs,count=1)
            docs=load_documents(inputs/'pages.json',inputs/'documents.json')
            saved=collect_historical_facts(docs,Provider())
            source=root/'saved.json';source.write_text(json.dumps(saved));before=source.read_bytes()
            argv=['units','--pages',str(inputs/'pages.json'),'--documents',str(inputs/'documents.json'),
                  '--stop-after','project-facts','--replay-project-facts',str(source),'--output',str(root/'replayed.json')]
            with patch.object(sys,'argv',argv),patch('socket.socket.connect',side_effect=AssertionError('Network')),patch.object(cli,'GeminiModel',side_effect=AssertionError('Client')):
                cli.main()
            result=json.loads((root/'replayed.json').read_text())
            self.assertEqual(result['status'],'recorded')
            self.assertEqual(source.read_bytes(),before)
            self.assertEqual(result['gemini_responses'],saved['gemini_responses'])
            self.assertEqual(result['validation_history'][-1]['source_artifact_sha256'],digest(saved))
            self.assertFalse((root/'replayed.gemini-raw.json').exists())

    def test_completed_json_scalars_are_saved_even_though_the_application_rejects_them(self):
        for value in (None, True, 42, 'Not the expected object'):
            with self.subTest(value=value), TemporaryDirectory() as tmp:
                provider=Provider(lambda _:value)
                budget=CallBudget(1)
                model=CachedModel(BudgetedModel(lambda:provider,budget),tmp,IDENTITY)
                doc=document('contract','A required provision.')
                first=collect_historical_facts([doc],model)
                second=collect_historical_facts([doc],model)
                self.assertEqual(first['status'],'incomplete')
                self.assertEqual(second,first)
                self.assertEqual(budget.used,1)
                self.assertEqual(len(provider.calls),1)
                self.assertEqual(first['gemini_responses'][0]['gemini'],value)

    def test_plan_only_stops_at_first_missing_dependent_request_without_fake_results(self):
        from bauvo_working_units.project_flow import load_documents
        with TemporaryDirectory() as tmp:
            root=Path(tmp);inputs=root/'inputs';inputs.mkdir();fixture(inputs,count=1)
            docs=load_documents(inputs/'pages.json',inputs/'documents.json')
            facts=collect_historical_facts(docs,Provider())
            path=root/'facts.json';path.write_text(json.dumps(facts))
            argv=['units','--pages',str(inputs/'pages.json'),'--documents',str(inputs/'documents.json'),
                  '--project-facts',str(path),'--stop-after','aufgaben','--plan-only','--output',str(root/'planned.json')]
            with patch.object(sys,'argv',argv),patch.object(cli,'GeminiModel',side_effect=AssertionError('Client')):
                cli.main()
            plans=json.loads((root/'planned.request-plan.json').read_text())
            self.assertEqual(len(plans),1)
            self.assertEqual(plans[0]['requests'][0]['phase'],'PAGE_EXTRACTION')
            self.assertEqual(plans[0]['planned_new_calls'],1)
            self.assertFalse((root/'planned.json').exists())
