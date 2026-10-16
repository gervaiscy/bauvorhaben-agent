import copy
import unittest
from apply_review import apply_review
from customer_report import fingerprint
from test_customer_report import fixture

class ReviewTests(unittest.TestCase):
    def setUp(self):
        _, self.payload, self.proposal = fixture()
        self.review = {'input_fingerprint': self.payload['input_fingerprint'],
          'base_proposal_fingerprint': fingerprint(self.proposal), 'reviewer': 'Test reviewer', 'reason': 'Evidence review',
          'patch': {'unit_updates': [], 'additional_units': [], 'merges': [],
                    'rules': copy.deepcopy(self.proposal['rules']), 'unresolved_questions': []},
          'required_rules': [copy.deepcopy(self.proposal['rules'][0])]}

    def test_required_timing_cannot_disappear_during_repair(self):
        self.review['patch']['rules'] = []
        with self.assertRaisesRegex(ValueError, 'required rule'): apply_review(self.payload, self.proposal, self.review)

    def test_review_is_bound_and_keeps_original_immutable(self):
        before = copy.deepcopy(self.proposal)
        updated, report, audit = apply_review(self.payload, self.proposal, self.review)
        self.assertEqual(self.proposal, before)
        self.assertEqual(report['status'], 'DRAFT')
        self.assertFalse(audit['publication_approved'])
        self.proposal['units'][0]['name'] = 'Changed after review'
        with self.assertRaisesRegex(ValueError, 'another proposal'): apply_review(self.payload, self.proposal, self.review)

    def test_question_removal_requires_attributed_reason(self):
        self.review['resolved_questions'] = [{'question': 'invented', 'reason': 'remove'}]
        with self.assertRaisesRegex(ValueError, 'original question'): apply_review(self.payload, self.proposal, self.review)

    def test_project_notes_require_exact_source_evidence(self):
        self.review['project_notes'] = [{'text': 'A claim', 'citations': [{'source_key':'blb','line_ids':['L1'],'quote':'Invented'}]}]
        with self.assertRaisesRegex(ValueError, 'SOURCE_INVALID'): apply_review(self.payload, self.proposal, self.review)

    def test_summary_escapes_review_text(self):
        from customer_report import render_summary
        self.review['project_notes']=[{'text':'<script>bad()</script>','citations':copy.deepcopy(self.proposal['units'][0]['citations'])}]
        _,report,_=apply_review(self.payload,self.proposal,self.review)
        rendered=render_summary(report)
        self.assertNotIn('<script>',rendered)
        self.assertIn('&lt;script&gt;',rendered)

    def test_cli_writes_bound_artifacts_and_refuses_overwrite(self):
        import json,subprocess,sys,tempfile
        from pathlib import Path
        source,_,_=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            manifest={'contract_signed':'2025-07-03','report_settings':self.payload['settings'],
                      'sources':[{'key':'blb','working_units':'units.json','pages':'pages.json'}]}
            for name,value in [('units.json',source.assembled),('lines.json',list(source.lines.values())),
                               ('pages.json',__import__('page_test_support').pages_fixture(list(source.lines.values()))),('proposal.json',self.proposal),
                               ('review.json',self.review),('manifest.json',manifest)]:
                (root/name).write_text(json.dumps(value))
            from enrich_working_units import load_manifest
            from customer_report import build_input
            signed,loaded=load_manifest(root/'manifest.json')
            current=build_input(loaded,signed,self.payload['settings'])
            self.proposal['input_fingerprint']=current['input_fingerprint']
            self.review['input_fingerprint']=current['input_fingerprint']
            self.review['base_proposal_fingerprint']=fingerprint(self.proposal)
            (root/'proposal.json').write_text(json.dumps(self.proposal))
            (root/'review.json').write_text(json.dumps(self.review))
            cmd=[sys.executable,str(Path(__file__).with_name('apply_review.py')),'--manifest',str(root/'manifest.json'),
                 '--proposal',str(root/'proposal.json'),'--review-patch',str(root/'review.json'),'--output-dir',str(root/'result')]
            run=subprocess.run(cmd,capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertTrue((root/'result/customer-summary.html').exists())
            audit=json.loads((root/'result/review-audit.json').read_text())
            self.assertEqual(audit['base_proposal_fingerprint'],fingerprint(self.proposal))
            self.assertNotEqual(subprocess.run(cmd,capture_output=True).returncode,0)
