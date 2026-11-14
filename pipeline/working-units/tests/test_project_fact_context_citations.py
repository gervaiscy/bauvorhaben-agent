from historical_facts import collect_historical_facts
from clause_fixtures import remap_references
"""Context references stay separate from claim evidence, with offline revalidation."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from bauvo_working_units.cache import CachedModel
from bauvo_working_units.core import digest
from bauvo_working_units.fact_artifact import validate_facts
from bauvo_working_units.page_flow import _prompt
from bauvo_working_units.phases.project_facts.full_context import records
from bauvo_working_units.phases.project_facts.index import complete_response
from bauvo_working_units.phases.project_facts.revalidation import revalidate_context_citations
from bauvo_working_units.project_facts import collect_project_facts, facts_view
from bauvo_working_units.trace import ModelRequestFailed
from clause_fixtures import answer_for, entries_for, interpreted
from test_project_facts import document


class FactContextCitationTests(unittest.TestCase):
    def setUp(self):
        self.doc = document('contract', 'Section: qualified assurance\n'
                            'The guarantee covers completion without significant defects.\n'
                            'It must be delivered before the second instalment is due.')
        self.entries = entries_for(self.doc)
        row = interpreted([{'evidence_id':e['evidence_id']} for e in self.entries])
        row['claims'][0]['evidence_ids'] = [e['evidence_id'] for e in self.entries[1:]]
        self.answer = answer_for(self.doc, [row])

    def collect(self, answer):
        return collect_historical_facts([self.doc], SimpleNamespace(extract=lambda _:copy.deepcopy(answer)))

    def historical(self, answer):
        result = self.collect(answer)
        response = result['gemini_responses'][0]
        response['gemini'] = remap_references(response['gemini'], result['reference_map'])
        facts, exclusions, coverage, findings = records(response['gemini'], result['source_documents'],
            target_evidence_ids=response['target_evidence_ids'], response_id=response['id'], require_claims=True)
        result.update(schema_version=8, evidence_protocol='source-backed-interpretations-v4',
                      facts=facts, excluded_passages=exclusions, review_findings=findings,
                      status='incomplete' if findings else 'recorded')
        response['coverage'] = coverage
        return validate_facts(result, [self.doc])

    def test_extra_context_is_preserved_without_assigning_it_to_any_claim(self):
        result = self.collect(self.answer)
        self.assertEqual(result['status'], 'recorded')
        fact = result['facts'][0]
        self.assertEqual(fact['gemini'], self.answer['verpflichtungen'][0])
        self.assertEqual(fact['derived']['context_citations'], fact['derived']['verified_citations'][:1])
        self.assertEqual(fact['derived']['claims'][0]['verified_citations'], fact['derived']['verified_citations'][1:])
        self.assertEqual(fact['derived']['text'], self.doc.pages[0]['text'])
        self.assertEqual(result['gemini_responses'][0]['coverage']['missing_ids'], [])
        for phase in ('PAGE_EXTRACTION', 'PAGE_OFFERS', 'PAGE_TIMING', 'PAGE_CONSEQUENCES', 'PAGE_APPLICABILITY'):
            data = json.loads(_prompt(phase, {'source_pages':[], 'project_facts':facts_view(result)}, 'de')
                              .split('SOURCE DATA:\n',1)[1])
            projected = data['project_facts']['facts'][0]
            if phase == 'PAGE_EXTRACTION':
                self.assertEqual([c['quote'] for c in projected['context_citations']],
                                 [c['quote'] for c in fact['derived']['context_citations']])
                self.assertEqual([[c['quote'] for c in claim['verified_citations']] for claim in projected['claims']],
                                 [[c['quote'] for c in claim['verified_citations']] for claim in fact['derived']['claims']])
            else:
                self.assertEqual(projected['context_citations'], fact['derived']['context_citations'])
                self.assertEqual(projected['claims'], fact['derived']['claims'])
        self.assertEqual(validate_facts(result, [self.doc]), result)

    def test_context_policy_is_generic_and_does_not_classify_by_heading_keywords(self):
        doc = document('source', 'Attachment identifier A7\nA concrete provision.')
        entries = entries_for(doc)
        row = interpreted([{'evidence_id':e['evidence_id']} for e in entries])
        row['claims'][0]['evidence_ids'] = [entries[1]['evidence_id']]
        result = collect_historical_facts([doc], SimpleNamespace(extract=lambda _:answer_for(doc,[row])))
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['facts'][0]['derived']['context_citations'][0]['quote'], 'Attachment identifier A7\n')

    def test_unknown_duplicate_missing_claim_evidence_and_missing_targets_still_block(self):
        for kind, code in [('unknown_context','unknown_evidence_id'),
                           ('missing_claim_evidence','missing_or_invalid_claim_evidence'),
                           ('missing_target','unaccounted_source_passage')]:
            answer = copy.deepcopy(self.answer)
            row = answer['verpflichtungen'][0]
            if kind == 'unknown_context': row['zitate'].append({'evidence_id':'invented'})
            elif kind == 'duplicate_context': row['zitate'].append(copy.deepcopy(row['zitate'][0]))
            elif kind == 'missing_claim_evidence': row['claims'][0]['evidence_ids'] = []
            else: row['zitate'].pop(0)
            result = self.collect(answer)
            with self.subTest(kind=kind):
                self.assertEqual(result['status'], 'incomplete')
                self.assertIn(code, {f['code'] for f in result['review_findings']})
                self.assertEqual(result['gemini_responses'][0]['gemini'], answer)
                self.assertEqual(validate_facts(result, [self.doc]), result)

    def test_new_cache_policy_accepts_context_but_old_policy_remains_strict(self):
        calls = []
        def provider(prompt):
            calls.append(prompt)
            return copy.deepcopy(self.answer)
        with TemporaryDirectory() as tmp:
            cache = CachedModel(SimpleNamespace(extract=provider), Path(tmp)/'cache', {'model':'fixture'})
            first = collect_historical_facts([self.doc], cache)
            saved = {p:p.read_bytes() for p in (Path(tmp)/'cache').glob('*.json')}
            self.assertEqual(collect_historical_facts([self.doc], cache), first)
            self.assertEqual(len(calls), 1)
            self.assertTrue(complete_response(calls[0], self.answer))
            with self.assertRaisesRegex(ValueError, 'IDs do not match'):
                complete_response(calls[0].replace('source-backed-interpretations-v9',
                                                  'source-backed-interpretations-v4'), self.answer)
            self.assertEqual(saved, {p:p.read_bytes() for p in saved})

    def test_context_provenance_cannot_be_removed_changed_or_moved_into_claims(self):
        result = self.collect(self.answer)
        for kind in ('remove','change','claim_link'):
            changed = copy.deepcopy(result)
            meta = changed['facts'][0]['derived']
            if kind == 'remove': meta['context_citations'].clear()
            elif kind == 'change': meta['context_citations'][0]['quote'] = 'Changed context'
            else: meta['claims'][0]['verified_citations'].append(copy.deepcopy(meta['context_citations'][0]))
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_facts(changed, [self.doc])

    def test_offline_revalidation_archives_old_findings_and_preserves_original_raw_and_sources(self):
        old = self.historical(self.answer)
        before = copy.deepcopy(old)
        self.assertEqual({f['code'] for f in old['review_findings']}, {'fact_citation_without_claim'})
        new = revalidate_context_citations(old, [self.doc])
        self.assertEqual(new['status'], 'recorded')
        self.assertEqual(new['review_findings'], [])
        self.assertEqual(new['gemini_responses'][0]['gemini'], old['gemini_responses'][0]['gemini'])
        self.assertEqual(new['source_documents'], old['source_documents'])
        self.assertEqual(new['source_snapshots'], old['source_snapshots'])
        history = new['validation_history'][-1]
        self.assertEqual(history['source_artifact_sha256'], digest(old))
        self.assertEqual(history['review_findings'], old['review_findings'])
        self.assertEqual(old, before)
        self.assertNotIn('semantic_approved', new)
        self.assertEqual(validate_facts(old, [self.doc]), old)

    def test_revalidation_does_not_retire_other_errors_or_user_review_findings(self):
        answer = copy.deepcopy(self.answer)
        answer['verpflichtungen'][0]['claims'][0]['evidence_ids'].append('invented')
        invalid = self.historical(answer)
        new = revalidate_context_citations(invalid, [self.doc])
        self.assertEqual(new['status'], 'incomplete')
        self.assertIn('unknown_evidence_id', {i['code'] for i in new['review_findings']})
        old = self.historical(self.answer)
        note = {'code':'unresolved_interpretation', 'fact_id':old['facts'][0]['id']}
        old['review_findings'].append(note)
        old['facts'][0]['review_findings'].append(note)
        reviewed = revalidate_context_citations(old, [self.doc])
        self.assertEqual(reviewed['status'], 'needs_review')
        self.assertIn(note, reviewed['review_findings'])
        self.assertIn(note, reviewed['facts'][0]['review_findings'])
        tampered = copy.deepcopy(old)
        tampered['source_documents'][0]['pages'][0]['text'] = 'Changed source'
        with self.assertRaises(ValueError): revalidate_context_citations(tampered, [self.doc])

    def test_revalidation_preserves_failed_requests(self):
        def fail(_): raise ModelRequestFailed([{'finish_reason':'MAX_TOKENS'}])
        old = collect_historical_facts([self.doc], SimpleNamespace(extract=fail))
        old.update(schema_version=8, evidence_protocol='source-backed-interpretations-v4')
        new = revalidate_context_citations(old, [self.doc])
        self.assertEqual(new['status'], 'request_failed')
        self.assertEqual(new['gemini_responses'], old['gemini_responses'])
