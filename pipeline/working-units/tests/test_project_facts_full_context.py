from historical_facts import collect_historical_facts
from clause_fixtures import owned_answer
from clause_fixtures import decode_prompt
"""Full contract packages, cross-document citations and request-bound recovery."""
import copy
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from bauvo_working_units.cache import CachedModel
from bauvo_working_units.fact_artifact import validate_facts
from bauvo_working_units.page_flow import _prompt
from bauvo_working_units.project_facts import collect_project_facts, facts_view, document_facts_view
from bauvo_working_units.project_flow import Document
from clause_fixtures import index_answer, interpreted
from test_project_facts import document


class FullContractFactsTests(unittest.TestCase):
    def test_entire_contract_package_is_sent_once_even_when_task_windows_are_tiny(self):
        main=Document('D1','Bauvertrag','contract.pdf',schema_version=6,source_key='contract',
                      pages=tuple({'pdf_page':n,'text':f'Vertragsseite {n}.\nBedingung {n}.'} for n in range(1,18)))
        offer=document('contract','Angebot mit bedingter Gutschrift.',18,'D2')
        amendment=document('contract','Nachtrag mit den Bedingungen der Gutschrift.',19,'D3')
        blb=replace(document('blb','Allgemeine Bauleistungsbeschreibung.'),source_type='blb')
        calls=[]
        def provider(prompt):
            data=decode_prompt(prompt);calls.append(data)
            self.assertEqual(data['context_scope'],'full_contract_package')
            self.assertEqual([d['document_id'] for d in data['documents']],['D1','D2','D3'])
            self.assertEqual([p['pdf_page'] for p in data['source_pages']],list(range(1,20)))
            self.assertEqual([p['text'] for p in data['source_pages']],
                             [p['text'] for doc in (main,offer,amendment) for p in doc.pages])
            self.assertEqual({p['source_key'] for p in data['source_pages']},{'contract'})
            return index_answer(data)
        result=collect_historical_facts([main,offer,amendment,blb,main],SimpleNamespace(extract=provider),
                                    max_chars=1,chunk_pages=1,overlap_pages=0)
        self.assertEqual(len(calls),1)
        self.assertEqual(len(result['gemini_responses']),1)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(len(result['source_snapshots']),3)
        self.assertEqual(len(result['excluded_sources']),1)
        self.assertEqual(validate_facts(result,[main,offer,amendment,blb]),result)

    def cross_document_result(self):
        offer=document('offer','Eine Gutschrift von 3.000 Euro gilt nur laut Nachtrag.')
        amendment=document('amendment','Der Nachtrag verlangt Zahlung innerhalb von 14 Tagen.')
        raw=[]
        def provider(prompt):
            data=decode_prompt(prompt)
            refs=[{'evidence_id':e['evidence_id']} for e in data['evidence_catalog']]
            answer={'verpflichtungen':[interpreted(refs,
                'Die Gutschrift von 3.000 Euro setzt Zahlung innerhalb von 14 Tagen voraus.')],
                'nicht_als_fakt_erfasst':[]}
            answer = owned_answer(data, answer)
            raw.append(copy.deepcopy(answer))
            return answer
        result=collect_historical_facts([offer,amendment],SimpleNamespace(extract=provider))
        return [offer,amendment],result,raw[0]

    def test_interpretation_can_cite_offer_and_amendment_even_when_page_and_document_ids_collide(self):
        docs,result,raw=self.cross_document_result()
        fact=result['facts'][0]
        self.assertEqual(result['gemini_responses'][0]['gemini'],raw)
        self.assertEqual(result['status'],'recorded')
        self.assertIsNone(fact['derived']['source_key'])
        self.assertIsNone(fact['derived']['document_id'])
        self.assertEqual({s['source_key'] for s in fact['derived']['sources']},{'offer','amendment'})
        self.assertEqual({c['source_key'] for c in fact['derived']['verified_citations']},{'offer','amendment'})
        self.assertEqual(len({c['evidence_id'] for c in fact['derived']['verified_citations']}),2)
        self.assertEqual(validate_facts(result,docs),result)
        for doc in docs:
            self.assertEqual(document_facts_view(result,doc)['facts'][0]['id'],fact['id'])
        for phase in ('PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_APPLICABILITY'):
            payload={'source_pages':[], 'project_facts':facts_view(result)}
            actual=json.loads(_prompt(phase,payload,'de').split('SOURCE DATA:\n')[1])
            selected=actual['project_facts']['facts'][0]
            if phase == 'PAGE_TIMING':
                self.assertEqual(selected, {'id': fact['id'], 'text': fact['derived']['text']})
                continue
            self.assertEqual(selected['interpretation'],raw['verpflichtungen'][0]['interpretation'])
            if phase=='PAGE_OFFERS':
                self.assertEqual({c['document'] for c in selected['evidence']},{'D1','D2'})
                self.assertEqual([c['quote'] for c in selected['evidence']],
                                 [c['quote'] for c in fact['derived']['verified_citations']])
            else:
                self.assertEqual(selected['sources'],fact['derived']['sources'])
                self.assertEqual(len(selected['citations']),2)

    def test_changed_or_partial_package_and_forged_cross_document_scope_are_rejected(self):
        docs,result,_=self.cross_document_result()
        for kind in ('source','window','response_scope','fact_scope','remove_response','duplicate_response'):
            changed=copy.deepcopy(result)
            if kind=='source':changed['source_documents'].pop()
            elif kind=='window':changed['windows'][1]['pages']=[]
            elif kind=='response_scope':changed['gemini_responses'][0]['sources'].pop()
            elif kind=='fact_scope':changed['facts'][0]['derived']['sources'].pop()
            elif kind=='remove_response':changed['gemini_responses']=[]
            else:changed['gemini_responses'].append(copy.deepcopy(changed['gemini_responses'][0]))
            with self.subTest(kind=kind),self.assertRaises(ValueError):validate_facts(changed,docs)

    def test_cache_reuses_whole_request_and_changing_only_an_amendment_invalidates_it(self):
        offer=document('contract','Preis laut Angebot.',1,'D1')
        amendment=document('contract','Zahlung innerhalb von 14 Tagen.',2,'D2')
        calls=[]
        def provider(prompt):
            calls.append(prompt)
            return index_answer(decode_prompt(prompt))
        with TemporaryDirectory() as tmp:
            cache=CachedModel(SimpleNamespace(extract=provider),Path(tmp)/'cache',{'model':'fixture'})
            first=collect_historical_facts([offer,amendment],cache)
            saved={p:p.read_bytes() for p in (Path(tmp)/'cache').glob('*.json')}
            self.assertEqual(collect_historical_facts([offer,amendment],cache),first)
            changed=replace(amendment,pages=({'pdf_page':2,'text':'Zahlung innerhalb von 16 Tagen.'},))
            latest=collect_historical_facts([offer,changed],cache)
            self.assertEqual(len(calls),2)
            self.assertNotEqual(first['gemini_responses'][0]['id'],latest['gemini_responses'][0]['id'])
            self.assertEqual(saved,{p:p.read_bytes() for p in saved})
            self.assertEqual(validate_facts(latest,[offer,changed]),latest)
