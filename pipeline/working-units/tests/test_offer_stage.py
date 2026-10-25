import copy
import json
from dataclasses import replace
from types import SimpleNamespace
import unittest

from bauvo_working_units.core import digest
from bauvo_working_units.offer_stage import extract_offers
from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.project_flow import Document
from bauvo_working_units.trace import ModelRequestFailed


class OfferStageTests(unittest.TestCase):
    def setUp(self):
        self.docs = [Document('D1', 'First', 'source.pdf', pages=tuple(
            {'pdf_page': n, 'text': 'Eine Zusatzleistung kann beauftragt werden.'} for n in range(1, 6)),
            schema_version=6, source_key='source', source_snapshot='snapshot'),
            Document('D2', 'Second', 'source.pdf', pages=({'pdf_page': 6, 'text': 'Ein weiteres Angebot.'},),
                     schema_version=6, source_key='source', source_snapshot='snapshot')]
        def tasks(prompt):
            data = json.loads(prompt.split('SOURCE DATA:\n')[1])
            p = next(p for p in data['source_pages'] if p['primary'])
            return {'working_units': [{'name': 'Aufgabe', 'action': 'Aufgabe', 'actor': None,
                    'performed_by': None, 'citations': [{'pdf_page': p['pdf_page'], 'quote': p['text']}]}],
                    'carry_forward': 'Original context'}
        self.baseline = extract_page_units(self.docs, SimpleNamespace(extract=tasks), stop_after='PAGE_EXTRACTION')

    def test_only_offers_are_requested_and_tasks_facts_and_original_responses_are_unchanged(self):
        original = copy.deepcopy(self.baseline)
        calls = []
        def offers(prompt):
            self.assertTrue(prompt.startswith('PAGE_OFFERS.'))
            data = json.loads(prompt.split('SOURCE DATA:\n')[1]); calls.append(data)
            self.assertEqual(data['prior_context'], 'Original context')
            p = next(p for p in data['source_pages'] if p['primary'])
            return {'offers': [{'name': 'Angebot', 'provider': None, 'description': p['text'],
                    'terms': [], 'citations': [{'pdf_page': p['pdf_page'], 'quote': p['text']}]}]}
        result = extract_offers(self.baseline, self.docs, SimpleNamespace(extract=offers))
        self.assertEqual([([p['pdf_page'] for p in d['source_pages']]) for d in calls], [[1,2,3], [3,4,5], [6]])
        self.assertEqual(result['working_units'], original['working_units'])
        self.assertEqual(result['project_facts'], original['project_facts'])
        self.assertEqual(result['gemini_responses'][:3], original['gemini_responses'])
        self.assertEqual(self.baseline, original)
        self.assertEqual(len(result['offers']), 3)
        self.assertEqual(result['completed_passes'], ['PAGE_EXTRACTION', 'PAGE_OFFERS'])
        ids = [r['id'] for r in result['gemini_responses']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(result['review_findings'], [])

    def test_source_or_window_changes_fail_before_model_calls(self):
        def fail(prompt):
            self.fail('No model call should occur')
        model = SimpleNamespace(extract=fail)
        with self.assertRaisesRegex(ValueError, 'source pages'):
            extract_offers(self.baseline, [replace(self.docs[0], source_snapshot='changed'), self.docs[1]], model)
        with self.assertRaisesRegex(ValueError, 'window size'):
            extract_offers(self.baseline, self.docs, model, chunk_pages=2)
        changed = copy.deepcopy(self.baseline)
        changed['source_snapshots'][0]['document_sha256'] = digest([])
        with self.assertRaises(ValueError): extract_offers(changed, self.docs, model)

    def test_unresolved_offer_failure_preserves_prior_tasks_and_successful_offers(self):
        count = 0
        def offers(prompt):
            nonlocal count
            count += 1
            if count == 2: raise ModelRequestFailed([{'error_type': 'ReadTimeout'}])
            return {'offers': []}
        result = extract_offers(self.baseline, self.docs, SimpleNamespace(extract=offers))
        self.assertEqual(count, 3)
        self.assertEqual(result['status'], 'request_failed')
        self.assertEqual(result['working_units'], self.baseline['working_units'])
        self.assertTrue(any(f['code']=='model_request_failed' and f['pass']=='PAGE_OFFERS'
                            for f in result['review_findings']))

    def test_later_passes_or_failed_aufgaben_are_not_silently_repeated(self):
        def fail(prompt): self.fail('No model call should occur')
        for field, value in [('completed_passes', ['PAGE_EXTRACTION','PAGE_OFFERS']),
                             ('review_findings', [{'code':'model_request_failed','pass':'PAGE_EXTRACTION'}])]:
            changed = copy.deepcopy(self.baseline); changed[field] = value
            with self.assertRaises(ValueError):
                extract_offers(changed, self.docs, SimpleNamespace(extract=fail))
