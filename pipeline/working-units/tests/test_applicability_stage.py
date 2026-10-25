import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bauvo_working_units.applicability_stage import extract_applicability, main
from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.project_facts import collect_project_facts
from bauvo_working_units.project_flow import Document
from bauvo_working_units.trace import ModelRequestFailed


class ApplicabilityStageTests(unittest.TestCase):
    def setUp(self):
        self.text='Der Auftraggeber stellt Bauwasser bereit.'
        self.docs=[Document('D1','Vertrag','source.pdf',schema_version=6,
                   source_key='source',source_snapshot='snapshot',
                   pages=({'pdf_page':1,'text':self.text},))]
        self.citation={'pdf_page':1,'quote':self.text}
        facts=collect_project_facts(self.docs,SimpleNamespace(extract=lambda prompt:{
            'verpflichtungen':[{'verpflichtung':self.text,'zitate':[{'pdf_page':1,'text':self.text}]}]}))
        def earlier(prompt):
            data=json.loads(prompt.split('SOURCE DATA:\n')[1])
            if prompt.startswith('PAGE_EXTRACTION.'):
                return {'working_units':[{'action':'Bauwasser bereitstellen','actor':'Auftraggeber',
                        'performed_by':None,'citations':[self.citation]}],'carry_forward':''}
            if prompt.startswith('PAGE_OFFERS.'):return {'offers':[]}
            return {'assessments':[{'unit_id':u['id'],'status':'NOT_STATED',
                                   'timing_rules':[],'missing_evidence':[]} for u in data['working_units']]}
        self.baseline=extract_page_units(self.docs,SimpleNamespace(extract=earlier),
                                        project_facts=facts,stop_after='PAGE_TIMING')

    def test_only_applicability_runs_with_facts_and_keeps_earlier_results_unchanged(self):
        original=copy.deepcopy(self.baseline);calls=[]
        def model(prompt):
            self.assertTrue(prompt.startswith('PAGE_APPLICABILITY.'))
            data=json.loads(prompt.split('SOURCE DATA:\n')[1]);calls.append(data)
            self.assertEqual(len(data['project_facts']['facts']),1)
            return {'assessments':[{'unit_id':data['working_units'][0]['id'],
                    'applicability':'APPLIES','reason':'Vertraglich vereinbart',
                    'applicability_rules':[],'missing_evidence':[],
                    'modality':'REQUIRED','modality_citations':[self.citation]}]}
        result=extract_applicability(self.baseline,self.docs,SimpleNamespace(extract=model))
        self.assertEqual(len(calls),1)
        self.assertEqual(result['completed_passes'],original['completed_passes']+['PAGE_APPLICABILITY'])
        self.assertEqual(result['gemini_responses'][:-1],original['gemini_responses'])
        self.assertEqual(result['working_units'][0]['reviews'][:-1],original['working_units'][0]['reviews'])
        self.assertEqual(result['project_facts'],original['project_facts'])
        self.assertEqual(self.baseline,original)
        self.assertEqual(result['review_findings'],[])

    def test_bad_prefix_and_changed_fact_sources_fail_before_model_calls(self):
        for kind in ('prefix','facts'):
            changed=copy.deepcopy(self.baseline)
            if kind=='prefix':changed['completed_passes'].pop()
            else:changed['project_facts']['source_snapshots'][0]['pages_sha256']='changed'
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                extract_applicability(changed,self.docs,SimpleNamespace(extract=lambda p:self.fail('Paid call')))

    def test_failed_requests_and_missing_assessments_remain_visible(self):
        def fail(prompt):raise ModelRequestFailed([{'error_type':'ReadTimeout'}])
        result=extract_applicability(self.baseline,self.docs,SimpleNamespace(extract=fail))
        self.assertEqual(result['status'],'request_failed')
        result=extract_applicability(self.baseline,self.docs,SimpleNamespace(extract=lambda p:{'assessments':[]}))
        self.assertTrue(any(f['code']=='missing_review' for f in result['review_findings']))

    def test_cli_does_not_overwrite_previous_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'working-units.json';output.write_text('original')
            argv=['applicability_stage','--working-units','unused','--pages','unused',
                  '--documents','unused','--project','unused','--output',str(output)]
            with patch('sys.argv',argv),patch('sys.stderr',new=io.StringIO()),self.assertRaises(SystemExit):main()
            self.assertEqual(output.read_text(),'original')
