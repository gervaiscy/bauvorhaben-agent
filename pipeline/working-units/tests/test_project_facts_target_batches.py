from historical_facts import collect_historical_facts
from clause_fixtures import remap_references
from clause_fixtures import decode_prompt
"""Bounded assignments keep complete context, evidence and independent recovery."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from bauvo_working_units.cache import CachedModel
from bauvo_working_units.fact_artifact import validate_facts
from bauvo_working_units.phases.project_facts.full_context import target_batches, records
from bauvo_working_units.phases.project_facts.index import complete_response, index_response
from bauvo_working_units.project_facts import collect_project_facts, require_current_catalog
from bauvo_working_units.recovery import RequestRecovery
from bauvo_working_units.shared.source_documents import full_source_documents
from bauvo_working_units.trace import RawRunRecorder
from clause_fixtures import index_answer, interpreted
from test_project_facts import document


def payload(prompt):
    return decode_prompt(prompt)


def sources():
    return [document('contract', '\n\n'.join(f'Independent provision {i}.' for i in range(101))),
            document('amendment', 'Credit requires payment within 14 days.')]


class TargetBatchFactsTests(unittest.TestCase):
    def test_every_request_keeps_all_sources_and_catalog_but_assigns_at_most_50_passages(self):
        docs = sources()
        calls = []
        def provider(prompt):
            data = payload(prompt)
            calls.append(data)
            return index_answer(data)
        result = collect_historical_facts(docs, SimpleNamespace(extract=provider),
                                      max_chars=1, chunk_pages=1, overlap_pages=0)
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual([len(d['target_evidence_ids']) for d in calls], [50, 50, 2])
        for data in calls:
            self.assertEqual(data['source_pages'], calls[0]['source_pages'])
            self.assertEqual(data['evidence_catalog'], calls[0]['evidence_catalog'])
            self.assertEqual([p['text'] for p in data['source_pages']],
                             [d.pages[0]['text'] for d in docs])
        assigned = [i for d in calls for i in d['target_evidence_ids']]
        self.assertEqual(assigned, [e['evidence_id'] for e in calls[0]['evidence_catalog']])
        self.assertEqual(len(set(assigned)), 102)
        self.assertEqual(len({r['id'] for r in result['gemini_responses']}), 3)
        self.assertEqual(validate_facts(result, docs), result)

    def test_outside_amendment_can_support_each_batch_without_being_assigned_or_discarded(self):
        docs = sources()
        calls = []
        def provider(prompt):
            data = payload(prompt)
            answer = index_answer(data)
            amendment = data['evidence_catalog'][-1]['evidence_id']
            for row in answer['verpflichtungen']:
                if {'evidence_id': amendment} not in row['zitate']:
                    row['zitate'].append({'evidence_id': amendment})
                    row['claims'][0]['evidence_ids'].append(amendment)
            calls.append(copy.deepcopy(answer))
            return answer
        result = collect_historical_facts(docs, SimpleNamespace(extract=provider))
        self.assertEqual(result['status'], 'recorded')
        for response, raw in zip(result['gemini_responses'], calls):
            self.assertEqual(response['gemini'], raw)
        amendment_id = result['gemini_responses'][0]['coverage']['supporting_ids'][0]
        for response in result['gemini_responses'][:2]:
            self.assertNotIn(amendment_id, response['coverage']['expected_ids'])
            self.assertIn(amendment_id, response['coverage']['supporting_ids'])
        # The amendment's own assignment is still required in the final batch.
        self.assertIn(amendment_id, result['gemini_responses'][2]['coverage']['expected_ids'])
        self.assertTrue(all(any(c['evidence_id'] == amendment_id
                               for c in f['derived']['verified_citations']) for f in result['facts']))
        self.assertEqual(validate_facts(result, docs), result)

    def test_accounting_rejects_unrelated_facts_and_outside_exclusions_without_losing_raw_rows(self):
        docs = sources()
        seen = []
        def provider(prompt):
            data = payload(prompt)
            answer = index_answer(data)
            if not seen:
                outside = data['evidence_catalog'][-1]['evidence_id']
                answer['verpflichtungen'].append(interpreted([{'evidence_id': outside}]))
                answer['nicht_als_fakt_erfasst'].append({'evidence_id': outside, 'grund': 'Unrelated'})
                self.assertFalse(complete_response(prompt, answer))
            seen.append(copy.deepcopy(answer))
            return answer
        result = collect_historical_facts(docs, SimpleNamespace(extract=provider))
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['gemini_responses'][0]['gemini'], seen[0])
        codes = {f['code'] for f in result['review_findings']}
        self.assertTrue({'fact_without_target_evidence', 'exclusion_outside_targets'} <= codes)
        self.assertEqual(validate_facts(result, docs), result)

    def test_context_citation_does_not_account_for_an_unselected_target(self):
        docs = sources()
        source_docs = full_source_documents(docs)
        metadata, rid = target_batches(source_docs)[0]
        from bauvo_working_units.phases.project_facts.full_context import context
        pages, entries, _, _ = context(source_docs)
        answer = {'verpflichtungen': [interpreted([{'evidence_id': entries[-1]['evidence_id']}])],
                  'nicht_als_fakt_erfasst': []}
        _, _, coverage, findings = index_response(answer, entries, pages,
            target_evidence_ids=metadata['target_evidence_ids'])
        self.assertEqual(coverage['missing_ids'], metadata['target_evidence_ids'])
        self.assertIn('fact_without_target_evidence', {i['code'] for i in findings})

    def test_model_cache_replays_all_completed_answers_including_incomplete_batch(self):
        calls, broken = [], True
        def provider(prompt):
            data = payload(prompt)
            calls.append(data['target_evidence_ids'])
            return ({'verpflichtungen': [], 'nicht_als_fakt_erfasst': []}
                    if broken and len(calls) == 2 else index_answer(data))
        docs = sources()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = SimpleNamespace(extract=provider)
            model = RawRunRecorder(CachedModel(backend, root/'cache', {'model': 'fixture'}), root/'raw.json')
            first = collect_historical_facts(docs, model)
            self.assertEqual(first['status'], 'incomplete')
            saved = {p: p.read_bytes() for p in (root/'cache').glob('*.json')}
            self.assertEqual(len(saved), 3)
            broken = False
            second = collect_historical_facts(docs, model)
            self.assertEqual(second['status'], 'incomplete')
            self.assertEqual(len(calls), 3)
            self.assertEqual(saved, {p: p.read_bytes() for p in saved})
            self.assertEqual(json.loads((root/'raw.json').read_text())[1],
                             first['gemini_responses'][1]['gemini'])

    def test_atomic_recovery_reuses_complete_targets_and_context_change_invalidates_all_batches(self):
        calls = []
        class Provider:
            broken = True
            def extract(self, prompt):
                data = payload(prompt)
                calls.append(data)
                answer = ({'verpflichtungen': [], 'nicht_als_fakt_erfasst': []}
                          if self.broken and len(calls) == 2 else index_answer(data))
                self.last_raw_response = json.dumps(answer)
                self.last_finish_reason = 'STOP'
                return answer
        docs = sources()
        backend = Provider()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = collect_historical_facts(docs, RequestRecovery(backend, root/'first', [], {'model': 'fixture'}))
            self.assertEqual(first['status'], 'incomplete')
            saved = {p: p.read_bytes() for p in (root/'first').rglob('*') if p.is_file()}
            backend.broken = False
            recovery = RequestRecovery(backend, root/'retry', [root/'first'], {'model': 'fixture'})
            second = collect_historical_facts(docs, recovery)
            self.assertEqual(second['status'], 'incomplete')
            self.assertEqual(recovery.reused, 3)
            self.assertEqual(len(calls), 3)
            self.assertEqual(saved, {p: p.read_bytes() for p in saved})
            changed = [docs[0], document('amendment', 'Credit requires payment within 16 days.')]
            recovery = RequestRecovery(backend, root/'changed', [root/'first', root/'retry'], {'model': 'fixture'})
            latest = collect_historical_facts(changed, recovery)
            self.assertEqual(recovery.reused, 0)
            self.assertEqual(len(calls), 6)
            self.assertEqual(validate_facts(latest, changed), latest)

    def test_saved_assignments_cannot_be_removed_reordered_or_relabelled(self):
        docs = sources()
        result = collect_historical_facts(docs, SimpleNamespace(extract=lambda p: index_answer(payload(p))))
        for kind in ('missing_batch', 'swap_batches', 'target_ids', 'batch_size', 'coverage'):
            changed = copy.deepcopy(result)
            if kind == 'missing_batch':
                changed['gemini_responses'].pop()
            elif kind == 'swap_batches':
                changed['gemini_responses'].reverse()
            elif kind == 'target_ids':
                changed['gemini_responses'][0]['target_evidence_ids'].pop()
            elif kind == 'batch_size':
                changed['target_batch_size'] = 100
            else:
                changed['gemini_responses'][0]['coverage']['expected_ids'].pop()
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_facts(changed, docs)

    def test_historical_single_request_remains_readable_but_cannot_resume_new_analysis(self):
        docs = [document('contract', 'An original provision.')]
        collection = collect_historical_facts(docs, SimpleNamespace(extract=lambda p: index_answer(payload(p))))
        original_sources = full_source_documents(docs)
        collection['gemini_responses'][0]['gemini'] = remap_references(collection['gemini_responses'][0]['gemini'], collection['reference_map'])
        raw = collection['gemini_responses'][0]['gemini']
        for row in raw['verpflichtungen']:
            row.pop('claims')
        facts, exclusions, coverage, findings = records(raw, original_sources)
        collection.update(schema_version=6, evidence_protocol='source-backed-interpretations-v2',
                          facts=facts, excluded_passages=exclusions, review_findings=findings)
        collection.pop('target_batch_size')
        collection['gemini_responses'][0].pop('target_evidence_ids')
        collection['gemini_responses'][0]['coverage'] = coverage
        self.assertEqual(validate_facts(collection, docs), collection)
        with self.assertRaisesRegex(ValueError, 'regenerate'):
            require_current_catalog(collection)
