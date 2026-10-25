"""Compact project agreements retain raw text and source-bound quote evidence."""
import copy
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from bauvo_working_units.project_flow import Document, extract_project_units
from bauvo_working_units.project_facts import collect_project_facts, facts_view, evidence_pages
from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.schemas import RESPONSE_SCHEMAS
from bauvo_working_units.gemini import IncompleteResponse
from bauvo_working_units.trace import RetryingModel, RawRunRecorder


def document(key, text, number=1, identifier='D1'):
    return Document(identifier,'Contract',key+'.pdf',schema_version=6,source_key=key,
                    pages=({'pdf_page':number,'text':text},))


def proposal(doc, quote=None):
    return {'verpflichtung':doc.pages[0]['text'],
            'zitate':[{'pdf_page':doc.pages[0]['pdf_page'],'text':quote or doc.pages[0]['text']}]}


class ProjectFactsTests(unittest.TestCase):
    def test_schema_extracts_commitments_and_quotes_without_classification(self):
        fields=RESPONSE_SCHEMAS['PAGE_PROJECT_FACTS']['properties']
        self.assertEqual(set(fields),{'verpflichtungen'})
        schema=fields['verpflichtungen']['items']
        self.assertEqual(set(schema['required']),{'verpflichtung','zitate'})
        self.assertEqual(set(schema['properties']['zitate']['items']['required']),{'pdf_page','text'})

    def test_every_window_is_read_with_only_page_text_and_without_crossing_documents(self):
        docs=[Document('D1','A','a.pdf',schema_version=6,source_key='contract',
                       pages=tuple({'pdf_page':n,'text':f'Clause {n}'} for n in range(1,8))),
              document('contract','Another form',8,'D2')]
        calls=[]
        def extract(prompt):
            self.assertTrue(prompt.startswith('PAGE_PROJECT_FACTS.'))
            data=json.loads(prompt.split('SOURCE DATA:\n')[1]);calls.append(data)
            self.assertEqual(set(data),{'source_pages'})
            self.assertTrue(all(set(p)=={'pdf_page','text'} for p in data['source_pages']))
            return {'verpflichtungen':[]}
        result=collect_project_facts([*docs,docs[0]],SimpleNamespace(extract=extract))
        self.assertEqual([w['primary_pages'] for w in result['windows']],[[1,2,3],[4,5],[6,7],[8]])
        self.assertEqual([[p['pdf_page'] for p in c['source_pages']] for c in calls],
                         [[1,2,3],[3,4,5],[5,6,7],[8]])
        self.assertEqual(len(result['source_snapshots']),2)

    def test_conflicting_snapshots_fail_before_any_request(self):
        a=document('contract','Agreement A');b=document('contract','Agreement B')
        with self.assertRaisesRegex(ValueError,'Conflicting source snapshots'):
            collect_project_facts([a,b],SimpleNamespace(extract=lambda p:self.fail('Paid request')))

    def test_offers_uncertainty_exclusions_and_amendments_are_not_reinterpreted(self):
        doc=document('contract','Eine Anlage ist angeboten. Auswahl offen. Dämmung ausgeschlossen. Ein Nachtrag ändert die Ausführung.')
        rows=[{**proposal(doc),'verpflichtung':text} for text in
              ('Eine Anlage ist angeboten.','Auswahl offen.','Dämmung ausgeschlossen.','Ein Nachtrag ändert die Ausführung.')]
        before=copy.deepcopy(rows)
        result=collect_project_facts([doc],SimpleNamespace(extract=lambda p:{'verpflichtungen':copy.deepcopy(rows)}))
        self.assertEqual([r['gemini'] for r in result['facts']],before)
        self.assertEqual(rows,before)
        self.assertEqual(result['status'],'recorded')  # Execution, not legal certainty.
        self.assertTrue(all('status' not in f['gemini'] for f in result['facts']))
        self.assertEqual(len(facts_view(result)['facts']),4)

    def test_missing_or_fabricated_evidence_stays_visible_without_becoming_verified(self):
        doc=document('contract','Vereinbart ist eine Wärmepumpe.')
        bad=proposal(doc,quote='Vereinbart ist eine Gasheizung.')
        empty={**proposal(doc),'zitate':[]}
        result=collect_project_facts([doc],SimpleNamespace(extract=lambda p:{'verpflichtungen':[bad,empty]}))
        self.assertEqual([r['gemini'] for r in result['facts']],[bad,empty])
        codes={f['code'] for f in result['review_findings']}
        self.assertIn('citation_quote_not_found',codes);self.assertIn('missing_citations',codes)
        self.assertEqual(evidence_pages(result),[])
        self.assertEqual(len(facts_view(result)['facts']),2)

    def test_quotes_are_bound_to_the_request_document_not_another_document_on_same_page(self):
        a=document('contract','Eine Wärmepumpe ist vereinbart.')
        b=document('offer','Eine Gasheizung wird angeboten.')
        rows=iter([{'verpflichtungen':[proposal(a)]},{'verpflichtungen':[proposal(b,quote=a.pages[0]['text'])]}])
        result=collect_project_facts([a,b],SimpleNamespace(extract=lambda p:next(rows)))
        self.assertEqual(evidence_pages(result),[{'source_key':'contract','document_id':'D1',
                         'pdf_page':1,'text':a.pages[0]['text']}])
        self.assertEqual(result['facts'][1]['derived']['verified_citations'],[])
        self.assertEqual([r['source_key'] for r in facts_view(result)['facts']],['contract','offer'])

    def test_provider_cannot_inject_another_source_identifier_into_compact_quotes(self):
        doc=document('contract','Eine Leistung ist zugesagt.')
        row=proposal(doc);row['zitate'][0]['source_key']='other'
        result=collect_project_facts([doc],SimpleNamespace(extract=lambda p:{'verpflichtungen':[row]}))
        self.assertIn('invalid_project_fact',{f['code'] for f in result['review_findings']})
        self.assertEqual(evidence_pages(result),[])
        self.assertEqual(result['facts'][0]['gemini'],row)

    def test_overlap_proposals_are_preserved_instead_of_silently_deduplicated(self):
        doc=Document('D1','Contract','c.pdf',schema_version=6,source_key='contract',
                     pages=tuple({'pdf_page':n,'text':f'Clause {n}'} for n in range(1,5)))
        row={'verpflichtung':'Clause 3','zitate':[{'pdf_page':3,'text':'Clause 3'}]}
        result=collect_project_facts([doc],SimpleNamespace(extract=lambda p:{'verpflichtungen':[copy.deepcopy(row)]}))
        self.assertEqual([r['gemini'] for r in result['facts']],[row,row])
        self.assertEqual(result['review_findings'],[])
        self.assertNotEqual(result['facts'][0]['id'],result['facts'][1]['id'])

    def test_legacy_shape_is_flagged_without_guessing_a_conversion(self):
        old={'project_facts':[{'subject':'House','value':'Old format'}]}
        result=collect_project_facts([document('contract','Contract')],SimpleNamespace(extract=lambda p:old))
        self.assertEqual(result['facts'],[])
        self.assertEqual(result['gemini_responses'][0]['gemini'],old)
        self.assertEqual(result['review_findings'][0]['code'],'missing_or_invalid_project_facts')

    def test_failed_window_retries_once_and_keeps_later_facts_and_raw_attempts(self):
        source=Document('D1','Contract','c.pdf',schema_version=6,source_key='contract',
                        pages=tuple({'pdf_page':n,'text':f'Vereinbarung {n}'} for n in range(1,5)))
        calls=[]
        class Model:
            last_raw_response=None
            last_finish_reason=None
            def extract(self,prompt):
                calls.append(prompt)
                data=json.loads(prompt.split('SOURCE DATA:\n')[1])
                if [p['pdf_page'] for p in data['source_pages']]==[1,2,3]:
                    self.last_raw_response='{unfinished';self.last_finish_reason='MAX_TOKENS'
                    raise IncompleteResponse('unfinished_generation','MAX_TOKENS')
                page=data['source_pages'][-1]
                row={'verpflichtung':page['text'],'zitate':[{'pdf_page':4,'text':page['text']}]}
                out={'verpflichtungen':[row]};self.last_raw_response=json.dumps(out);self.last_finish_reason='STOP'
                return out
        with tempfile.TemporaryDirectory() as tmp:
            raw=Path(tmp)/'raw.json'
            result=collect_project_facts([source],RetryingModel(RawRunRecorder(Model(),raw)))
            self.assertEqual(result['status'],'request_failed');self.assertEqual(len(result['facts']),1)
            self.assertEqual(calls[0],calls[1]);self.assertEqual(len(calls),3)
            self.assertEqual(json.loads(raw.read_text())[:2],['{unfinished','{unfinished'])
            self.assertEqual(result['gemini_responses'][0]['status'],'request_failed')
            self.assertEqual(len(result['review_findings'][0]['attempts']),2)

    def test_full_collection_reaches_applicability_with_conflicting_and_unresolved_source_text(self):
        docs=[document('contract','Vereinbart ist Holz.'),document('amendment','Vereinbart ist Stein.'),
              document('offer','Eine Solaranlage wird angeboten.'),document('selection','Die Farbe bleibt ungeklärt.')]
        answers=iter([{'verpflichtungen':[proposal(d)]} for d in docs])
        facts=collect_project_facts(docs,SimpleNamespace(extract=lambda p:next(answers)))
        source=document('blb','Der Kunde bestätigt die Planung.');calls=[]
        def extract(prompt):
            kind=prompt.split('.',1)[0];data=json.loads(prompt.split('SOURCE DATA:\n')[1])
            if kind=='PAGE_EXTRACTION':return {'working_units':[{'action':'Planung bestätigen',
                'citations':[{'pdf_page':1,'quote':source.pages[0]['text']}]}],'carry_forward':''}
            if kind=='PAGE_OFFERS':return {'offers':[]}
            if kind=='PAGE_APPLICABILITY':calls.append(data)
            return {'assessments':[{'unit_id':u['id'],'status':'NOT_STATED','citations':[]} for u in data['working_units']]}
        result=extract_page_units([source],SimpleNamespace(extract=extract),project_facts=facts)
        received=calls[0]['project_facts']
        self.assertEqual([r['gemini']['verpflichtung'] for r in received['facts']],[d.pages[0]['text'] for d in docs])
        self.assertEqual([r['source_key'] for r in received['facts']],[d.source_key for d in docs])
        self.assertTrue(all(r['gemini']['zitate'] for r in received['facts']))
        self.assertNotIn('project_context',calls[0]);self.assertEqual(result['project_facts'],facts)

    def test_production_entrypoint_collects_facts_before_task_passes_and_retains_raw(self):
        source=document('blb','Der Kunde bestätigt die Planung.')
        contract=document('contract','Vereinbart ist eine Wärmepumpe.');calls=[];answers=[]
        def extract(prompt):
            kind=prompt.split('.',1)[0];data=json.loads(prompt.split('SOURCE DATA:\n')[1]);calls.append(kind)
            if kind=='PAGE_PROJECT_FACTS':
                out={'verpflichtungen':[] if data['source_pages'][0]['text']==source.pages[0]['text'] else [proposal(contract)]}
            elif kind=='PAGE_EXTRACTION':out={'working_units':[{'action':'Planung bestätigen','citations':[
                {'pdf_page':1,'quote':source.pages[0]['text']}]}],'carry_forward':''}
            elif kind=='PAGE_OFFERS':out={'offers':[]}
            else:out={'assessments':[{'unit_id':u['id'],'status':'NOT_STATED','citations':[]} for u in data['working_units']]}
            answers.append(copy.deepcopy(out));return out
        result=extract_project_units([source],SimpleNamespace(extract=extract),context_documents=[contract])
        self.assertEqual(calls[:2],['PAGE_PROJECT_FACTS','PAGE_PROJECT_FACTS'])
        self.assertEqual(calls[-1],'PAGE_APPLICABILITY')
        self.assertEqual([r['gemini'] for r in result['gemini_responses']],answers)
        self.assertEqual(len(result['project_facts']['facts']),1)
        self.assertEqual(len(result['working_units']),1)
