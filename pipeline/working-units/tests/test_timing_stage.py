from clause_fixtures import decode_prompt
import copy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bauvo_working_units.page_flow import _view, extract_page_units
from bauvo_working_units.project_flow import Document
from bauvo_working_units.timing_stage import extract_timing, main
from bauvo_working_units.trace import ModelRequestFailed


class TimingStageTests(unittest.TestCase):
    def setUp(self):
        self.text = 'Der Bauherr stellt Bauwasser zwei Wochen vor Baubeginn bereit.'
        self.docs = [Document('D1', 'Agreement', 'source.pdf', schema_version=6,
                     source_key='source', source_snapshot='snapshot',
                     pages=({'pdf_page': 1, 'text': self.text},))]
        def earlier(prompt):
            if prompt.startswith('PAGE_EXTRACTION.'):
                return {'working_units': [{'name':'Bauwasser bereitstellen', 'action':self.text,
                        'actor':'Bauherr', 'performed_by':None,
                        'citations':[{'pdf_page':1,'quote':self.text}]}], 'carry_forward':''}
            return {'offers':[{'name':'Option', 'provider':None,'description':self.text,
                              'terms':[], 'citations':[{'passage':'P1-1'}]}]}
        self.baseline = extract_page_units(self.docs, SimpleNamespace(extract=earlier), stop_after='PAGE_OFFERS')

    def test_only_timing_runs_and_prior_proposals_offers_and_raw_responses_stay_unchanged(self):
        original = copy.deepcopy(self.baseline)
        calls = []
        def timing(prompt):
            self.assertTrue(prompt.startswith('PAGE_TIMING.'))
            data = decode_prompt(prompt); calls.append(data)
            return {'assessments':[{'unit_id':u['id'],'status':'NOT_STATED','timing_rules':[],
                                    'missing_evidence':[]} for u in data['working_units']]}
        result = extract_timing(self.baseline, self.docs, SimpleNamespace(extract=timing))
        self.assertEqual(len(calls),1)
        self.assertEqual(result['working_units'][0]['gemini'], original['working_units'][0]['gemini'])
        self.assertEqual(result['offers'], original['offers'])
        self.assertEqual(result['project_facts'], original['project_facts'])
        self.assertEqual(result['gemini_responses'][:2], original['gemini_responses'])
        self.assertEqual(result['completed_passes'], ['PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING'])
        self.assertEqual(self.baseline, original)
        self.assertEqual(result['review_findings'], [])

    def test_relative_deadline_without_supported_date_stays_unresolved(self):
        def timing(prompt):
            data = decode_prompt(prompt)
            rule = {'kind':'EVENT_RELATIVE','scope':'Bauwasser bereitstellen',
                    'expression':'zwei Wochen vor Baubeginn','conditions':[], 'alternative_group':None,
                    'anchor_events':['Baubeginn'],'relation':'BEFORE','offset':'zwei Wochen',
                    'duration':None,'date':None,'calculation':None,
                    'citations':[{'pdf_page':1,'quote':self.text}]}
            return {'assessments':[{'unit_id':data['working_units'][0]['id'], 'status':'EXPLICIT',
                                    'timing_rules':[rule],'missing_evidence':[]}]}
        result = extract_timing(self.baseline, self.docs, SimpleNamespace(extract=timing))
        calc = result['working_units'][0]['reviews'][0]['derived']['date_calculations'][0]
        self.assertEqual(calc['status'],'UNRESOLVED');self.assertIsNone(calc['date'])

    def test_invalid_anchor_or_incomplete_prefix_fails_before_model_calls(self):
        def fail(prompt): self.fail('Must validate before a model call')
        model = SimpleNamespace(extract=fail)
        with self.assertRaises(ValueError):
            extract_timing(self.baseline, self.docs, model, anchors=[{'id':'invented'}])
        changed = copy.deepcopy(self.baseline);changed['completed_passes']=['PAGE_EXTRACTION']
        with self.assertRaises(ValueError): extract_timing(changed,self.docs,model)

    def test_failure_and_missing_assessments_remain_visible_without_changing_input(self):
        original = copy.deepcopy(self.baseline)
        def fail(prompt): raise ModelRequestFailed([{'error_type':'ReadTimeout'}])
        result = extract_timing(self.baseline,self.docs,SimpleNamespace(extract=fail))
        self.assertEqual(result['status'],'request_failed')
        self.assertEqual(self.baseline,original)
        result = extract_timing(self.baseline,self.docs,SimpleNamespace(extract=lambda p:{'assessments':[]}))
        self.assertTrue(any(f['code']=='missing_review' for f in result['review_findings']))

    def test_cli_refuses_to_overwrite_existing_outputs_before_loading_or_calling_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'working-units.json';output.write_text('original evidence')
            argv=['timing-stage','--working-units','unused','--pages','unused','--documents','unused',
                  '--project','unused','--output',str(output)]
            with patch('sys.argv',[*argv,'--max-new-calls','100']),patch('sys.stderr',new=io.StringIO()),self.assertRaises(SystemExit):
                main()
            self.assertEqual(output.read_text(),'original evidence')


class QuotedTaskTimingHandoffTests(unittest.TestCase):
    def setUp(self):
        # Changed words remain unresolved even with whitespace-tolerant binding.
        self.quotations = [
            {'title': f'Anschluss {n} bereitstellen', 'actor': 'Bauherr' if n else None,
             'quote': f'Der Bauherr stellt Anschluss {n} zwei Wochen vor Baubeginn bereit.'}
            for n in range(6)]
        self.exact = {'title': 'Lagerplatz bereitstellen', 'actor': 'Bauherr',
                      'quote': 'Der Bauherr stellt einen Lagerplatz bereit.'}
        first = '\n\n'.join(q['quote'].replace(' stellt ', ' hält ').replace(' zwei Wochen', '\nzwei Wochen')
                             for q in self.quotations) + '\n\n' + self.exact['quote']
        self.second = {'title': 'Planung besprechen', 'actor': 'Beide Parteien',
                       'quote': 'Beide Parteien besprechen die Planung.'}
        self.docs = [Document('D1', 'Fiktive Vereinbarung', 'source.pdf', schema_version=6,
            source_key='source', pages=({'pdf_page': 1, 'text': first},
                                       {'pdf_page': 2, 'text': self.second['quote']}))]
        def earlier(prompt):
            data = decode_prompt(prompt)
            if prompt.startswith('PAGE_OFFERS.'):
                return {'offers': []}
            return {'working_units': copy.deepcopy(
                self.quotations + [self.exact] if data['source_pages'][0]['pdf_page'] == 1
                else [self.second])}
        self.baseline = extract_page_units(self.docs, SimpleNamespace(extract=earlier),
            chunk_pages=1, overlap_pages=0, stop_after='PAGE_OFFERS')

    def test_every_task_reaches_timing_with_original_window_and_honest_evidence_status(self):
        before = copy.deepcopy(self.baseline)
        calls = []
        def timing(prompt):
            self.assertTrue(prompt.startswith('PAGE_TIMING.'))
            data = decode_prompt(prompt); calls.append(data)
            return {'assessments': [{'unit_id': u['id'], 'answer': 'NOT_STATED',
                                     'citations': []} for u in data['working_units']]}
        result = extract_timing(self.baseline, self.docs, SimpleNamespace(extract=timing),
                                chunk_pages=1, overlap_pages=0)
        self.assertEqual(self.baseline, before)
        self.assertEqual(len(calls), 3)
        views = [u for data in calls for u in data['working_units']]
        self.assertCountEqual([u['id'] for u in views], [u['id'] for u in before['working_units']])
        self.assertTrue(all(len(data['working_units']) <= 5 for data in calls))
        for data in calls:
            page = data['source_pages'][0]
            self.assertEqual(page['text'], self.docs[0].pages[page['pdf_page'] - 1]['text'])
        for n, unit in enumerate(before['working_units']):
            view = next(v for v in views if v['id'] == unit['id'])
            self.assertEqual(view['action'], unit['gemini']['quote'])
            self.assertEqual(view['actor'], unit['gemini']['actor'])
            if n < 6:
                self.assertEqual(view['action_source'], 'unverified_task_quote')
                self.assertEqual(view['citations'], [])
                self.assertNotIn('source_text', result['working_units'][n]['derived'])
            else:
                self.assertNotIn('action_source', view)
                self.assertTrue(view['citations'])
        for old, new in zip(before['working_units'], result['working_units']):
            self.assertEqual(old['gemini'], new['gemini'])
            self.assertEqual(old['derived']['citations'], new['derived']['citations'])
            self.assertEqual(len(new['reviews']), 1)
        self.assertEqual(result['offers'], before['offers'])
        self.assertEqual(result['project_facts'], before['project_facts'])
        self.assertEqual(result['gemini_responses'][:len(before['gemini_responses'])], before['gemini_responses'])
        self.assertEqual(result['review_findings'][:len(before['review_findings'])], before['review_findings'])
        self.assertEqual(sum(f['code'] == 'assessment_context_unresolved'
                             for f in result['review_findings']), 6)

    def test_proposed_quote_never_becomes_validated_evidence_or_uses_the_generated_title(self):
        task = copy.deepcopy(self.baseline['working_units'][0])
        task['gemini']['quote'] = 'Eine vom Modell veränderte Aufgabenbeschreibung.'
        before = copy.deepcopy(task)
        view = _view(task)
        self.assertEqual(view['action'], task['gemini']['quote'])
        self.assertNotEqual(view['action'], task['gemini']['title'])
        self.assertEqual(view['action_source'], 'unverified_task_quote')
        self.assertEqual(view['citations'], [])
        self.assertNotIn('source_text', task['derived'])
        self.assertEqual(task, before)

    def test_unusable_task_is_reported_instead_of_silently_skipped(self):
        for gemini in (None, {'title': 'Nur ein Titel', 'actor': None, 'quote': ''}):
            with self.subTest(gemini=gemini):
                baseline = copy.deepcopy(self.baseline)
                task = baseline['working_units'][0]
                task['gemini'] = gemini
                seen = []
                def timing(prompt):
                    data = decode_prompt(prompt)
                    seen.extend(u['id'] for u in data['working_units'])
                    return {'assessments': [{'unit_id': u['id'], 'answer': 'NOT_STATED',
                                             'citations': []} for u in data['working_units']]}
                result = extract_timing(baseline, self.docs, SimpleNamespace(extract=timing),
                                        chunk_pages=1, overlap_pages=0)
                self.assertNotIn(task['id'], seen)
                self.assertCountEqual(seen, [u['id'] for u in baseline['working_units'][1:]])
                self.assertIn({'unit_id': task['id'], 'pass': 'PAGE_TIMING',
                               'code': 'assessment_task_unusable'}, result['review_findings'])
                self.assertEqual(result['status'], 'needs_review')

    def test_same_task_view_keeps_later_assessment_stages_from_skipping_unbound_quotes(self):
        def assess(prompt):
            data = decode_prompt(prompt)
            fields = ({'status': 'NOT_STATED', 'consequences': [], 'citations': []}
                      if prompt.startswith('PAGE_CONSEQUENCES.') else
                      {'applicability': 'UNCLEAR', 'reason': 'Fiktiver Transporttest', 'citations': []}
                      if prompt.startswith('PAGE_APPLICABILITY.') else
                      {'answer': 'NOT_STATED', 'citations': []})
            return {'assessments': [{'unit_id': u['id'], **fields} for u in data['working_units']]}
        for stop, expected in [('PAGE_CONSEQUENCES', ['PAGE_TIMING', 'PAGE_CONSEQUENCES']),
                               ('PAGE_APPLICABILITY', ['PAGE_APPLICABILITY'])]:
            with self.subTest(stop=stop):
                result = extract_page_units(self.docs, SimpleNamespace(extract=assess),
                    chunk_pages=1, overlap_pages=0, stop_after=stop,
                    aufgaben_result=self.baseline, project_facts=self.baseline['project_facts'])
                self.assertTrue(all([r['kind'] for r in u['reviews']] == expected
                                    for u in result['working_units']))
