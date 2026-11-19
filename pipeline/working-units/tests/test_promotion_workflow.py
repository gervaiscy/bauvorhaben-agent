from consequence_fixtures import assessment_answer
from clause_fixtures import decode_prompt
"""Empty extraction and optional activities remain provider-owned, with no text filters."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.project_flow import Document


class PromotionWorkflowTests(unittest.TestCase):
    def test_assigned_and_included_work_in_a_supplier_directory_is_preserved(self):
        cases=json.loads((Path(__file__).resolve().parents[1]/'evaluation/cases.json').read_text())['cases']
        case=next(c for c in cases if c['id']=='capability-directory-with-project-work')
        units=[{'name':u['action'],'action':u['action'],'actor':u['actor'],
                'performed_by':u['performed_by'],'citations':[
                    {'pdf_page':c['pdf_page'],'quote':c['quote']} for c in u['evidence']]}
               for u in case['expected_units']]
        doc=Document('D1','Synthetic source','synthetic.pdf',schema_version=6,pages=tuple(case['pages']))
        calls=[]
        def extract(prompt):
            kind=prompt.split('.',1)[0]
            payload=decode_prompt(prompt);calls.append(kind)
            if prompt.startswith('PAGE_CONSEQUENCES.'):
                out = assessment_answer(json.loads(prompt.split('SOURCE DATA:\n', 1)[1]))
                return out
            self.assertEqual(payload['source_pages'][0]['text'],case['pages'][0]['text'])
            self.assertFalse(set(payload)&{'expected_units','expected_offers','expectations','forbidden_inferences'})
            if kind=='PAGE_EXTRACTION':return {'working_units':copy.deepcopy(units),'carry_forward':''}
            if kind=='PAGE_OFFERS':return {'offers':[]}
            rows=[]
            for u in payload['working_units']:
                row={'unit_id':u['id']}
                if kind=='PAGE_TIMING':row.update(answer='NOT_STATED',citations=[])
                elif kind=='PAGE_CONSEQUENCES':row.update(status='NOT_STATED',consequences=[],citations=[])
                else:row.update(applicability='APPLIES',reason='Assigned or included work')
                rows.append(row)
            return {'assessments':rows}
        result=extract_page_units([doc],SimpleNamespace(extract=extract))
        self.assertEqual([u['gemini'] for u in result['working_units']],units)
        self.assertEqual(result['offers'],[])
        self.assertEqual(result['review_findings'],[])
        self.assertTrue(all(len(u['reviews'])==3 for u in result['working_units']))
        self.assertEqual(calls,['PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'])

    def test_empty_extraction_is_preserved_without_synthetic_units_or_assessment_calls(self):
        doc=Document('D1','Brochure','synthetic.pdf',schema_version=6,
            pages=({'pdf_page':1,'text':'Wir begleiten Sie auf dem Weg zum Eigenheim.'},))
        answer={'working_units':[],'offers':[],'carry_forward':'Introduction'}
        calls=[]
        def extract(prompt):
            calls.append(prompt.split('.',1)[0])
            return {'offers':[]} if prompt.startswith('PAGE_OFFERS.') else copy.deepcopy(answer)
        result=extract_page_units([doc],SimpleNamespace(extract=extract),chunk_pages=2,overlap_pages=1)
        self.assertEqual(calls,['PAGE_EXTRACTION','PAGE_OFFERS'])
        self.assertEqual(result['working_units'],[]);self.assertEqual(result['offers'],[])
        self.assertEqual(result['gemini_responses'][0]['gemini'],answer)
        self.assertEqual(result['review_findings'],[])

    def test_optional_action_in_promotional_surroundings_is_not_keyword_filtered(self):
        text='Wir machen Wohnträume wahr. Auf Wunsch kann der Kunde eine Lichtplanung beauftragen.'
        unit={'dedupe_key':'choice','name':'Lichtplanung beauftragen','actor':'Kunde',
            'performed_by':'Kunde','action':'Lichtplanung beauftragen',
            'citations':[{'pdf_page':1,'quote':'Auf Wunsch kann der Kunde eine Lichtplanung beauftragen.'}]}
        doc=Document('D1','Brochure','synthetic.pdf',schema_version=6,pages=({'pdf_page':1,'text':text},))
        calls=[];answers=[]
        def extract(prompt):
            kind=prompt.split('.',1)[0];calls.append(kind);data=decode_prompt(prompt)
            if prompt.startswith('PAGE_CONSEQUENCES.'):
                out = assessment_answer(json.loads(prompt.split('SOURCE DATA:\n', 1)[1]))
                answers.append(out)
                return out
            if kind=='PAGE_EXTRACTION':out={'working_units':[copy.deepcopy(unit)],'offers':[],'carry_forward':''}
            elif kind=='PAGE_OFFERS':out={'offers':[]}
            else:
                self.assertNotIn('modality', data['working_units'][0])
                row={'unit_id':data['working_units'][0]['id']}
                if kind=='PAGE_APPLICABILITY':row.update(applicability='CONDITIONAL',modality='OPTIONAL',modality_citations=unit['citations'],reason='Customer choice',applicability_rules=[],missing_evidence=['Choice'])
                elif kind=='PAGE_TIMING':row.update(status='NOT_STATED',timing_rules=[],missing_evidence=[])
                else:row.update(status='NOT_STATED',consequences=[],citations=[])
                out={'assessments':[row]}
            answers.append(copy.deepcopy(out));return out
        result=extract_page_units([doc],SimpleNamespace(extract=extract),chunk_pages=2,overlap_pages=1)
        self.assertEqual(calls,['PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'])
        self.assertEqual(result['working_units'][0]['gemini'],unit)
        self.assertEqual([r['gemini'] for r in result['gemini_responses']],answers)
