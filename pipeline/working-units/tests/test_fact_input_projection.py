import copy
import json
import unittest
from bauvo_working_units.page_flow import _prompt

class FactInputProjectionTests(unittest.TestCase):
    def test_only_two_fields_and_all_quotes_without_mutating_artifacts(self):
        payload={'source_pages':[], 'project_facts':{'status':'needs_review',
            'source_snapshots':[{'hash':'x'}], 'excluded_sources':[], 'review_findings':[{'code':'issue'}],
            'facts':[{'id':'F1','gemini':{'verpflichtung':'An obligation',
                'zitate':[{'pdf_page':1,'text':'First quote'},{'pdf_page':2,'text':'Second quote'}]},
                'review_findings':[{'code':'issue'}]}]}}
        original=copy.deepcopy(payload)
        for phase in ('PAGE_TIMING','PAGE_APPLICABILITY','PAGE_EXTRACTION','PAGE_OFFERS','PAGE_CONSEQUENCES'):
            with self.subTest(phase=phase):
                actual=json.loads(_prompt(phase,payload,'de').split('SOURCE DATA:\n')[1])
                self.assertEqual(actual['project_facts'],{'facts':[
                    {'verpflichtung':'An obligation','text':'First quote\nSecond quote'}]})
                self.assertEqual(payload,original)

    def test_anchors_are_local_only_for_every_pass(self):
        payload={'source_pages':[], 'supported_anchors':[{'event':'Signature','date':'2026-10-25'}]}
        original=copy.deepcopy(payload)
        for phase in ('PAGE_TIMING','PAGE_APPLICABILITY','PAGE_EXTRACTION','PAGE_OFFERS','PAGE_CONSEQUENCES','PAGE_PROJECT_FACTS'):
            with self.subTest(phase=phase):
                prompt=_prompt(phase,payload,'de')
                self.assertNotIn('supported_anchors',prompt)
                self.assertEqual(payload,original)

    def test_source_snapshot_is_local_only_for_every_pass(self):
        payload={'source_key':'source', 'source_snapshot':'original-hash', 'source_pages':[]}
        original=copy.deepcopy(payload)
        for phase in ('PAGE_TIMING','PAGE_APPLICABILITY','PAGE_EXTRACTION','PAGE_OFFERS','PAGE_CONSEQUENCES','PAGE_PROJECT_FACTS'):
            with self.subTest(phase=phase):
                data=json.loads(_prompt(phase,payload,'de').split('SOURCE DATA:\n')[1])
                self.assertNotIn('source_snapshot',data)
                self.assertEqual(payload,original)

    def test_primary_pages_list_is_local_but_extraction_keeps_overlap_markers(self):
        payload={'primary_pages':[2], 'source_pages':[
            {'pdf_page':1,'text':'Overlap','primary':False},
            {'pdf_page':2,'text':'New page','primary':True}]}
        original=copy.deepcopy(payload)
        for phase in ('PAGE_TIMING','PAGE_APPLICABILITY','PAGE_EXTRACTION','PAGE_OFFERS','PAGE_CONSEQUENCES','PAGE_PROJECT_FACTS'):
            with self.subTest(phase=phase):
                data=json.loads(_prompt(phase,payload,'de').split('SOURCE DATA:\n')[1])
                self.assertNotIn('primary_pages',data)
                if phase in ('PAGE_EXTRACTION','PAGE_OFFERS'):
                    self.assertEqual(data['source_pages'],payload['source_pages'])
                self.assertEqual(payload,original)

    def test_timing_request_includes_simple_question_uncertainty_and_evidence(self):
        prompt=_prompt('PAGE_TIMING',{'source_pages':[], 'working_units':[]},'de')
        self.assertIn('Wann muss jede aufgelistete Aufgabe laut dem Quellauszug und den Vertragsfakten erledigt werden?',prompt)
        for heading in ('Rolle:', 'Kontext:', 'Aufgabe:', 'Ausgabe:'):
            self.assertIn(heading,prompt)
        self.assertIn('eine Bewertung pro aufgelisteter Aufgabe',prompt)
        self.assertIn('Ergänze keine Zeitvorgaben.',prompt)
        self.assertIn('Wenn keine genannt ist: NOT_STATED.',prompt)
        self.assertIn('Wenn die Zuordnung zur Aufgabe unklar ist: AMBIGUOUS.',prompt)
        self.assertIn('Belege jede Zeitvorgabe mit einem wörtlichen Zitat und ihrer Quelle.',prompt)
