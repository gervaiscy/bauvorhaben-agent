import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from customer_report import fingerprint
from document_report import build_document_report, render_document_report, publication_check
from test_customer_report import fixture


def case():
    _, payload, proposal = fixture()
    cs=proposal['units'][0]['citations']
    def statement(text):return {'text':text,'citations':copy.deepcopy(cs)}
    review={'input_fingerprint':payload['input_fingerprint'],'proposal_fingerprint':fingerprint(proposal),
      'reviewer':'Source reviewer','review_basis':'Full supplied clauses; no project progress.',
      'units':[{'id':u['id'],'unit_fingerprint':fingerprint(u),'category':'CONTRACT_SCOPE',
          'reason':'Source comparison','responsibility':statement('Bauherr'),
          'performance':statement('Bauherr beauftragt den Planer'),
          'timing':[statement('Vor der Wandplanung')], 'qualifications':[],
          'consequences':[dict(statement('Fehlende Voraussetzung der Wandplanung'),kind='PLANNING_IMPLICATION')]}
          for u in proposal['units']], 'overview':[statement('Relative Fristen bleiben relativ')],
          'document_gaps':[], 'coverage_limitations':[]}
    return payload,proposal,review


def rebind(proposal,review):
    review['proposal_fingerprint']=fingerprint(proposal)
    units={u['id']:u for u in proposal['units']}
    for a in review['units']:a['unit_fingerprint']=fingerprint(units[a['id']])


class DocumentReportTests(unittest.TestCase):
    def test_live_events_never_affect_document_report(self):
        payload,proposal,review=case()
        before=build_document_report(payload,proposal,review)
        payload['settings']['events']={'construction_start':{'date':'not-a-date','verified':False}}
        payload['input_fingerprint']=fingerprint({k:v for k,v in payload.items() if k!='input_fingerprint'})
        proposal['input_fingerprint']=payload['input_fingerprint']
        review['input_fingerprint']=payload['input_fingerprint']
        rebind(proposal,review)
        after=build_document_report(payload,proposal,review)
        self.assertEqual(render_document_report(before),render_document_report(after))
        self.assertNotIn('schedules',after)
        self.assertNotIn('questions',after)
        self.assertNotIn('2026-02-27',render_document_report(after))

    def test_missing_start_keeps_relative_deadline_without_customer_question(self):
        payload,proposal,review=case()
        proposal['rules'].append(dict(proposal['rules'][0],id='ready',target='unit:start',anchor='event:actual_start',
            offset={'unit':'weeks','min':46,'max':46},kind='CONTRACTUAL'))
        review['units'][-1]['timing'][0]['text']='46 Wochen nach Baubeginn'
        rebind(proposal,review)
        rendered=render_document_report(build_document_report(payload,proposal,review))
        self.assertIn('46 Wochen nach Baubeginn',rendered)
        self.assertNotIn('Zeitanker',rendered)
        self.assertNotIn('Alternative verbindlich auswählen',rendered)

    def test_option_cannot_be_published_as_mandatory(self):
        payload,proposal,review=case()
        for u in proposal['units']:u['requirements']=[]
        proposal['units'][0]['kind']='OPTION'
        rebind(proposal,review)
        with self.assertRaisesRegex(ValueError,'option'):build_document_report(payload,proposal,review)
        review['units'][0]['category']='OPTION'
        self.assertIn('Wahlmöglichkeit',render_document_report(build_document_report(payload,proposal,review)))

    def test_conditional_rule_is_visible_without_proving_occurrence(self):
        payload,proposal,review=case()
        for u in proposal['units']:u['requirements']=[]
        proposal['units'][0]['scope'].update(status='UNKNOWN',condition='Falls Zusatzleistungen beauftragt werden')
        review['units'][0]['category']='CONDITIONAL'
        rebind(proposal,review)
        r=build_document_report(payload,proposal,review)
        self.assertIn('Falls Zusatzleistungen',render_document_report(r))
        review['units'][0]['category']='CONTRACT_SCOPE'
        with self.assertRaisesRegex(ValueError,'Unresolved scope'):build_document_report(payload,proposal,review)

    def test_full_review_coverage_and_snapshot_required(self):
        p,proposal,review=case()
        review['units'].pop()
        with self.assertRaisesRegex(ValueError,'exactly one'):build_document_report(p,proposal,review)
        p,proposal,review=case();proposal['units'][0]['action']='Changed claim'
        with self.assertRaisesRegex(ValueError,'another snapshot'):build_document_report(p,proposal,review)
        p,proposal,review=case();p['source_registry']['blb']['L1']['text']='Altered source'
        with self.assertRaisesRegex(ValueError,'snapshot was modified'):build_document_report(p,proposal,review)

    def test_unknown_scope_quotes_are_also_checked(self):
        p,proposal,review=case()
        for u in proposal['units']:u['requirements']=[]
        u=proposal['units'][0];u['scope'].update(status='UNKNOWN',condition='If required',
             condition_citations=[{'source_key':'blb','line_ids':['L1'],'quote':'Not actually in source'}])
        review['units'][0]['category']='CONDITIONAL';rebind(proposal,review)
        with self.assertRaisesRegex(ValueError,'does not match'):build_document_report(p,proposal,review)

    def test_consequence_requires_evidence_and_type(self):
        p,proposal,review=case()
        review['units'][0]['consequences'][0]['citations']=[]
        with self.assertRaisesRegex(ValueError,'citations'):build_document_report(p,proposal,review)
        p,proposal,review=case();review['units'][0]['consequences'][0]['kind']='AUTOMATIC_FINE'
        with self.assertRaisesRegex(ValueError,'distinguish'):build_document_report(p,proposal,review)

    def test_untrusted_text_is_escaped_and_no_overdue_label(self):
        p,proposal,review=case();review['overview'][0]['text']='<script>alert(1)</script>'
        rendered=render_document_report(build_document_report(p,proposal,review))
        self.assertIn('&lt;script&gt;',rendered);self.assertNotIn('<script>alert',rendered)
        self.assertNotIn('Überfällig',rendered)

    def test_no_self_approval_stale_approval_or_unchecked_html(self):
        p,proposal,review=case();r=build_document_report(p,proposal,review);b=render_document_report(r).encode()
        self.assertFalse(publication_check(r,b)['publishable'])
        approval={'report_digest':r['digest'],'artifact_sha256':hashlib.sha256(b).hexdigest(),
            'reviewed_by':'External qualified reviewer','reviewed_at':'2026-10-12','qualification':'Construction contract review',
            'decision_reason':'Exact report checked','approved':True,'visual_qa_passed':True}
        self.assertTrue(publication_check(r,b,approval)['publishable'])
        self.assertFalse(publication_check(r,b+b'altered',approval)['publishable'])
        approval['reviewed_by']=r['reviewer']
        self.assertIn('INDEPENDENT_REVIEW_REQUIRED',publication_check(r,b,approval)['reasons'])
        approval['reviewed_by']='External qualified reviewer';approval['visual_qa_passed']=False
        self.assertIn('VISUAL_QA_REQUIRED',publication_check(r,b,approval)['reasons'])

    def test_cli_writes_review_draft_and_preserves_existing_output(self):
        p,proposal,review=case()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,value in [('input',p),('proposal',proposal),('review',review)]:
                (root/(name+'.json')).write_text(json.dumps(value))
            cmd=[sys.executable,str(Path(__file__).with_name('document_report.py')),
                 '--input',str(root/'input.json'),'--proposal',str(root/'proposal.json'),
                 '--review',str(root/'review.json'),'--output-dir',str(root/'result')]
            run=subprocess.run(cmd,capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            status=json.loads((root/'result/publication-status.json').read_text())
            self.assertFalse(status['publishable'])
            self.assertNotEqual(subprocess.run(cmd,capture_output=True).returncode,0)

if __name__=='__main__':unittest.main()
