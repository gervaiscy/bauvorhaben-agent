from historical_facts import collect_historical_facts
"""Ownership, status and relationship boundaries; synthetic providers only."""
import copy
import json
from types import SimpleNamespace
import unittest

from bauvo_working_units.fact_artifact import validate_facts
from bauvo_working_units.page_flow import _prompt
from bauvo_working_units.phases.project_facts.full_context import context, target_batches
from bauvo_working_units.phases.project_facts.payload import expand_payload
from bauvo_working_units.phases.project_facts.provisions import provisions, FACT_STATUSES
from bauvo_working_units.phases.project_facts.revalidation import replay_project_facts
from bauvo_working_units.project_facts import collect_project_facts, facts_view
from bauvo_working_units.shared.source_documents import full_source_documents
from bauvo_working_units.trace import ModelRequestFailed
from test_project_facts import document


def payload(prompt):
    return json.loads(prompt.split('SOURCE DATA:\n', 1)[1])


def answer(prompt):
    data = payload(prompt)
    return {'verpflichtungen': [
        {'provision_id': unit['id'], 'status': 'CONTRACT_RULE', 'related_provision_ids': [],
         'uncertainty': None, 'claims': [{'statement': 'Synthetic source-backed rule.',
                                         'evidence_ids': unit['evidence_ids']}], 'zitate': []}
        for unit in data['provisions'] if unit['id'] in data['target_provision_ids']],
        'nicht_als_fakt_erfasst': []}


class ProvisionOwnershipTests(unittest.TestCase):
    def test_sections_qualifications_and_table_rows_stay_whole_and_cover_every_original_span(self):
        doc = document('contract', '§ 1 Vergütung\nDer Preis gilt nur nach Freigabe.\n\n'
            'Die Ausnahme bleibt erhalten.\n\n§ 2 Ausführung\nEine weitere Regel.')
        pages, entries, _, _ = context(full_source_documents([doc]))
        units = provisions(pages, entries)
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0]['evidence_ids'], [e['evidence_id'] for e in entries[:3]])
        self.assertEqual([i for u in units for i in u['evidence_ids']], [e['evidence_id'] for e in entries])
        table = document('offer', 'Beschreibung\tMenge\tPreis\n'
            '1 Gebäude\t1 Stück\t195.000,00\nInklusive Planung.\n'
            '2 Zusätzliche Tür\t2 Stück\t600,00\nOhne Malerarbeiten.')
        pages, entries, _, _ = context(full_source_documents([table]))
        units = provisions(pages, entries)
        self.assertEqual([u['kind'] for u in units], ['PARAGRAPH', 'TABLE_ROW', 'TABLE_ROW'])
        self.assertEqual([len(u['evidence_ids']) for u in units], [1, 2, 2])

    def test_no_provision_is_split_or_assigned_to_multiple_batches_even_if_over_budget(self):
        doc = document('contract', '§ 1\n' + '\n'.join(f'Qualification {n}.' for n in range(60)) + '\n§ 2\nOther.')
        sources = full_source_documents([doc])
        pages, entries, _, _ = context(sources)
        specs = target_batches(sources, owned=True)
        self.assertEqual([len(m['target_evidence_ids']) for m, _ in specs], [61, 2])
        self.assertEqual([p for m, _ in specs for p in m['target_provision_ids']], ['P1', 'P2'])
        self.assertEqual([i for m, _ in specs for i in m['target_evidence_ids']], [e['evidence_id'] for e in entries])
        calls = []
        result = collect_historical_facts([doc], SimpleNamespace(extract=lambda p: (calls.append(payload(p)), answer(p))[1]))
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(calls[0]['source_pages'], calls[1]['source_pages'])
        self.assertEqual(expand_payload(calls[0])['source_pages'], pages)
        self.assertEqual(validate_facts(result, [doc]), result)

    def test_a_foreign_owner_unknown_owner_or_missing_owned_claim_evidence_blocks_without_deleting_raw(self):
        doc = document('contract', '\n\n'.join(f'Rule {n}.' for n in range(55)))
        for kind, code in [('foreign', 'provision_not_owned_by_batch'),
                           ('unknown', 'unknown_provision_id'),
                           ('no_evidence', 'fact_without_owned_claim_evidence')]:
            seen = []
            def provider(prompt):
                value = answer(prompt)
                if not seen:
                    if kind == 'foreign': value['verpflichtungen'][0]['provision_id'] = 'P55'
                    elif kind == 'unknown': value['verpflichtungen'][0]['provision_id'] = {'invalid': True}
                    else: value['verpflichtungen'][0]['claims'][0]['evidence_ids'] = ['S55']
                seen.append(copy.deepcopy(value))
                return value
            with self.subTest(kind=kind):
                result = collect_historical_facts([doc], SimpleNamespace(extract=provider))
                self.assertEqual(result['status'], 'incomplete')
                self.assertIn(code, {i['code'] for i in result['review_findings']})
                self.assertEqual([r['gemini'] for r in result['gemini_responses']], seen)
                self.assertEqual(validate_facts(result, [doc]), result)

    def test_explicit_statuses_survive_downstream_and_unresolved_requires_an_explanation(self):
        doc = document('contract', 'A source observation.')
        for status in FACT_STATUSES:
            def provider(prompt):
                value = answer(prompt)
                value['verpflichtungen'][0].update(status=status,
                    uncertainty='The contractual association is not established.' if status == 'UNRESOLVED' else None)
                return value
            result = collect_historical_facts([doc], SimpleNamespace(extract=provider))
            self.assertEqual(result['status'], 'recorded')
            self.assertEqual(facts_view(result)['facts'][0]['status'], status)
            for phase in ('PAGE_EXTRACTION', 'PAGE_OFFERS', 'PAGE_TIMING', 'PAGE_CONSEQUENCES', 'PAGE_APPLICABILITY'):
                data = payload(_prompt(phase, {'source_pages': [], 'project_facts': facts_view(result)}, 'de'))
                self.assertEqual(data['project_facts']['facts'][0]['status'], status)
                self.assertEqual(data['project_facts']['facts'][0]['provision_id'], 'P1')
        for status, code in [(None, 'missing_or_invalid_fact_status'), ('APPROVED', 'missing_or_invalid_fact_status'),
                             ('UNRESOLVED', 'unresolved_fact_without_uncertainty')]:
            def invalid(prompt):
                value = answer(prompt)
                value['verpflichtungen'][0]['status'] = status
                return value
            result = collect_historical_facts([doc], SimpleNamespace(extract=invalid))
            self.assertEqual(result['status'], 'incomplete')
            self.assertIn(code, {i['code'] for i in result['review_findings']})

    def test_an_owned_price_fact_cannot_append_an_independently_rewritten_foreign_credit_claim(self):
        doc = document('contract', 'Der Preis beträgt 200.000 EUR.\n\nDer Bonus setzt pünktliche Zahlung voraus.')
        def provider(prompt):
            value = answer(prompt)
            value['verpflichtungen'][0]['claims'].append({
                'statement': 'Der Bonus setzt pünktliche Zahlung voraus.', 'evidence_ids': ['S2']})
            return value
        result = collect_historical_facts([doc], SimpleNamespace(extract=provider))
        self.assertEqual(result['status'], 'incomplete')
        self.assertIn('claim_outside_owned_provision', {i['code'] for i in result['review_findings']})
        self.assertEqual(validate_facts(result, [doc]), result)

    def test_forward_references_resolve_to_canonical_facts_after_later_batch_without_more_calls(self):
        doc = document('contract', '\n\n'.join(f'Rule {n}.' for n in range(55)))
        calls = []
        def provider(prompt):
            value = answer(prompt)
            if not calls:
                value['verpflichtungen'][0]['related_provision_ids'] = ['P55', 'P55']
                value['verpflichtungen'][0]['claims'][0]['evidence_ids'].append('S55')
            calls.append(copy.deepcopy(value))
            return value
        result = collect_historical_facts([doc], SimpleNamespace(extract=provider))
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(len(calls), 2)
        first, last = result['facts'][0], result['facts'][-1]
        self.assertEqual(first['derived']['related_provision_ids'], ['P55'])
        self.assertEqual(first['derived']['related_fact_ids'], [last['id']])
        self.assertEqual(result['gemini_responses'][0]['gemini'], calls[0])
        self.assertEqual(validate_facts(result, [doc]), result)
        replay = replay_project_facts(result, [doc])
        self.assertEqual(replay['facts'], result['facts'])
        self.assertEqual(replay['gemini_responses'], result['gemini_responses'])

    def test_missing_canonical_fact_and_unknown_or_self_links_remain_blocking(self):
        doc = document('contract', 'A rule.\n\nAnother rule.')
        for reference, code in [('P2', 'unresolved_provision_reference'),
                                ('P1', 'self_provision_reference'), ('P999', 'unknown_provision_id')]:
            def provider(prompt):
                value = answer(prompt)
                value['verpflichtungen'][0]['related_provision_ids'] = [reference]
                value['verpflichtungen'].pop()
                value['nicht_als_fakt_erfasst'] = [{'evidence_id': 'S2', 'grund': 'Synthetic exclusion.'}]
                return value
            with self.subTest(reference=reference):
                result = collect_historical_facts([doc], SimpleNamespace(extract=provider))
                self.assertEqual(result['status'], 'incomplete')
                self.assertIn(code, {i['code'] for i in result['review_findings']})
                self.assertEqual(validate_facts(result, [doc]), result)
                self.assertEqual(replay_project_facts(result, [doc])['review_findings'], result['review_findings'])

    def test_tampered_ownership_status_and_canonical_links_are_rejected_against_original_sources(self):
        doc = document('contract', 'A rule.\n\nAnother rule.')
        result = collect_historical_facts([doc], SimpleNamespace(extract=answer))
        for kind in ('units', 'batch', 'digest', 'status', 'links'):
            changed = copy.deepcopy(result)
            if kind == 'units': changed['provisions'][0]['evidence_ids'].pop()
            elif kind == 'batch': changed['gemini_responses'][0]['target_provision_ids'].reverse()
            elif kind == 'digest': changed['gemini_responses'][0]['provisions_sha256'] = 'changed'
            elif kind == 'status': changed['facts'][0]['derived']['status'] = 'DOCUMENTED_AGREEMENT'
            else: changed['facts'][0]['derived']['related_fact_ids'] = [changed['facts'][1]['id']]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_facts(changed, [doc])

    def test_failed_owner_batch_preserves_completed_facts_and_pending_reference(self):
        doc = document('contract', '\n\n'.join(f'Rule {n}.' for n in range(55)))
        calls = []
        def provider(prompt):
            calls.append(prompt)
            if len(calls) == 2:
                raise ModelRequestFailed([{'error_type': 'fixture_failure'}])
            value = answer(prompt)
            value['verpflichtungen'][0]['related_provision_ids'] = ['P55']
            return value
        result = collect_historical_facts([doc], SimpleNamespace(extract=provider))
        self.assertEqual(result['status'], 'request_failed')
        self.assertTrue(result['facts'])
        self.assertIn('unresolved_provision_reference', {i['code'] for i in result['review_findings']})
        self.assertEqual(validate_facts(result, [doc]), result)

