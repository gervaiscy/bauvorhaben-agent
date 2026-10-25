"""Verify evaluation isolation and evidence preservation, not model interpretation."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

BASE=Path(__file__).resolve().parents[1]/'evaluation'
sys.path.insert(0,str(BASE))
import applicability_project_evaluation as evaluation
from bauvo_working_units.applicability_stage import extract_applicability
from bauvo_working_units.gemini import IncompleteResponse


class ApplicabilityProjectEvaluationTests(unittest.TestCase):
    def test_only_applicability_is_called_and_expectations_are_not_sent(self):
        calls=[];answers=[]
        def extract(prompt):
            self.assertTrue(prompt.startswith('PAGE_APPLICABILITY.'))
            data=json.loads(prompt.split('SOURCE DATA:\n')[1]);calls.append(data)
            self.assertNotIn('expectations',data)
            self.assertNotIn('expected',data)
            answer={'assessments':[{'unit_id':u['id'],'applicability':'UNKNOWN','modality':'UNCLEAR',
                    'reason':'Unresolved provider interpretation','applicability_rules':[],
                    'modality_citations':[],'missing_evidence':['Provider uncertainty']}
                    for u in data['working_units']]}
            answers.append(copy.deepcopy(answer));return answer
        cases=json.loads(evaluation.CASES.read_text())['cases']
        self.assertEqual(len(cases),9)
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'results'
            summary=evaluation.run(output,'unused',model=SimpleNamespace(extract=extract))
            self.assertEqual(len(calls),len(cases))
            for case,answer,row in zip(cases,answers,summary):
                folder=output/case['id']
                result=json.loads((folder/'result.json').read_text())
                self.assertEqual(result['gemini_responses'][-1]['gemini'],answer)
                self.assertEqual(json.loads((folder/'raw.json').read_text()),[answer])
                self.assertEqual(json.loads((folder/'expectations.json').read_text()),case['expectations'])
                self.assertTrue(all(c['status_fields_match']==
                    (c['actual']['applicability']==c['expected']['applicability']) for c in row['comparison']))
            self.assertTrue(json.loads((output/'integrity.json').read_text())['inputs_and_code_unchanged'])
            with self.assertRaises(FileExistsError):evaluation.run(output,'unused',model=SimpleNamespace(extract=extract))

    def test_page_collision_requires_original_quote_and_contract_identity(self):
        case=next(c for c in json.loads(evaluation.CASES.read_text())['cases']
                  if c['id']=='same-page-different-source')
        baseline,docs,context=evaluation.prepare(case)
        original=copy.deepcopy(baseline)
        for mode in ('original','summary','wrong-source'):
            def extract(prompt):
                data=json.loads(prompt.split('SOURCE DATA:\n')[1]);fact=data['project_facts']['facts'][0]
                quote=fact['gemini']['verpflichtung'] if mode=='summary' else fact['gemini']['zitate'][0]['text']
                citation={'pdf_page':1,'quote':quote,'source_key':docs[0].source_key if mode=='wrong-source'
                          else fact['source_key'],'document_id':fact['document_id']}
                return {'assessments':[{'unit_id':data['working_units'][0]['id'],'applicability':'APPLIES',
                        'modality':'REQUIRED','reason':'Source evidence','modality_citations':[citation],
                        'applicability_rules':[{'effect':'APPLIES_WHEN','scope':'Aufheizen',
                          'condition':'Fußbodenheizung vereinbart','replacement_action':None,'citations':[citation]}],
                        'missing_evidence':[]}]}
            result=extract_applicability(baseline,docs,SimpleNamespace(extract=extract),context_documents=context)
            codes={f['code'] for f in result['review_findings']}
            if mode=='original':self.assertEqual(codes,set())
            else:self.assertIn('citation_quote_not_found',codes)
            self.assertEqual(baseline,original)

    def test_incomplete_response_retries_once_and_does_not_stop_later_cases(self):
        calls=[]
        class Model:
            last_raw_response=None
            last_finish_reason=None
            def extract(self,prompt):
                calls.append(prompt)
                if len(calls)<=2:
                    self.last_raw_response='{unfinished'
                    self.last_finish_reason='MAX_TOKENS'
                    raise IncompleteResponse('unfinished_generation','MAX_TOKENS')
                self.last_raw_response='{"assessments": []}'
                self.last_finish_reason='STOP'
                return {'assessments':[]}
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'results';summary=evaluation.run(output,'unused',model=Model())
            self.assertEqual(len(calls),10)
            self.assertEqual(calls[0],calls[1])
            self.assertEqual(summary[0]['status'],'request_failed')
            self.assertEqual(len(summary),9)
            self.assertEqual(summary[0]['assessment_count'],0)
            folder=output/summary[0]['case']
            self.assertEqual(json.loads((folder/'raw.json').read_text()),['{unfinished','{unfinished'])
