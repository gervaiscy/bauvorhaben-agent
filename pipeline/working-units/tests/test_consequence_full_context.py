from clause_fixtures import decode_prompt
"""Full consequence evidence is independent of target windows and five-unit batches."""
import copy
import json
from types import SimpleNamespace
import unittest

from bauvo_working_units.citation_checks import citation_findings
from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.phases.consequences.service import full_source_documents
from bauvo_working_units.phases.consequences.stage import extract_consequences
from bauvo_working_units.project_facts import collect_project_facts
from bauvo_working_units.project_flow import Document
from bauvo_working_units.schemas import RESPONSE_SCHEMAS
from consequence_fixtures import claim


def document(identifier, key, pages, kind='project'):
    return Document(identifier,identifier,key+'.pdf',schema_version=6,source_key=key,
                    source_snapshot=key+'-hash',source_type=kind,
                    pages=tuple({'pdf_page':number,'text':text,'checkboxes':[]} for number,text in pages))


class ConsequenceFullContextTests(unittest.TestCase):
    def test_every_small_batch_sees_all_pages_and_other_documents_for_each_source_type(self):
        for kind in ('project','blb'):
            with self.subTest(kind=kind):
                target=document('TASKS','main',[(3,'Das Formular ausfüllen und zurücksenden.')],kind)
                notice=document('NOTICE','main',[(2,'Das Formular ist nicht vorgeschrieben. Eine eindeutige Erklärung genügt.')],kind)
                long=document('LONG','main',[(n,f'Vollständige Quellseite {n}.') for n in range(10,23)],kind)
                amendment=document('TASKS','amendment',[(3,'Eine Abtretung ist eine Alternative zur Bürgschaft.')])
                specification=document('TASKS','specification',[(3,'Technische Leistungsbeschreibung.')],'blb')
                docs=[target,notice,long];context=[amendment,specification]
                originals=copy.deepcopy((docs,context))
                facts=collect_project_facts([],SimpleNamespace(extract=lambda _: self.fail('No fact calls')))
                def earlier(prompt):
                    data=decode_prompt(prompt);phase=prompt.split('.',1)[0]
                    if phase=='PAGE_EXTRACTION':
                        units=[{'name':f'Formular {i}','action':'Formular verwenden',
                            'actor':'Bauherr','performed_by':'Bauherr',
                            'citations':[{'pdf_page':3,'quote':target.pages[0]['text']}]} for i in range(6)] if data['document']['id']=='TASKS' else []
                        return {'working_units':units,'carry_forward':''}
                    if phase=='PAGE_OFFERS':return {'offers':[]}
                    return {'assessments':[{'unit_id':u['id'],'answer':'NOT_STATED','citations':[]} for u in data['working_units']]}
                baseline=extract_page_units(docs,SimpleNamespace(extract=earlier),chunk_pages=3,overlap_pages=1,
                    context_documents=context,project_facts=facts,stop_after='PAGE_TIMING')
                before=copy.deepcopy(baseline);calls=[];responses=[]
                citation={'source_key':'main','document_id':'NOTICE','pdf_page':2,'quote':notice.pages[0]['text']}
                def model(prompt):
                    self.assertTrue(prompt.startswith('PAGE_CONSEQUENCES.'))
                    data=decode_prompt(prompt);calls.append(data)
                    self.assertEqual([p['pdf_page'] for p in data['source_pages']],[3])
                    self.assertTrue(all('reviews' not in u for u in data['working_units']))
                    self.assertEqual(data['source_documents'],full_source_documents([*docs,*context]))
                    self.assertEqual(sum(len(d['pages']) for d in data['source_documents']),17)
                    reference={'evidence_id':next(e['evidence_id'] for e in data['evidence_catalog'] if e['source_key']=='main' and e['document_id']=='NOTICE')}
                    result={'assessments':[{'unit_id':u['id'],'status':'DERIVED',
                        'consequences':[{**claim(reference), 'claim':'Der Verzicht auf dieses Formular verhindert den Widerruf nicht; eine eindeutige Erklärung ist eine Alternative.'}]} for u in data['working_units']]}
                    responses.append(copy.deepcopy(result));return result
                result=extract_consequences(baseline,docs,SimpleNamespace(extract=model),3,1,context)
                self.assertEqual([len(c['working_units']) for c in calls],[5,1])
                self.assertEqual(result['review_findings'],[])
                self.assertEqual([r['gemini'] for r in result['gemini_responses'] if r['pass']=='PAGE_CONSEQUENCES'],responses)
                self.assertEqual(result['context_snapshots'],baseline['context_snapshots'])
                for prior,current in zip(baseline['working_units'],result['working_units']):
                    self.assertEqual(prior['gemini'],current['gemini'])
                    self.assertEqual(prior['reviews'],current['reviews'][:-1])
                    self.assertEqual(current['reviews'][-1]['derived']['context_scope'],'all_supplied_documents')
                self.assertEqual(baseline,before)
                self.assertEqual((docs,context),originals)

    def test_qualified_citations_disambiguate_identical_document_ids_and_page_numbers(self):
        first=document('D1','first',[(1,'Erster Text.')])
        second=document('D1','second',[(1,'Alternative im anderen Dokument.')])
        sources=full_source_documents([first,second])
        pages=[{**p,'source_key':d['source_key'],'document_id':d['document_id']} for d in sources for p in d['pages']]
        good={'source_key':'second','document_id':'D1','pdf_page':1,'quote':second.pages[0]['text']}
        for citation,code in [(good,None),({**good,'source_key':'first'},'citation_quote_not_found'),
                ({**good,'source_key':'unknown'},'citation_page_outside_window'),
                ({**good,'document_id':'wrong'},'citation_page_outside_window'),
                ({**good,'pdf_page':2},'citation_page_outside_window'),
                ({'pdf_page':1,'quote':first.pages[0]['text']},'missing_citation_source')]:
            with self.subTest(citation=citation):
                response={'assessments':[{'unit_id':'U1','status':'DERIVED','consequences':['Abgeleitet: Alternative.'],'citations':[citation]}]}
                before=copy.deepcopy(response)
                findings=citation_findings(response,first.pages,pages,source_key='first',document_id='D1',require_qualified=True)
                self.assertEqual([f['code'] for f in findings],[] if code is None else [code])
                self.assertEqual(response,before)

    def test_complete_pages_are_deduplicated_only_for_identical_document_identity(self):
        doc=document('D1','source',[(1,'Unveränderter Volltext.')])
        self.assertEqual(full_source_documents([doc,copy.deepcopy(doc)]),full_source_documents([doc]))
        other=document('D1','source',[(1,'Widersprechender Text.')])
        with self.assertRaisesRegex(ValueError,'Conflicting source documents'):
            full_source_documents([doc,other])

    def test_response_schema_requires_evidence_references_for_each_citation(self):
        row=RESPONSE_SCHEMAS['PAGE_CONSEQUENCES']['properties']['assessments']['items']
        citation=row['properties']['consequences']['items']['properties']['citations']['items']
        self.assertEqual(set(citation['required']),{'evidence_id'})
        # All phases now select IDs instead of regenerating source coordinates.
        timing=RESPONSE_SCHEMAS['PAGE_TIMING']['properties']['assessments']['items']
        self.assertEqual(set(timing['properties']['citations']['items']['required']),{'evidence_id'})
