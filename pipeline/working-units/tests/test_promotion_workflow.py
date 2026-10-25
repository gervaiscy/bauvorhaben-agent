"""Empty extraction and optional activities remain provider-owned, with no text filters."""
import copy
import json
from types import SimpleNamespace
import unittest

from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.project_flow import Document


class PromotionWorkflowTests(unittest.TestCase):
    def test_empty_extraction_is_preserved_without_synthetic_units_or_assessment_calls(self):
        doc=Document('D1','Brochure','synthetic.pdf',schema_version=6,
            pages=({'pdf_page':1,'text':'Wir begleiten Sie auf dem Weg zum Eigenheim.'},))
        answer={'working_units':[],'offers':[],'carry_forward':'Introduction'}
        calls=[]
        def extract(prompt):
            calls.append(prompt.split('.',1)[0]);return copy.deepcopy(answer)
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
            kind=prompt.split('.',1)[0];calls.append(kind);data=json.loads(prompt.split('SOURCE DATA:\n')[1])
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
