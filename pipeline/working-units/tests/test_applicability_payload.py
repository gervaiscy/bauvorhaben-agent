"""Applicability sees source content, complete window units and facts, not app state."""
import copy
import json
from types import SimpleNamespace
import unittest

from bauvo_working_units.applicability_stage import extract_applicability
from bauvo_working_units.page_flow import _prompt, extract_page_units
from bauvo_working_units.project_facts import collect_project_facts
from bauvo_working_units.project_flow import Document


class ApplicabilityPayloadTests(unittest.TestCase):
    def test_metadata_is_removed_only_from_model_input_and_uncertainty_is_kept(self):
        issue={'code':'citation_quote_not_found','fact_id':'F1'}
        fact={'id':'F1','source_key':'contract','document_id':'D1',
              'gemini':{'verpflichtung':'Uncertain agreement','zitate':[{'pdf_page':1,'text':'Original quote'}]},
              'review_findings':[issue]}
        data={'source_key':'blb','document':{'id':'D1','title':'BLB'},
              'source_snapshot':'hash','primary_pages':[2,3],'supported_anchors':[],
              'source_pages':[{'pdf_page':1,'text':'Overlap text','primary':False,'checkboxes':[]},
                              {'pdf_page':2,'text':'New text','primary':True}],
              'working_units':[{'id':'U1','action':'Work','citations':[{'pdf_page':1,'quote':'Overlap text'}]}],
              'project_facts':{'status':'needs_review','facts':[fact],'review_findings':[issue],
                               'source_snapshots':[{'pages_sha256':'hash'}],'excluded_sources':[]}}
        before=copy.deepcopy(data)
        prompt=_prompt('PAGE_APPLICABILITY',data,'de')
        actual=json.loads(prompt.split('SOURCE DATA:\n')[1])
        self.assertEqual(set(actual),{'source_key','document','source_pages','working_units','project_facts'})
        self.assertEqual(actual['source_pages'],[{'pdf_page':1,'text':'Overlap text'},{'pdf_page':2,'text':'New text'}])
        self.assertEqual(actual['working_units'],data['working_units'])
        self.assertEqual(actual['project_facts'],{'facts':[fact],'review_findings':[issue]})
        self.assertNotIn('supported_anchors',prompt)
        self.assertEqual(data,before)
        timing=json.loads(_prompt('PAGE_TIMING',data,'de').split('SOURCE DATA:\n')[1])
        self.assertEqual(timing,data)

    def test_all_window_units_are_sent_once_including_overlap_citations_without_crossing_documents(self):
        docs=[Document('D1','First','first.pdf',schema_version=6,source_key='source',
                       pages=tuple({'pdf_page':n,'text':f'Work {n}.'} for n in range(1,6))),
              Document('D2','Second','first.pdf',schema_version=6,source_key='source',
                       pages=({'pdf_page':6,'text':'Work 6.'},))]
        facts=collect_project_facts([],SimpleNamespace(extract=lambda p:self.fail('No fact calls')))
        def earlier(prompt):
            d=json.loads(prompt.split('SOURCE DATA:\n')[1])
            if prompt.startswith('PAGE_EXTRACTION.'):
                page=d['source_pages'][-1]['pdf_page']
                citations=[3]*6 if page==3 else [3,5] if page==5 else [6]
                return {'working_units':[{'name':f'Work {page}-{i}','action':f'Work {page}-{i}',
                        'actor':None,'performed_by':None,
                        'citations':[{'pdf_page':n,'quote':f'Work {n}.'}]} for i,n in enumerate(citations)],
                        'carry_forward':''}
            if prompt.startswith('PAGE_OFFERS.'):return {'offers':[]}
            return {'assessments':[{'unit_id':u['id'],'status':'NOT_STATED','timing_rules':[],
                                   'missing_evidence':[]} for u in d['working_units']]}
        baseline=extract_page_units(docs,SimpleNamespace(extract=earlier),project_facts=facts,stop_after='PAGE_TIMING')
        original=copy.deepcopy(baseline);calls=[]
        def app(prompt):
            d=json.loads(prompt.split('SOURCE DATA:\n')[1]);calls.append(d)
            return {'assessments':[{'unit_id':u['id'],'applicability':'UNKNOWN','modality':'UNCLEAR',
                                   'reason':'Unresolved','applicability_rules':[],'modality_citations':[],
                                   'missing_evidence':['Selection']} for u in d['working_units']]}
        result=extract_applicability(baseline,docs,SimpleNamespace(extract=app))
        self.assertEqual([len(c['working_units']) for c in calls],[6,2,1])
        self.assertEqual([[p['pdf_page'] for p in c['source_pages']] for c in calls],[[1,2,3],[3,4,5],[6]])
        self.assertEqual(calls[1]['working_units'][0]['citations'][0]['pdf_page'],3)
        ids=[u['id'] for c in calls for u in c['working_units']]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(set(ids),{u['id'] for u in baseline['working_units']})
        self.assertEqual([c['document']['id'] for c in calls],['D1','D1','D2'])
        self.assertEqual(result['source_snapshots'],baseline['source_snapshots'])
        self.assertEqual(result['project_facts'],facts)
        for current,previous in zip(result['working_units'],baseline['working_units']):
            self.assertEqual(current['reviews'][:-1],previous['reviews'])
            self.assertEqual(current['derived']['applicability_window_pages'],previous['derived']['window_pages'])
            self.assertEqual(current['derived']['assessment_window_pages'],previous['derived']['assessment_window_pages'])
        self.assertEqual(baseline,original)
