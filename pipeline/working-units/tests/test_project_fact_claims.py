from historical_facts import collect_historical_facts
from clause_fixtures import remap_references
"""Per-claim source binding, incomplete-response boundaries and historical evidence."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from bauvo_working_units.cache import CachedModel
from bauvo_working_units.fact_artifact import validate_facts
from bauvo_working_units.page_flow import _prompt
from bauvo_working_units.phases.project_facts.full_context import records
from bauvo_working_units.project_facts import collect_project_facts, facts_view, require_current_catalog
from bauvo_working_units.trace import RawRunRecorder
from clause_fixtures import answer_for, entries_for, interpreted
from test_project_facts import document


class FactClaimsTests(unittest.TestCase):
    def setUp(self):
        self.doc = document('contract', 'A conditional credit is shown.\n'
                            'It is paid after final payment and return of the guarantee.\n'
                            'Prior deduction is excluded.')
        self.refs = [{'evidence_id': e['evidence_id']} for e in entries_for(self.doc)]
        row = interpreted(self.refs, 'The credit is paid later and cannot be deducted beforehand.')
        row['claims'] = [
            {'statement': 'The credit requires final payment and guarantee return.',
             'evidence_ids': [r['evidence_id'] for r in self.refs[:2]]},
            {'statement': 'Prior deduction is excluded.', 'evidence_ids': [self.refs[2]['evidence_id']]}]
        self.answer = answer_for(self.doc, [row])

    def collect(self, answer):
        return collect_historical_facts([self.doc], SimpleNamespace(extract=lambda _: copy.deepcopy(answer)))

    def test_each_claim_gets_its_own_exact_evidence_and_survives_all_downstream_projections(self):
        result = self.collect(self.answer)
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['facts'][0]['gemini'], self.answer['verpflichtungen'][0])
        claims = result['facts'][0]['derived']['claims']
        self.assertEqual(claims[0]['text'], ''.join(self.doc.pages[0]['text'].splitlines(keepends=True)[:2]))
        self.assertEqual(claims[1]['text'], 'Prior deduction is excluded.')
        self.assertEqual(validate_facts(result, [self.doc]), result)
        before = copy.deepcopy(result)
        for phase in ('PAGE_EXTRACTION', 'PAGE_OFFERS', 'PAGE_TIMING', 'PAGE_APPLICABILITY'):
            data = json.loads(_prompt(phase, {'source_pages': [], 'project_facts': facts_view(result)}, 'de')
                              .split('SOURCE DATA:\n', 1)[1])
            if phase == 'PAGE_TIMING':
                self.assertEqual(data['project_facts'], {'facts': [{'id': f['id'], 'text': f['text']} for f in facts_view(result)['facts']]})
                continue
            projected = data['project_facts']['facts'][0]['claims']
            if phase == 'PAGE_EXTRACTION':
                self.assertEqual([c['statement'] for c in projected], [c['statement'] for c in claims])
                self.assertEqual([[q['quote'] for q in c['verified_citations']] for c in projected],
                                 [[q['quote'] for q in c['verified_citations']] for c in claims])
            elif phase == 'PAGE_OFFERS':
                self.assertEqual([c['statement'] for c in projected], [c['statement'] for c in claims])
                self.assertEqual([''.join(q['quote'] for q in c['evidence']) for c in projected],
                                 [c['text'] for c in claims])
            else:
                self.assertEqual(projected, claims)
        self.assertEqual(result, before)

    def test_invalid_claims_preserve_raw_values_and_stop_the_batch_becoming_complete(self):
        mutations = [
            ('missing_or_invalid_fact_claims', lambda r: r.pop('claims')),
            ('missing_or_invalid_fact_claims', lambda r: r.update(claims=[])),
            ('missing_or_invalid_fact_claims', lambda r: r.update(claims='not a list')),
            ('invalid_fact_claim', lambda r: r['claims'].__setitem__(0, None)),
            ('missing_or_invalid_claim_statement', lambda r: r['claims'][0].update(statement=' ')),
            ('missing_or_invalid_claim_evidence', lambda r: r['claims'][0].update(evidence_ids=[])),
            ('missing_or_invalid_claim_evidence', lambda r: r['claims'][0].update(evidence_ids='not a list')),
            ('invalid_claim_evidence_reference', lambda r: r['claims'][0].update(evidence_ids=[{}])),
            ('unknown_evidence_id', lambda r: r['claims'][0].update(evidence_ids=['invented'])),
        ]
        for code, mutate in mutations:
            answer = copy.deepcopy(self.answer)
            mutate(answer['verpflichtungen'][0])
            with self.subTest(code=code):
                result = self.collect(answer)
                self.assertEqual(result['status'], 'incomplete')
                self.assertIn(code, {i['code'] for i in result['review_findings']})
                self.assertEqual(result['gemini_responses'][0]['gemini'], answer)
                self.assertEqual(validate_facts(result, [self.doc]), result)

    def test_incomplete_claim_evidence_is_saved_and_replayed_without_automatic_paid_retry(self):
        bad = copy.deepcopy(self.answer)
        bad['verpflichtungen'][0]['claims'][0]['evidence_ids'] = ['invented']
        calls = []
        def provider(prompt):
            calls.append(prompt)
            return copy.deepcopy(bad if len(calls) == 1 else self.answer)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = RawRunRecorder(CachedModel(SimpleNamespace(extract=provider), root/'cache',
                                               {'model': 'fixture'}), root/'raw.json')
            self.assertEqual(collect_historical_facts([self.doc], model)['status'], 'incomplete')
            self.assertEqual(len(list((root/'cache').glob('*.json'))), 1)
            self.assertEqual(collect_historical_facts([self.doc], model)['status'], 'incomplete')
            self.assertEqual(collect_historical_facts([self.doc], model)['status'], 'incomplete')
            self.assertEqual(len(calls), 1)
            self.assertEqual(json.loads((root/'raw.json').read_text()), [bad, bad, bad])

    def test_claim_statements_and_quotations_cannot_be_changed_without_original_response(self):
        result = self.collect(self.answer)
        for field in ('raw_statement', 'raw_evidence', 'derived_statement', 'derived_quote', 'remove_claim'):
            changed = copy.deepcopy(result)
            fact = changed['facts'][0]
            if field == 'raw_statement':
                fact['gemini']['claims'][0]['statement'] = 'Changed meaning'
            elif field == 'raw_evidence':
                fact['gemini']['claims'][0]['evidence_ids'] = [self.refs[2]['evidence_id']]
            elif field == 'derived_statement':
                fact['derived']['claims'][0]['statement'] = 'Changed meaning'
            elif field == 'derived_quote':
                fact['derived']['claims'][0]['verified_citations'][0]['quote'] = 'Changed original'
            else:
                fact['derived']['claims'].pop()
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_facts(changed, [self.doc])

    def test_historical_batched_results_remain_readable_without_inventing_claims(self):
        result = self.collect(self.answer)
        result['gemini_responses'][0]['gemini'] = remap_references(result['gemini_responses'][0]['gemini'], result['reference_map'])
        raw = result['gemini_responses'][0]['gemini']
        for row in raw['verpflichtungen']:
            row.pop('claims')
        response = result['gemini_responses'][0]
        facts, exclusions, coverage, findings = records(raw, result['source_documents'],
            target_evidence_ids=response['target_evidence_ids'], response_id=response['id'])
        result.update(schema_version=7, evidence_protocol='source-backed-interpretations-v3',
                      facts=facts, excluded_passages=exclusions, review_findings=findings)
        response['coverage'] = coverage
        self.assertEqual(validate_facts(result, [self.doc]), result)
        self.assertNotIn('claims', facts_view(result)['facts'][0])
        with self.assertRaisesRegex(ValueError, 'regenerate'):
            require_current_catalog(result)

    def test_valid_ids_do_not_trigger_keyword_filters_or_fabricated_semantic_approval(self):
        # A structurally valid but wrong model interpretation remains reviewable raw evidence.
        answer = copy.deepcopy(self.answer)
        answer['verpflichtungen'][0]['interpretation'] = 'The credit is unconditionally deductible now.'
        answer['verpflichtungen'][0]['claims'][0]['statement'] = 'The credit is unconditionally deductible now.'
        result = self.collect(answer)
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['facts'][0]['gemini'], answer['verpflichtungen'][0])
        self.assertNotIn('semantic_approved', result)
