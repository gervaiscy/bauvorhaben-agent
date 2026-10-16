import copy
import json
import tempfile
import unittest
from pathlib import Path
from customer_report import fingerprint
from test_timing_assessment import sample
from timing_recovery import recover


class RecoveryTests(unittest.TestCase):
    def setup_run(self, root):
        p,q,a,r,put=sample()
        review={'fixture':'review'}
        manifest={'input':p['input_fingerprint'],'proposal':fingerprint(q),
                  'review':fingerprint(review),'batch_size':2}
        (root/'run.json').write_text(json.dumps(manifest))
        (root/'batch-000.json').write_text(json.dumps({'assessments':a[:2]}))
        return p,q,a,review,manifest

    def test_partial_response_is_not_model_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p,q,a,review,m=self.setup_run(root)
            (root/'batch-001-attempt-0.partial.txt').write_text('{"assessments":[')
            records,result=recover(root,p,q,review)
            self.assertEqual(result['coverage']['assessed'],4)
            self.assertEqual(result['coverage']['model_responses'],2)
            self.assertEqual(result['coverage']['missing_model_responses'],2)
            self.assertEqual(result['coverage']['unresolved'],2)
            self.assertTrue(all(x['review_required'] for x in result['units'][2:]))
            self.assertTrue(all(not x['candidates'] for x in result['units'][2:]))

    def test_raw_response_is_recovered_and_evidence_not_modified(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p,q,a,review,m=self.setup_run(root)
            raw=json.dumps({'assessments':a[2:]})
            f=root/'batch-001-attempt-10.raw.json';f.write_text(raw)
            (root/'batch-001-attempt-9.raw.json').write_text('{}')
            records,result=recover(root,p,q,review)
            self.assertEqual(result['coverage']['model_responses'],4)
            self.assertEqual(f.read_text(),raw)
            self.assertEqual(records,a)

    def test_structural_error_is_local_not_a_silent_success(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p,q,a,review,m=self.setup_run(root)
            a[2]['finding']='BAD'
            (root/'batch-001.json').write_text(json.dumps({'assessments':a[2:]}))
            records,result=recover(root,p,q,review)
            self.assertEqual(result['coverage']['rejected_model_responses'],1)
            self.assertEqual(result['coverage']['model_responses'],4)
            self.assertEqual(records[2]['finding'],'AMBIGUOUS')
            self.assertEqual(records[3],a[3])

    def test_review_cannot_be_reused_on_changed_record_or_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p,q,a,review,m=self.setup_run(root)
            decision={**m,'changes':[{'unit_id':a[0]['unit_id'],'before':fingerprint(a[0]),
                        'reason':'Reviewed clause','citations':a[0]['citations'],'replacement':a[0]}]}
            _,result=recover(root,p,q,review,decision)
            self.assertEqual(result['coverage']['source_review_corrections'],1)
            decision['changes'][0]['before']='stale'
            with self.assertRaisesRegex(ValueError,'Stale'):recover(root,p,q,review,decision)
            with self.assertRaisesRegex(ValueError,'snapshot'):recover(root,p,q,{},decision)

    def test_publication_cannot_approve_missing_model_coverage(self):
        from test_document_report import case
        from document_report import build_document_report,render_document_report,publication_check
        p,q,review=case();q['rules'][0]['id']='price_boundary';review['proposal_fingerprint']=fingerprint(q)
        _,_,a,_,_=sample()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'run.json').write_text(json.dumps({'input':p['input_fingerprint'],
                'proposal':fingerprint(q),'review':fingerprint(review),'batch_size':2}))
            (root/'batch-000.json').write_text(json.dumps({'assessments':a[:2]}))
            _,timing=recover(root,p,q,review)
            report=build_document_report(p,q,review,timing)
            rendered=render_document_report(report)
            self.assertIn('2 fehlen',rendered)
            self.assertIn('Terminprüfung offen',rendered)
            self.assertIn('TIMING_MODEL_COVERAGE_INCOMPLETE',publication_check(report,rendered.encode())['reasons'])

    def test_supplement_fills_only_missing_units_and_preserves_full_context(self):
        from timing_assessment import run_batches
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p,q,a,_,m=self.setup_run(root)
            review={'input_fingerprint':p['input_fingerprint'],'proposal_fingerprint':fingerprint(q),
                    'units':[{'id':u['id'],'timing':[],'category':'CONTRACT_SCOPE'} for u in q['units']]}
            m['review']=fingerprint(review);(root/'run.json').write_text(json.dumps(m))
            calls=[]
            class Model:
                def extract(self,prompt):
                    data=json.JSONDecoder().raw_decode(prompt.split('SOURCE DATA:\n')[1])[0]
                    calls.append(data)
                    return {'assessments':a[2:]}
            def factory():return Model()
            factory.identity={'model':'test'}
            supplement=root/'supplement'
            run_batches(p,q,review,factory,supplement,2,1,[x['unit_id'] for x in a[2:]])
            self.assertEqual(len(calls),1)
            self.assertEqual(len(calls[0]['unit_registry']),4)
            self.assertEqual(len(calls[0]['requested_units']),2)
            _,result=recover(root,p,q,review,supplement=supplement)
            self.assertEqual(result['coverage']['missing_model_responses'],0)
            (root/'batch-001.json').write_text(json.dumps({'assessments':a[2:]}))
            with self.assertRaisesRegex(ValueError,'replace'):recover(root,p,q,review,supplement=supplement)


if __name__=='__main__':unittest.main()
