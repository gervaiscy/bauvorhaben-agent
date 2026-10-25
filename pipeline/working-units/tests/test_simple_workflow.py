import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from test_schema6_flow import fixture, load, payload
from bauvo_working_units.project_flow import extract_project_units
from bauvo_working_units.page_flow import assessment_findings
from bauvo_working_units.schemas import RESPONSE_SCHEMAS


class SimpleWorkflowTests(unittest.TestCase):
    def run_case(self, count=1, citation=None, response_mode=None):
        calls, answers = [], []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root, 7, [[1, 2, 3, 4, 5, 6], [7]])
            original = {str(p): p.read_bytes() for p in root.glob('*.json')}
            def extract(prompt):
                p = payload(prompt);kind = prompt.split('.', 1)[0]
                calls.append((kind, copy.deepcopy(p)))
                if kind == 'PAGE_PROJECT_FACTS':
                    response = {'verpflichtungen': []}
                elif kind == 'PAGE_EXTRACTION':
                    rows = [{'name': 'Coordinate work', 'dedupe_key': str(i), 'action': f'Owner task {i}',
                             'actor': 'Owner', 'performed_by': 'Installer', 'modality': 'REQUIRED',
                             'citations': copy.deepcopy(citation if citation is not None else [])}
                            for i in range(count)]
                    response = {'working_units': rows, 'offers': [], 'carry_forward': 'previous source'}
                elif kind == 'PAGE_OFFERS':
                    response = {'offers': []}
                else:
                    rows = []
                    for unit in p['working_units']:
                        self.assertEqual(unit['actor'], 'Owner')
                        self.assertEqual(unit['performed_by'], 'Installer')
                        self.assertNotIn('reviews', unit)
                        self.assertNotIn('validation_feedback', p)
                        self.assertNotIn('source_exclusion_candidates', unit)
                        self.assertNotIn('local_source_context', unit)
                        rows.append({'unit_id': unit['id'], 'unverified_interpretation': 'Preserve this',
                                     'citations': [{'pdf_page': 999, 'quote': 'not on any page'}]})
                    if response_mode == 'missing':rows = []
                    if response_mode == 'unknown' and rows:rows[0]['unit_id'] = 'not-requested'
                    response = {'assessments': rows}
                answers.append(copy.deepcopy(response))
                return response
            result = extract_project_units(load(root), SimpleNamespace(extract=extract))
            self.assertEqual(original, {str(p): p.read_bytes() for p in root.glob('*.json')})
        return result, calls, answers

    def test_only_five_passes_and_document_local_overlap(self):
        result, calls, _ = self.run_case()
        self.assertEqual(set(k for k, _ in calls), set(RESPONSE_SCHEMAS))
        self.assertEqual(len(RESPONSE_SCHEMAS), 6)
        self.assertEqual([w['pages'] for w in result['windows']], [[1,2,3], [3,4,5], [5,6], [7]])
        for _, p in calls:
            self.assertLessEqual(len(p['source_pages']), 5)
            if p.get('document', {}).get('id') == 'D2':self.assertEqual([x['pdf_page'] for x in p['source_pages']], [7])
        first=[p for k,p in calls if k=='PAGE_EXTRACTION' and p['document']['id']=='D2'][0]
        self.assertEqual(first['prior_context'], '');self.assertEqual(first['known_items'], [])

    def test_applicability_uses_whole_window_while_other_assessments_keep_five_unit_batches(self):
        result, calls, _ = self.run_case(12)
        for kind in ('PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'):
            first=[p for k,p in calls if k==kind and [x['pdf_page'] for x in p['source_pages']]==[1,2,3]]
            self.assertEqual([len(p['working_units']) for p in first], [12] if kind=='PAGE_APPLICABILITY' else [5,5,2])
            for p in first:self.assertEqual([x['pdf_page'] for x in p['source_pages']], [1,2,3])
        self.assertTrue(all(len(u['reviews'])==3 for u in result['working_units']))

    def test_missing_or_invalid_citations_never_filter_units_or_trigger_repairs(self):
        for citations in ([], [{'pdf_page':999,'quote':'invented'}], [{'quote':'broken shape'}]):
            with self.subTest(citations=citations):
                result, calls, _ = self.run_case(citation=citations)
                self.assertEqual(result['status'], 'needs_review')
                self.assertTrue(result['review_findings'])
                self.assertEqual(result['citation_validation'], 'page_and_quote_checks')
                self.assertEqual(result['working_units'][0]['gemini']['citations'], citations)
                self.assertTrue(all(len(u['reviews'])==3 for u in result['working_units']))
                self.assertFalse(any('REPAIR' in k for k,_ in calls))
                self.assertNotIn('validation', result['working_units'][0])

    def test_raw_proposals_and_assessments_are_unchanged(self):
        result, _, answers = self.run_case()
        self.assertEqual([r['gemini'] for r in result['gemini_responses']], answers)
        by_id={r['id']:r['gemini'] for r in result['gemini_responses']}
        for u in result['working_units']:
            self.assertEqual(u['gemini'], by_id[u['derived']['response_id']]['working_units'][u['derived']['response_position']])
            for r in u['reviews']:self.assertEqual(r['gemini'],by_id[r['response_id']]['assessments'][r['response_position']])

    def test_structural_failures_remain_visible_without_semantic_retry(self):
        for mode, code in [('missing','missing_review'),('unknown','unknown_review_target')]:
            with self.subTest(mode=mode):
                result,calls,_=self.run_case(response_mode=mode)
                self.assertEqual(result['status'],'needs_review')
                self.assertTrue(any(f['code']==code for f in result['review_findings']))
                self.assertEqual(len(calls),24)  # Four fact windows plus four windows with five task passes.
        self.assertEqual(assessment_findings('bad JSON',['U1']),[{'code':'missing_or_invalid_assessments'}])
        self.assertTrue(any(f['code']=='duplicate_review_target' for f in assessment_findings({'assessments':[{'unit_id':'U1'},{'unit_id':'U1'}]},['U1'])))

    def test_model_schema_keeps_applicability_simple_and_timing_separate(self):
        app=RESPONSE_SCHEMAS['PAGE_APPLICABILITY']['properties']['assessments']['items']['properties']
        self.assertEqual(set(app),{'unit_id','applicability','reason'})
        timing=RESPONSE_SCHEMAS['PAGE_TIMING']['properties']['assessments']['items']['properties']['timing_rules']['items']['anyOf'][0]['properties']
        self.assertNotIn('offset_evidence',timing);self.assertNotIn('anchor_status',timing)
        self.assertIn('conditions',timing);self.assertIn('alternative_group',timing)
