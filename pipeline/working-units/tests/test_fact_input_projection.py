from historical_facts import collect_historical_facts
import copy
import json
import unittest
from bauvo_working_units.page_flow import _prompt

class FactInputProjectionTests(unittest.TestCase):
    def test_all_quotes_provenance_and_uncertainty_without_mutating_artifacts(self):
        payload={'source_pages':[], 'project_facts':{'status':'needs_review',
            'source_snapshots':[{'hash':'x'}], 'excluded_sources':[], 'review_findings':[{'code':'issue'}],
            'facts':[{'id':'F1','source_key':'contract','document_id':'D1',
                'citations':[{'source_key':'contract','document_id':'D1','pdf_page':1,'quote':'First quote'},
                             {'source_key':'contract','document_id':'D1','pdf_page':2,'quote':'Second quote'}],
                'gemini':{'verpflichtung':'An obligation',
                'zitate':[{'pdf_page':1,'text':'First quote'},{'pdf_page':2,'text':'Second quote'}]},
                'review_findings':[{'code':'issue'}]}]}}
        original=copy.deepcopy(payload)
        for phase in ('PAGE_TIMING','PAGE_APPLICABILITY','PAGE_EXTRACTION','PAGE_OFFERS'):
            with self.subTest(phase=phase):
                supplied=copy.deepcopy(payload)
                if phase=='PAGE_OFFERS':
                    supplied['project_facts']['source_documents']=[{'source_key':'contract','document_id':'D1',
                        'pages':[{'pdf_page':1,'text':'First quote'},{'pdf_page':2,'text':'Second quote'}]}]
                actual=json.loads(_prompt(phase,supplied,'de').split('SOURCE DATA:\n')[1])
                if phase=='PAGE_TIMING':
                    self.assertEqual(actual['project_facts'], {'facts': [{'id':'F1', 'text':'First quote\n[separate source passage]\nSecond quote'}]})
                    continue
                if phase=='PAGE_OFFERS':
                    self.assertEqual([c['quote'] for c in actual['project_facts']['facts'][0]['evidence']],
                                     ['First quote','Second quote'])
                    self.assertNotIn('source_documents',actual['project_facts'])
                    self.assertEqual(payload,original)
                    continue
                if phase == 'PAGE_EXTRACTION':
                    projected = actual['project_facts']['facts'][0]
                    self.assertEqual([c['quote'] for c in projected['citations']], ['First quote', 'Second quote'])
                    self.assertTrue(all('evidence_id' not in c for c in projected['citations']))
                    self.assertEqual(projected['review_findings'], [{'code':'issue'}])
                    self.assertNotIn('source_documents', actual['project_facts'])
                    self.assertEqual(payload, original)
                    continue
                self.assertEqual(actual['project_facts'],{'facts':[
                    {'id':'F1','text':'First quote\n[separate source passage]\nSecond quote',
                     'source_key':'contract','document_id':'D1','document_title':None,
                     'citations':payload['project_facts']['facts'][0]['citations'],
                     'review_findings':[{'code':'issue'}]}],
                    'excluded_passages':[], 'review_findings':[{'code':'issue'}]})
                self.assertEqual(payload,original)

    def test_anchors_are_local_only_for_every_pass(self):
        payload={'source_pages':[], 'supported_anchors':[{'event':'Signature','date':'2026-10-25'}]}
        original=copy.deepcopy(payload)
        for phase in ('PAGE_TIMING','PAGE_APPLICABILITY','PAGE_EXTRACTION','PAGE_OFFERS','PAGE_PROJECT_FACTS'):
            with self.subTest(phase=phase):
                prompt=_prompt(phase,payload,'de')
                self.assertNotIn('supported_anchors',prompt)
                self.assertEqual(payload,original)

    def test_source_snapshot_is_local_only_for_every_pass(self):
        payload={'source_key':'source', 'source_snapshot':'original-hash', 'source_pages':[]}
        original=copy.deepcopy(payload)
        for phase in ('PAGE_TIMING','PAGE_APPLICABILITY','PAGE_EXTRACTION','PAGE_OFFERS','PAGE_PROJECT_FACTS'):
            with self.subTest(phase=phase):
                data=json.loads(_prompt(phase,payload,'de').split('SOURCE DATA:\n')[1])
                self.assertNotIn('source_snapshot',data)
                self.assertEqual(payload,original)

    def test_primary_pages_list_is_local_but_extraction_keeps_overlap_markers(self):
        payload={'primary_pages':[2], 'source_pages':[
            {'pdf_page':1,'text':'Overlap','primary':False},
            {'pdf_page':2,'text':'New page','primary':True}]}
        original=copy.deepcopy(payload)
        for phase in ('PAGE_TIMING','PAGE_APPLICABILITY','PAGE_EXTRACTION','PAGE_OFFERS','PAGE_PROJECT_FACTS'):
            with self.subTest(phase=phase):
                data=json.loads(_prompt(phase,payload,'de').split('SOURCE DATA:\n')[1])
                self.assertNotIn('primary_pages',data)
                if phase == 'PAGE_EXTRACTION':
                    self.assertEqual([(p['pdf_page'], p['primary'], p['text'])
                                      for p in data['source_pages']], [(1, False, 'Overlap'), (2, True, 'New page')])
                elif phase == 'PAGE_OFFERS':
                    self.assertEqual(data['source_pages'], [{'pdf_page':2,'passages':[{'passage':'P2-1','text':'New page'}]}])
                    self.assertNotIn('source_documents', data)
                self.assertEqual(payload,original)

    def test_timing_request_includes_current_instructions_and_citation_contract(self):
        from bauvo_working_units.page_flow import PASS_RULES
        from bauvo_working_units.schemas import RESPONSE_SCHEMAS
        prompt=_prompt('PAGE_TIMING',{'source_pages':[], 'working_units':[]},'de')
        self.assertIn(PASS_RULES['PAGE_TIMING'].rstrip(),prompt)
        for heading in ('Rolle:', 'Kontext:', 'Ziel:', 'Frage:', 'Ausgabe:'):
            self.assertIn(heading,prompt)
        self.assertEqual(json.loads(prompt.split('SOURCE DATA:\n',1)[1]),
                         {'source_pages':[], 'working_units':[], 'project_facts':{'facts':[]}})
        self.assertEqual(set(RESPONSE_SCHEMAS['PAGE_TIMING']['properties']['assessments']['items']['properties']),
                         {'unit_id','answer','citations'})
        self.assertIn('NOT_STATED',prompt)
        self.assertIn('AMBIGUOUS',prompt)
        self.assertNotIn('evidence_catalog',prompt)
        self.assertIn('fact_id',prompt)
        self.assertIn('pdf_page',prompt)
