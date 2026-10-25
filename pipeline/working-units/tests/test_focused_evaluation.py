import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

BASE=Path(__file__).resolve().parents[1]/'evaluation'
sys.path.insert(0,str(BASE))
import focused_evaluation as evaluation


class FocusedEvaluationTests(unittest.TestCase):
    def test_all_existing_synthetic_cases_are_recorded_without_expected_values_or_repairs(self):
        cases=list(BASE.glob('*.json'))
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in cases}
        calls=[]
        class Model:
            def extract(self,prompt):
                data=json.loads(prompt.split('SOURCE DATA:\n')[1]);kind=prompt.split('.',1)[0]
                calls.append((kind,data))
                if kind=='PAGE_PROJECT_FACTS':return {'verpflichtungen':[]}
                if kind=='PAGE_EXTRACTION':
                    return {'working_units':[{'name':'Task','dedupe_key':'task','action':'Do task','actor':'Owner',
                           'performed_by':None,'modality':'REQUIRED','citations':[]}],'offers':[],'carry_forward':''}
                return {'assessments':[{'unit_id':u['id'],'unchanged':'Provider output','citations':[{'pdf_page':999,'quote':'bad citation'}]} for u in data['working_units']]}
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'evaluation'
            summary=evaluation.run(output,'unused',model=Model())
            self.assertEqual(len(summary),sum(len(json.loads((BASE/name).read_text())['cases'])
                for name in ('cases.json','applicability-cases.json','timing-cases.json')))
            self.assertTrue(all(row['status']=='needs_review' for row in summary))
            for kind,data in calls:
                self.assertIn(kind,('PAGE_PROJECT_FACTS','PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'))
                self.assertLessEqual(len(data['source_pages']),5)
                self.assertFalse(set(data)&{'expected_units','expectations','forbidden_inferences','checks'})
                self.assertLessEqual(len(data.get('working_units',[])),5)
            for path in output.rglob('raw.json'):
                raw=json.loads(path.read_text());folder=path.parent
                if (folder/'working-units.json').exists():
                    result=json.loads((folder/'working-units.json').read_text())
                    self.assertEqual(raw,[r['gemini'] for r in result['gemini_responses']])
                else:
                    result=json.loads((folder/'result.json').read_text())
                    self.assertTrue(result['semantic_review_required'])
                    self.assertEqual(raw,[r['gemini'] for r in result['responses']])
            with self.assertRaises(ValueError):evaluation.run(output,'unused',model=Model())
        self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in cases})

    def test_selected_case_preserves_partial_result_after_two_failed_attempts(self):
        from bauvo_working_units.gemini import IncompleteResponse
        calls=[]
        class Model:
            last_raw_response=None
            last_finish_reason=None
            def extract(self,prompt):
                calls.append(prompt)
                self.last_raw_response='{unfinished'
                self.last_finish_reason='MAX_TOKENS'
                raise IncompleteResponse('unfinished_generation','MAX_TOKENS')
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'evaluation'
            summary=evaluation.run(output,'unused',model=Model(),case_ids=['unknown-material-exclusion'])
            self.assertEqual(len(summary),1);self.assertEqual(summary[0]['status'],'request_failed')
            self.assertEqual(calls,[calls[0],calls[0]])
            folder=output/'unknown-material-exclusion'
            result=json.loads((folder/'result.json').read_text())
            self.assertEqual(result['status'],'request_failed');self.assertEqual(result['responses'],[])
            self.assertEqual(len(result['review_findings'][0]['attempts']),2)
            self.assertEqual(json.loads((folder/'raw.json').read_text()),['{unfinished','{unfinished'])

    def test_unknown_selection_fails_before_any_model_request(self):
        class Model:
            def extract(self,prompt):raise AssertionError('Must not call model')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'Unknown or unavailable'):
                evaluation.run(Path(tmp)/'output','unused',model=Model(),case_ids=['typo'])
