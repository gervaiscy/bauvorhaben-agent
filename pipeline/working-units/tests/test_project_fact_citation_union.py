from historical_facts import collect_historical_facts
"""Claim selections drive aggregation and coverage without duplicate model lists."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from bauvo_working_units.cache import CachedModel
from bauvo_working_units.fact_artifact import validate_facts
from bauvo_working_units.phases.project_facts.full_context import records
from bauvo_working_units.phases.project_facts.index import complete_response
from bauvo_working_units.phases.project_facts.revalidation import revalidate_context_citations
from bauvo_working_units.project_facts import collect_project_facts
from bauvo_working_units.recovery import RequestRecovery
from clause_fixtures import entries_for, interpreted, labeled_answer, remap_references
from test_project_facts import document


class FactCitationUnionTests(unittest.TestCase):
    def setUp(self):
        self.doc = document('contract', 'Guarantee clause\nCompletion without significant defects.\n'
                            'Delivery before the second instalment.')
        self.entries = entries_for(self.doc)
        self.ids = [e['evidence_id'] for e in self.entries]
        row = interpreted([{'evidence_id':self.ids[0]}])
        row['claims'] = [{'statement':'The qualified guarantee is due before the second instalment.',
                          'evidence_ids':self.ids[1:]}]
        self.answer = labeled_answer([self.doc], {'verpflichtungen':[row], 'nicht_als_fakt_erfasst':[]})
        self.labels = ['S1', 'S2', 'S3']

    def collect(self, answer):
        return collect_historical_facts([self.doc], SimpleNamespace(extract=lambda _:copy.deepcopy(answer)))

    def test_claim_only_selections_are_combined_with_context_without_mutating_original_json(self):
        before = copy.deepcopy(self.answer)
        result = self.collect(self.answer)
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['review_findings'], [])
        fact = result['facts'][0]
        self.assertEqual(fact['gemini'], self.answer['verpflichtungen'][0])
        self.assertEqual([c['evidence_id'] for c in fact['derived']['verified_citations']], self.ids)
        self.assertEqual([c['evidence_id'] for c in fact['derived']['context_citations']], self.ids[:1])
        self.assertEqual([c['evidence_id'] for c in fact['derived']['claims'][0]['verified_citations']], self.ids[1:])
        self.assertEqual(fact['derived']['text'], self.doc.pages[0]['text'])
        self.assertEqual(result['gemini_responses'][0]['coverage']['indexed_ids'], self.ids)
        self.assertEqual(self.answer, before)
        self.assertEqual(validate_facts(result, [self.doc]), result)

    def test_empty_additional_reference_list_is_valid_and_claims_alone_cover_targets(self):
        answer = copy.deepcopy(self.answer)
        row = answer['verpflichtungen'][0]
        row['claims'][0]['evidence_ids'] = self.labels
        row['zitate'] = []
        result = self.collect(answer)
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['facts'][0]['derived']['context_citations'], [])
        self.assertEqual(result['gemini_responses'][0]['coverage']['missing_ids'], [])
        self.assertEqual(result['facts'][0]['gemini']['zitate'], [])

    def test_unknown_claims_empty_evidence_missing_targets_and_conflicting_dispositions_still_block(self):
        for kind, code in [('unknown','unknown_evidence_id'),
                           ('empty','missing_or_invalid_claim_evidence'),
                           ('missing','unaccounted_source_passage'),
                           ('conflict','conflicting_passage_disposition'),
                           ('unknown_context','unknown_evidence_id')]:
            answer = copy.deepcopy(self.answer)
            row = answer['verpflichtungen'][0]
            if kind == 'unknown': row['claims'][0]['evidence_ids'].append('invented')
            elif kind == 'empty': row['claims'][0]['evidence_ids'] = []
            elif kind == 'missing': row['claims'][0]['evidence_ids'].pop()
            elif kind == 'conflict': answer['nicht_als_fakt_erfasst'] = [{'evidence_id':self.labels[-1], 'grund':'Excluded'}]
            else: row['zitate'].append({'evidence_id':'invented'})
            result = self.collect(answer)
            with self.subTest(kind=kind):
                self.assertEqual(result['status'], 'incomplete')
                self.assertIn(code, {i['code'] for i in result['review_findings']})
                self.assertEqual(result['gemini_responses'][0]['gemini'], answer)

    def test_cache_and_atomic_recovery_reuse_complete_union_without_model_calls(self):
        calls = []
        answer = self.answer
        class Provider:
            def extract(self, prompt):
                calls.append(prompt)
                self.last_raw_response = json.dumps(answer)
                self.last_finish_reason = 'STOP'
                return copy.deepcopy(answer)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = CachedModel(Provider(), root/'cache', {'model':'fixture'})
            first = collect_historical_facts([self.doc], RequestRecovery(cache, root/'first', [], {'model':'fixture'}))
            self.assertEqual(first['status'], 'recorded')
            self.assertTrue(complete_response(calls[0], answer))
            # A forged old protocol cannot silently rebind short labels as hashes.
            with self.assertRaisesRegex(ValueError, 'IDs do not match'):
                complete_response(calls[0].replace('source-backed-interpretations-v9',
                                                  'source-backed-interpretations-v5'), answer)
            saved = {p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
            recovered = RequestRecovery(cache, root/'retry', [root/'first'], {'model':'fixture'})
            self.assertEqual(collect_historical_facts([self.doc], recovered), first)
            self.assertEqual(recovered.reused, 1)
            self.assertEqual(collect_historical_facts([self.doc], cache), first)
            self.assertEqual(len(calls), 1)
            self.assertEqual(saved, {p:p.read_bytes() for p in saved})

    def test_offline_revalidation_keeps_old_mismatches_and_coverage_findings_in_history(self):
        current = self.collect(self.answer)
        response = current['gemini_responses'][0]
        response['gemini'] = remap_references(response['gemini'], current['reference_map'])
        facts, exclusions, coverage, findings = records(response['gemini'], current['source_documents'],
            target_evidence_ids=response['target_evidence_ids'], response_id=response['id'],
            require_claims=True, allow_context_citations=True)
        old = copy.deepcopy(current)
        old.update(schema_version=9, evidence_protocol='source-backed-interpretations-v5',
                   status='incomplete', facts=facts, excluded_passages=exclusions, review_findings=findings)
        old['gemini_responses'][0]['coverage'] = coverage
        self.assertIn('claim_evidence_not_in_fact_citations', {f['code'] for f in findings})
        self.assertIn('unaccounted_source_passage', {f['code'] for f in findings})
        before = copy.deepcopy(old)
        self.assertEqual(validate_facts(old, [self.doc]), old)
        result = revalidate_context_citations(old, [self.doc])
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['review_findings'], [])
        self.assertEqual(result['validation_history'][-1]['review_findings'], findings)
        self.assertEqual(result['facts'][0]['gemini'], old['facts'][0]['gemini'])
        self.assertEqual(old, before)

    def test_derived_combined_evidence_cannot_be_removed_or_forged(self):
        result = self.collect(self.answer)
        for kind in ('remove','forge'):
            changed = copy.deepcopy(result)
            citations = changed['facts'][0]['derived']['verified_citations']
            if kind == 'remove': citations.pop()
            else: citations[-1]['quote'] = 'Changed original wording'
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_facts(changed, [self.doc])
