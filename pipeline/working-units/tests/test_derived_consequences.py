from clause_fixtures import decode_prompt
"""Derived consequences retain evidence and conditions without rewriting provider answers."""
import copy
import json
from types import SimpleNamespace
import unittest

from bauvo_working_units.citation_checks import citation_findings
from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.phases.consequences.stage import extract_consequences
from bauvo_working_units.project_facts import collect_project_facts
from bauvo_working_units.project_flow import Document
from bauvo_working_units.schemas import RESPONSE_SCHEMAS
from consequence_fixtures import claim


class DerivedConsequencesTests(unittest.TestCase):
    text = 'Eine Genehmigung setzt einen Antrag voraus. Die Ausführung beginnt erst nach Genehmigung.'

    def test_schema_distinguishes_explicit_derived_mixed_and_unknown(self):
        row = RESPONSE_SCHEMAS['PAGE_CONSEQUENCES']['properties']['assessments']['items']
        self.assertEqual(set(row['properties']['status']['enum']), {'EXPLICIT','DERIVED','MIXED','NOT_STATED'})
        self.assertEqual(set(row['required']), {'unit_id','status','consequences'})

    def test_derived_and_mixed_answers_require_citations_without_rewriting_them(self):
        for status in ('DERIVED','MIXED'):
            for citations, expected in [([], 'missing_citations'),
                    ([{'pdf_page':1,'quote':'Unbelegte Sanktion'}], 'citation_quote_not_found')]:
                with self.subTest(status=status, citations=citations):
                    response={'assessments':[{'unit_id':'U1','status':status,
                        'consequences':['Abgeleitet: Die Ausführung bleibt bedingt.'],'citations':citations}]}
                    before=copy.deepcopy(response)
                    findings=citation_findings(response,[{'pdf_page':1,'text':self.text}])
                    self.assertIn(expected,{f['code'] for f in findings})
                    self.assertEqual(response,before)

    def test_dependency_chain_conditions_and_raw_response_survive_the_stage_boundary(self):
        documents=[Document('D1','Agreement','fixture.pdf',schema_version=6,source_key='fixture',
                            source_snapshot='fixture-hash',pages=({'pdf_page':1,'text':self.text},))]
        citation={'source_key':'fixture','document_id':'D1','pdf_page':1,'quote':self.text}
        def earlier(prompt):
            kind=prompt.split('.',1)[0]
            if kind=='PAGE_EXTRACTION':
                return {'working_units':[{'name':'Antrag stellen','action':'Antrag stellen',
                    'actor':'Bauherr','performed_by':'Bauherr','citations':[citation]}],'carry_forward':''}
            if kind=='PAGE_OFFERS':return {'offers':[]}
            data=decode_prompt(prompt)
            return {'assessments':[{'unit_id':u['id'],'answer':'NOT_STATED','citations':[]}
                                   for u in data['working_units']]}
        facts=collect_project_facts([],SimpleNamespace(extract=lambda _: self.fail('No fact calls')))
        baseline=extract_page_units(documents,SimpleNamespace(extract=earlier),stop_after='PAGE_TIMING',project_facts=facts)
        before=copy.deepcopy(baseline)
        unit_id=baseline['working_units'][0]['id']
        for status in ('EXPLICIT','DERIVED','MIXED','NOT_STATED'):
            with self.subTest(status=status):
                consequence='Abgeleitet: Falls noch kein Antrag vorliegt, fehlt ohne Antrag die Genehmigung; die Ausführung kann nicht beginnen.'
                answer={'assessments':[{'unit_id':unit_id,'status':status,
                    'consequences':[] if status=='NOT_STATED' else [
                        {**claim({'evidence_id':'placeholder'}, kind, f'C{i}'), 'claim':consequence}
                        for i,kind in enumerate(('EXPLICIT','DERIVED') if status=='MIXED' else (status,))]}]}
                calls=[]
                def model(prompt):
                    calls.append(prompt)
                    data=decode_prompt(prompt)
                    self.assertEqual(data['source_pages'][0]['text'],self.text)
                    self.assertNotIn('reviews',data['working_units'][0])
                    for c in answer['assessments'][0]['consequences']:
                        c['citations']=[{'evidence_id':data['evidence_catalog'][0]['evidence_id']}]
                        for premise in c['premises']:premise['citations']=copy.deepcopy(c['citations'])
                    return copy.deepcopy(answer)
                result=extract_consequences(baseline,documents,SimpleNamespace(extract=model))
                self.assertEqual(len(calls),1)
                self.assertTrue(calls[0].startswith('PAGE_CONSEQUENCES.'))
                self.assertEqual(next(r['gemini'] for r in result['gemini_responses'] if r['pass']=='PAGE_CONSEQUENCES'),answer)
                self.assertEqual(result['working_units'][0]['reviews'][-1]['gemini'],answer['assessments'][0])
                self.assertEqual(result['working_units'][0]['reviews'][:-1],baseline['working_units'][0]['reviews'])
                self.assertEqual(result['review_findings'],[])
                self.assertEqual(baseline,before)
