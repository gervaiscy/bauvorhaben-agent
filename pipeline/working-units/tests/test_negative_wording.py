import copy
import json
from types import SimpleNamespace
import unittest

from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.project_flow import Document
from bauvo_working_units.schemas import timing_findings


class NegativeWordingTests(unittest.TestCase):
    def test_negative_wording_never_triggers_application_keyword_filtering(self):
        text='Der Kunde darf die Zufahrt nicht blockieren.'
        doc=Document('D1','Agreement','synthetic.pdf',schema_version=6,pages=({'pdf_page':1,'text':text},))
        proposal={'name':'Zufahrt frei halten','action':'Zufahrt nicht blockieren','actor':'Kunde',
                  'performed_by':'Kunde','modality':'REQUIRED','citations':[{'pdf_page':1,'quote':text}]}
        calls=[];answers=[]
        def extract(prompt):
            kind=prompt.split('.',1)[0];calls.append(kind);data=json.loads(prompt.split('SOURCE DATA:\n')[1])
            if kind=='PAGE_EXTRACTION':out={'working_units':[copy.deepcopy(proposal)],'offers':[],'carry_forward':''}
            elif kind=='PAGE_OFFERS':out={'offers':[]}
            else:
                self.assertEqual(data['working_units'][0]['action'],'Zufahrt nicht blockieren')
                row={'unit_id':data['working_units'][0]['id'],'citations':[]}
                if kind=='PAGE_TIMING':row.update(status='NOT_STATED',timing_rules=[],missing_evidence=[])
                elif kind=='PAGE_APPLICABILITY':row.update(applicability='APPLIES',reason='Contractual duty',applicability_rules=[],missing_evidence=[])
                else:row.update(status='NOT_STATED',consequences=[])
                out={'assessments':[row]}
            answers.append(copy.deepcopy(out));return out
        result=extract_page_units([doc],SimpleNamespace(extract=extract))
        self.assertEqual(calls,['PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'])
        self.assertEqual(result['working_units'][0]['gemini'],proposal)
        self.assertEqual([r['gemini'] for r in result['gemini_responses']],answers)
        self.assertEqual(result['status'],'recorded')

    def test_negatively_worded_positive_constraint_is_not_dropped_by_contract_check(self):
        result={'assessments':[{'unit_id':'U1','status':'EXPLICIT','timing_rules':[{
            'kind':'PREREQUISITE','scope':'Montage','expression':'nicht vor der Freigabe',
            'anchor_events':['Freigabe'],'relation':'AFTER','offset':None,'duration':None,'date':None}], 'missing_evidence':[]}]}
        before=copy.deepcopy(result)
        self.assertEqual(timing_findings(result),[])
        self.assertEqual(result,before)
