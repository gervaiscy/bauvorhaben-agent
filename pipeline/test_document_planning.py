import copy
import unittest
from customer_report import fingerprint
from document_planning import derive_planning
from test_customer_report import fixture

class PlanningTests(unittest.TestCase):
    def setup_case(self):
        _,p,q=fixture()
        return p,q,{'boundary_anchor':'anchor:price','scenarios':[{'id':'base','label':'Price-protection planning','rule_ids':['price','start'],'selections':[]}],'display_aliases':[]}

    def test_backwards_chain_reserves_longer_planning_duration(self):
        p,q,c=self.setup_case();r=derive_planning(p,q,c)
        self.assertEqual(r['boundary'],'2026-07-03')
        row=next(x for x in r['deadlines'] if x['unit_id']=='electrical')
        self.assertEqual(row['date'],'2026-02-27')
        self.assertEqual(row['shorter_duration_date'],'2026-03-27')
        self.assertTrue(row['derivation']);self.assertTrue(row['citations'])

    def test_twelve_week_and_six_week_branches_remain_separate(self):
        p,q,c=self.setup_case()
        q['units'][0]['kind']='OPTION'
        for u in q['units']:u['requirements']=[]
        for rid,amount in [('early',-12),('late',-6)]:
            q['rules'].append(dict(q['rules'][1],id=rid,target='unit:electrical',anchor='anchor:price',
               offset={'unit':'weeks','min':amount,'max':amount},requires_selection='electrical'))
        c['scenarios']=[{'id':rid,'label':rid,'selections':['electrical'],'rule_ids':['price',rid],
                        'display_units':['electrical']} for rid in ['early','late']]
        before=copy.deepcopy(p)
        r=derive_planning(p,q,c)
        self.assertEqual([(x['scenario_id'],x['date']) for x in r['deadlines']],
                         [('early','2026-04-10'),('late','2026-05-22')])
        self.assertTrue(all(x['conditional_branch'] for x in r['deadlines']))
        self.assertEqual(p,before)
        p['contract_signed']='2025-08-03'
        shifted=derive_planning(p,q,c)
        self.assertEqual(shifted['boundary'],'2026-08-03')
        self.assertEqual(shifted['deadlines'][0]['date'],'2026-05-11')

    def test_actual_completion_rules_cannot_become_planning_dates(self):
        p,q,c=self.setup_case()
        q['rules'][1]['anchor']='event:construction_start'
        with self.assertRaisesRegex(ValueError,'actual event'):derive_planning(p,q,c)

    def test_unknown_baseline_and_missing_boundary_fail(self):
        p,q,c=self.setup_case();p['settings']['contract_signed_verified']=False
        with self.assertRaisesRegex(ValueError,'confirmed'):derive_planning(p,q,c)
        p,q,c=self.setup_case();c['boundary_anchor']='anchor:missing'
        with self.assertRaisesRegex(ValueError,'could not'):derive_planning(p,q,c)

    def test_source_tampering_fails(self):
        p,q,c=self.setup_case();q['rules'][0]['citations'][0]['quote']='Invented price guarantee'
        with self.assertRaises(ValueError):derive_planning(p,q,c)

    def test_report_renders_dates_and_keeps_relative_rule(self):
        from test_document_report import case
        from document_report import build_document_report,render_document_report
        p,q,review=case()
        _,_,config=self.setup_case();review['planning']=config
        report=build_document_report(p,q,review)
        rendered=render_document_report(report)
        self.assertIn('Abgeleitete Planungsfrist: 27.02.2026',rendered)
        self.assertIn('03.07.2026',rendered)
        self.assertIn('Vor der Wandplanung',rendered)
        self.assertIn('kein zusätzlicher Puffer',rendered)
        self.assertNotIn('Überfällig',rendered)

if __name__=='__main__':unittest.main()
