import copy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bauvo_working_units.page_flow import extract_page_units
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
                              'terms':[], 'citations':[{'pdf_page':1,'quote':self.text}]}]}
        self.baseline = extract_page_units(self.docs, SimpleNamespace(extract=earlier), stop_after='PAGE_OFFERS')

    def test_only_timing_runs_and_prior_proposals_offers_and_raw_responses_stay_unchanged(self):
        original = copy.deepcopy(self.baseline)
        calls = []
        def timing(prompt):
            self.assertTrue(prompt.startswith('PAGE_TIMING.'))
            data = json.loads(prompt.split('SOURCE DATA:\n')[1]); calls.append(data)
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
            data = json.loads(prompt.split('SOURCE DATA:\n')[1])
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
            with patch('sys.argv',argv),patch('sys.stderr',new=io.StringIO()),self.assertRaises(SystemExit):
                main()
            self.assertEqual(output.read_text(),'original evidence')
