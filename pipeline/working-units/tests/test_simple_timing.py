import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bauvo_working_units.page_flow import extract_page_units, assessment_findings
from bauvo_working_units.project_flow import Document
from bauvo_working_units.timing_stage import extract_timing
from bauvo_working_units.phases.consequences.stage import extract_consequences
from bauvo_working_units.applicability_stage import extract_applicability
from bauvo_working_units.schemas import RESPONSE_SCHEMAS, timing_findings
from bauvo_working_units.trace import RequestCheckpoints
from bauvo_working_units.project_facts import collect_project_facts

class SimpleTimingTests(unittest.TestCase):
    def test_current_schema_has_only_answer_and_citations(self):
        row=RESPONSE_SCHEMAS['PAGE_TIMING']['properties']['assessments']['items']
        self.assertEqual(set(row['properties']),{'unit_id','answer','citations'})
        self.assertEqual(set(row['required']),set(row['properties']))
        self.assertFalse(row['additionalProperties'])

    def test_answer_validation_and_target_checks(self):
        good={'unit_id':'U1','answer':'Two weeks before start','citations':[{'pdf_page':1,'quote':'source'}]}
        self.assertEqual(assessment_findings({'assessments':[good]},['U1'],'PAGE_TIMING'),[])
        for answer in ('NOT_STATED','AMBIGUOUS'):
            self.assertEqual(timing_findings({'assessments':[{**good,'answer':answer,'citations':[]}]}),[])
        issues=timing_findings({'assessments':[{**good,'citations':[]}]})
        self.assertEqual(issues[0]['code'],'timing_answer_without_citation')
        self.assertTrue(timing_findings({'assessments':[{**good,'answer':None}]}))
        self.assertTrue(assessment_findings({'assessments':[good,good]},['U1','U2'],'PAGE_TIMING'))

    def test_timing_citations_raw_and_downstream_preservation_without_calculating_dates(self):
        text='Supply water two weeks before construction.'
        docs=[Document('D1','Contract','contract.pdf',schema_version=6,source_key='source',
            source_snapshot='hash',pages=({'pdf_page':1,'text':text},))]
        def earlier(prompt):
            if prompt.startswith('PAGE_EXTRACTION.'):
                return {'working_units':[{'action':text,'name':'Supply water','actor':None,'performed_by':None,
                    'citations':[{'pdf_page':1,'quote':text}]}],'carry_forward':''}
            return {'offers':[]}
        facts=collect_project_facts([],SimpleNamespace(extract=lambda p: self.fail('No sources to collect')))
        baseline=extract_page_units(docs,SimpleNamespace(extract=earlier),stop_after='PAGE_OFFERS',project_facts=facts)
        original=copy.deepcopy(baseline)
        def timing(prompt):
            data=json.loads(prompt.split('SOURCE DATA:\n')[1])
            return {'assessments':[{'unit_id':data['working_units'][0]['id'],'answer':'Two weeks before construction',
                'citations':[{'pdf_page':1,'quote':text}]}]}
        with patch('bauvo_working_units.phases.timing.date_calculation.calculate_dates',side_effect=AssertionError('Do not infer dates')):
            result=extract_timing(baseline,docs,SimpleNamespace(extract=timing))
        review=copy.deepcopy(result['working_units'][0]['reviews'][0])
        self.assertNotIn('date_calculations',review['derived'])
        self.assertEqual(result['review_findings'],[])
        self.assertEqual(result['gemini_responses'][-1]['gemini']['assessments'][0],review['gemini'])
        self.assertEqual(baseline,original)
        def consequence(prompt):
            data=json.loads(prompt.split('SOURCE DATA:\n')[1]);return {'assessments':[
                {'unit_id':data['working_units'][0]['id'],'status':'NOT_STATED','consequences':[],'citations':[]}]}
        extended=extract_consequences(result,docs,SimpleNamespace(extract=consequence))
        def applicable(prompt):
            data=json.loads(prompt.split('SOURCE DATA:\n')[1]);return {'assessments':[
                {'unit_id':data['working_units'][0]['id'],'applicability':'APPLIES','reason':'Source obligation'}]}
        final=extract_applicability(extended,docs,SimpleNamespace(extract=applicable))
        self.assertEqual(final['working_units'][0]['reviews'][0],review)
        def incorrect(prompt):
            response=timing(prompt);response['assessments'][0]['citations'][0]['quote']='Not in the source';return response
        invalid=extract_timing(baseline,docs,SimpleNamespace(extract=incorrect))
        self.assertIn('citation_quote_not_found',{f['code'] for f in invalid['review_findings']})

    def test_legacy_structured_checkpoints_are_not_replayed(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);trace=root/'old'/'working-units.trace';trace.mkdir(parents=True)
            prompt='PAGE_TIMING. same prompt'
            (trace/'0001-prompt.txt').write_text(prompt)
            (trace/'0001-response.json').write_text(json.dumps({'assessments':[
                {'unit_id':'U1','status':'NOT_STATED','timing_rules':[],'missing_evidence':[]}]}))
            (trace/'0001-attempt.json').write_text(json.dumps({'status':'recorded','finish_reason':'STOP',
                'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()}))
            cache=RequestCheckpoints(None,root/'new',{'model':'test'},root/'old')
            self.assertNotIn(prompt,cache.saved)
