from historical_facts import collect_historical_facts
from clause_fixtures import owned_answer, labeled_answer
"""Short model references bind exactly to immutable source fingerprints, offline."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from bauvo_working_units.cache import CachedModel, provider_request
from bauvo_working_units.call_budget import BudgetedModel, CallBudget
from bauvo_working_units.fact_artifact import validate_facts
from bauvo_working_units.page_flow import _prompt
from bauvo_working_units.phases.project_facts.full_context import context, records, target_batches
from bauvo_working_units.phases.project_facts.index import complete_response
from bauvo_working_units.phases.project_facts.payload import expand_payload
from bauvo_working_units.phases.project_facts.revalidation import replay_project_facts
from bauvo_working_units.project_facts import collect_project_facts, facts_view
from bauvo_working_units.trace import RawRunRecorder
from clause_fixtures import remap_references
from test_project_facts import document


def select_targets(prompt):
    data = json.loads(prompt.split('SOURCE DATA:\n', 1)[1])
    return owned_answer(data, {'verpflichtungen': [{'uncertainty': None, 'claims': [
        {'statement': 'Source-backed synthetic statement.', 'evidence_ids': data['target_evidence_ids']}],
        'zitate': []}], 'nicht_als_fakt_erfasst': []})


class ProjectFactLabelTests(unittest.TestCase):
    def test_full_package_has_short_labels_in_source_order_and_same_labels_in_every_batch(self):
        docs = [document('contract', '\n\n'.join(f'Provision {n}.' for n in range(101))),
                document('amendment', 'Amended provision.')]
        requests = []
        def provider(prompt):
            data = json.loads(prompt.split('SOURCE DATA:\n', 1)[1])
            requests.append(data)
            expanded = expand_payload(data)
            for identifier in expanded['reference_map'].values():
                self.assertNotIn(identifier, prompt)
            return select_targets(prompt)
        result = collect_historical_facts(docs, SimpleNamespace(extract=provider))
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual([r['target_evidence_ids'] for r in requests],
                         [[f'S{n}' for n in range(1, 51)], [f'S{n}' for n in range(51, 101)], ['S101', 'S102']])
        self.assertTrue(all(r['source_pages'] == requests[0]['source_pages'] for r in requests))
        self.assertEqual(list(result['reference_map']), [f'S{n}' for n in range(1, 103)])
        self.assertEqual(len(set(result['reference_map'].values())), 102)
        self.assertEqual(validate_facts(result, docs), result)

    def test_claims_context_exclusions_and_cross_document_support_bind_without_changing_raw_json(self):
        docs = [document('contract', 'Heading\nThe conditional credit.\nContact.'),
                document('amendment', 'The credit requires final payment.')]
        answer = {'verpflichtungen': [{'uncertainty': None, 'claims': [
            {'statement': 'The credit requires final payment.', 'evidence_ids': ['S4', 'S2', 'S2']}],
            'zitate': [{'evidence_id': 'S1'}, {'evidence_id': 'S1'}]}],
            'nicht_als_fakt_erfasst': [{'evidence_id': 'S3', 'grund': 'Contact only.'}]}
        answer = labeled_answer(docs, answer)
        before = copy.deepcopy(answer)
        result = collect_historical_facts(docs, SimpleNamespace(extract=lambda _: answer))
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(answer, before)
        self.assertEqual(result['gemini_responses'][0]['gemini'], before)
        self.assertEqual(result['facts'][0]['gemini'], before['verpflichtungen'][0])
        derived = result['facts'][0]['derived']
        self.assertEqual([c['quote'] for c in derived['claims'][0]['verified_citations']],
                         ['The conditional credit.\n', 'The credit requires final payment.'])
        self.assertEqual([c['quote'] for c in derived['context_citations']], ['Heading\n'])
        self.assertEqual(result['excluded_passages'][0]['derived']['citation']['quote'], 'Contact.')
        self.assertEqual(facts_view(result)['facts'][0]['claims'], derived['claims'])
        self.assertEqual(validate_facts(result, docs), result)

    def test_unknown_labels_wrong_case_padding_and_internal_fingerprints_are_not_guessed(self):
        doc = document('contract', 'An actual provision.')
        good = collect_historical_facts([doc], SimpleNamespace(extract=select_targets))
        for bad in ('S2', 's1', 'S01', ' S1 ', next(iter(good['reference_map'].values()))):
            def provider(prompt):
                answer = select_targets(prompt)
                answer['verpflichtungen'][0]['claims'][0]['evidence_ids'] = [bad]
                self.assertFalse(complete_response(prompt, answer))
                return answer
            with self.subTest(reference=bad):
                result = collect_historical_facts([doc], SimpleNamespace(extract=provider))
                self.assertEqual(result['status'], 'incomplete')
                issues = result['review_findings']
                self.assertTrue(any(i['code'] == 'unknown_evidence_id' and i['evidence_id'] == bad for i in issues))
                self.assertIn('unaccounted_source_passage', {i['code'] for i in issues})
                self.assertEqual(validate_facts(result, [doc]), result)

    def test_unknown_context_and_exclusion_labels_and_empty_claims_remain_blocking(self):
        doc = document('contract', 'An actual provision.')
        for kind in ('context', 'exclusion', 'empty'):
            def provider(prompt):
                answer = select_targets(prompt)
                if kind == 'context':
                    answer['verpflichtungen'][0]['zitate'] = [{'evidence_id': 'S999'}]
                elif kind == 'exclusion':
                    answer['nicht_als_fakt_erfasst'] = [{'evidence_id': 'S999', 'grund': 'Unknown.'}]
                else:
                    answer['verpflichtungen'][0]['claims'][0]['evidence_ids'] = []
                return answer
            with self.subTest(kind=kind):
                result = collect_historical_facts([doc], SimpleNamespace(extract=provider))
                self.assertEqual(result['status'], 'incomplete')
                self.assertEqual(validate_facts(result, [doc]), result)

    def test_saved_mapping_and_each_response_binding_are_checked_against_original_sources(self):
        docs = [document('contract', 'First.\nSecond.')]
        result = collect_historical_facts(docs, SimpleNamespace(extract=select_targets))
        for kind in ('swap', 'remove', 'extra', 'protocol', 'response_binding'):
            bad = copy.deepcopy(result)
            if kind == 'swap':
                bad['reference_map']['S1'], bad['reference_map']['S2'] = bad['reference_map']['S2'], bad['reference_map']['S1']
            elif kind == 'remove':
                del bad['reference_map']['S1']
            elif kind == 'extra':
                bad['reference_map']['S3'] = bad['reference_map']['S1']
            elif kind == 'protocol':
                bad['reference_protocol'] = 'unknown'
            else:
                bad['gemini_responses'][0]['reference_map_sha256'] = 'changed'
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'reference mapping'):
                validate_facts(bad, docs)

    def test_changed_or_duplicated_labels_in_saved_prompt_cannot_be_rebound(self):
        prompts = []
        collect_historical_facts([document('contract', 'First.\nSecond.')],
                              SimpleNamespace(extract=lambda p: (prompts.append(p), select_targets(p))[1]))
        source = json.loads(prompts[0].split('SOURCE DATA:\n', 1)[1])
        for replacement in ('S2', 'S01', 'unknown'):
            bad = copy.deepcopy(source)
            bad['source_pages'][0]['segments'][0][0] = replacement
            with self.subTest(label=replacement), self.assertRaisesRegex(ValueError, 'IDs do not match'):
                expand_payload(bad)

    def test_completed_label_responses_and_raw_file_replay_offline_without_new_calls(self):
        docs = [document('contract', 'An actual provision.')]
        calls = []
        identity = {'model': 'fixture'}
        def provider(prompt):
            calls.append(prompt)
            return select_targets(prompt)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = RawRunRecorder(CachedModel(BudgetedModel(lambda: SimpleNamespace(extract=provider), CallBudget(1)),
                                               root/'store', identity), root/'raw.json')
            result = collect_historical_facts(docs, model)
            saved = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
            offline = CachedModel(BudgetedModel(lambda: self.fail('Provider constructed offline'), CallBudget()),
                                  root/'store', identity)
            self.assertEqual(collect_historical_facts(docs, offline), result)
            replay = replay_project_facts(result, docs)
            self.assertEqual(replay['schema_version'], 13)
            self.assertEqual(replay['status'], 'recorded')
            for key in ('facts', 'gemini_responses', 'reference_map'):
                self.assertEqual(replay[key], result[key])
            self.assertEqual(json.loads((root/'raw.json').read_text()), [result['gemini_responses'][0]['gemini']])
            self.assertEqual(saved, {p: p.read_bytes() for p in saved})
            self.assertEqual(len(calls), 1)

    def test_source_changes_invalidate_request_identity_even_when_label_is_reused(self):
        prompts = []
        for text in ('Original provision.', 'Changed provision.'):
            result = collect_historical_facts([document('contract', text)],
                SimpleNamespace(extract=lambda p: (prompts.append(p), select_targets(p))[1]))
            self.assertEqual(list(result['reference_map']), ['S1'])
        self.assertNotEqual(provider_request(prompts[0], {}), provider_request(prompts[1], {}))

    def test_historical_fingerprint_answers_replay_under_their_original_protocol(self):
        docs = [document('contract', 'An original provision.')]
        current = collect_historical_facts(docs, SimpleNamespace(extract=select_targets))
        historical = copy.deepcopy(current)
        historical.update(schema_version=11, evidence_protocol='source-backed-interpretations-v7')
        mapping = historical.pop('reference_map')
        del historical['reference_protocol']
        response = historical['gemini_responses'][0]
        del response['reference_map_sha256']
        response['gemini'] = remap_references(response['gemini'], mapping)
        facts, exclusions, coverage, findings = records(response['gemini'], historical['source_documents'],
            target_evidence_ids=response['target_evidence_ids'], response_id=response['id'],
            require_claims=True, allow_context_citations=True, derive_fact_citations=True, authoritative_claims=True)
        historical.update(facts=facts, excluded_passages=exclusions, review_findings=findings)
        response['coverage'] = coverage
        before = copy.deepcopy(historical)
        self.assertEqual(validate_facts(historical, docs), historical)
        replay = replay_project_facts(historical, docs)
        self.assertEqual(replay['schema_version'], 11)
        self.assertEqual(replay['status'], 'recorded')
        self.assertEqual(replay['gemini_responses'], before['gemini_responses'])
        self.assertEqual(replay['facts'], before['facts'])
        self.assertEqual(historical, before)

    def test_new_label_requests_cannot_reuse_the_old_fingerprint_request_identity(self):
        docs = [document('contract', 'An original provision.')]
        prompts = []
        result = collect_historical_facts(docs,
            SimpleNamespace(extract=lambda p: (prompts.append(p), select_targets(p))[1]))
        sources = result['source_documents']
        pages, entries, _, _ = context(sources)
        metadata, _ = target_batches(sources)[0]
        legacy_prompt = _prompt('PAGE_PROJECT_FACTS', {**metadata, 'source_pages': pages,
            'evidence_catalog': entries, 'catalog_protocol': result['catalog_protocol'],
            'evidence_protocol': 'source-backed-interpretations-v7'}, 'de')
        self.assertNotEqual(provider_request(prompts[0], {}), provider_request(legacy_prompt, {}))
        self.assertIn(entries[0]['evidence_id'], legacy_prompt)
        self.assertNotIn(entries[0]['evidence_id'], prompts[0])

