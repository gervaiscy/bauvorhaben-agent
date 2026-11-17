"""Window-only transport, compact fact evidence and offline quotation binding."""
import copy
import json
import unittest

from bauvo_working_units.citation_evidence import catalog
from bauvo_working_units.page_flow import _prompt
from bauvo_working_units.phases.offers.payload import model_payload
from bauvo_working_units.phases.offers.response import resolve_response
from bauvo_working_units.phases.offers.schema import RESPONSE
import test_offer_boundaries as boundaries
from test_offer_boundaries import offer, citation, doc


def fixture():
    source={'source_key':'contract-private-key','document_id':'D0001','title':'Vertrag',
            'pages':[{'pdf_page':1,'text':'Erste Regel.\nZweite Regel.\nNicht ausgewählt.\n'}]}
    entries=catalog([{**p,'source_key':source['source_key'],'document_id':source['document_id']} for p in source['pages']])
    return {'source_key':'blb-private-key','document':{'id':'D0001','title':'BLB'},
            'source_pages':[{'pdf_page':10,'text':'Overlap','primary':False},
                            {'pdf_page':11,'text':'  Übergröße €\r\n\nZusatzleistung.\t','primary':True}],
            'source_documents':[{'source_key':'other','document_id':'D2','pages':[{'pdf_page':99,'text':'Nicht senden.'}]}],
            'known_items':[{'name':'Vorheriges Angebot'}],
            'evidence_catalog':entries,
            'project_facts':{'source_documents':[source],
                'facts':[{'id':'private-fact-id','interpretation':'Vorgeschlagene Auslegung.',
                          'citations':entries[:2]+entries[:2]}]}}


class WindowOfferPayloadTests(unittest.TestCase):
    def test_context_explains_attached_facts_while_their_original_evidence_reaches_the_request(self):
        prompt=_prompt('PAGE_OFFERS',fixture(),'de')
        context=prompt.split('Kontext:\n',1)[1].split('\n\nZiel:\n',1)[0]
        self.assertIn('project_facts',context)
        data=json.loads(prompt.split('SOURCE DATA:\n',1)[1])
        fact=data['project_facts']['facts'][0]
        self.assertEqual(fact['interpretation'],'Vorgeschlagene Auslegung.')
        self.assertEqual(fact['evidence'],[{'document':'D1','pdf_page':1,
                                          'quote':'Erste Regel.\nZweite Regel.\n'}])
        self.assertTrue(all(p['passage'].startswith('P11-')
                            for page in data['source_pages'] for p in page['passages']))

    def test_prompt_has_homeowner_role_context_goal_and_question_in_order(self):
        prompt=_prompt('PAGE_OFFERS',fixture(),'de')
        headings=('Rolle','Kontext','Ziel','Frage')
        positions=[prompt.index(heading+':\n') for heading in headings]
        self.assertEqual(positions,sorted(positions))
        role=prompt[positions[0]:positions[1]]
        self.assertIn('Bauherr',role)
        for start,end in zip(positions,positions[1:]):
            self.assertTrue(prompt[start:end].split(':\n',1)[1].strip())
        question=prompt[positions[-1]:].split(':\n',1)[1].split('\n\n',1)[0]
        self.assertTrue(question.split('?',1)[0].strip())
        self.assertIn('?',question)

    def test_wire_contains_only_primary_original_pages_and_compact_facts(self):
        payload=fixture();before=copy.deepcopy(payload)
        data=model_payload(payload)
        self.assertEqual(set(data),{'source_pages','project_facts'})
        self.assertEqual(data['source_pages'],[{'pdf_page':11,'passages':[{'passage':'P11-1','text':'  Übergröße €\r\n\n'}, {'passage':'P11-2','text':'Zusatzleistung.\t'}]}])
        fact=data['project_facts']['facts'][0]
        self.assertEqual(fact['interpretation'],'Vorgeschlagene Auslegung.')
        self.assertEqual(fact['evidence'],[{'document':'D1','pdf_page':1,'quote':'Erste Regel.\nZweite Regel.\n'}])
        self.assertEqual(data['project_facts']['documents'],[{'id':'D1','title':'Vertrag'}])
        text=json.dumps(data,ensure_ascii=False)
        for forbidden in ('Nicht ausgewählt.','Overlap','Nicht senden.','private-fact-id',
                          'contract-private-key','blb-private-key','Vorheriges Angebot'):
            self.assertNotIn(forbidden,text)
        self.assertEqual(payload,before)

    def test_prompt_uses_one_simple_question_and_the_same_schema_as_the_api(self):
        prompt=_prompt('PAGE_OFFERS',fixture(),'de')
        instructions,data=prompt.split('\n\nSOURCE DATA:\n')
        text,encoded=instructions.rsplit('\n\n{',1)
        self.assertEqual(json.loads('{'+encoded),RESPONSE)
        self.assertEqual(json.loads(data),model_payload(fixture()))
        # The fictional output example must obey the same contract as real rows.
        from bauvo_working_units.phases.offers.validation import valid_proposal
        example = json.JSONDecoder().raw_decode(text.split('Beispielausgabe:\n',1)[1])[0]
        self.assertEqual(len(example['offers']),3)
        self.assertTrue(all(valid_proposal(row) for row in example['offers']))
        self.assertEqual(len({row['name'] for row in example['offers']}),3)
        self.assertEqual(len({json.dumps(row['citations']) for row in example['offers']}),1)
        # Example rows live in instructions, never in the actual source data.
        self.assertNotIn('offers',json.loads(data))

    def test_unselected_gaps_are_not_joined_and_claim_and_context_evidence_survive(self):
        payload=fixture();view=payload['project_facts'];source=view['source_documents'][0]
        refs=catalog([{**p,'source_key':source['source_key'],'document_id':source['document_id']} for p in source['pages']])
        fact=view['facts'][0]
        fact.update(citations=[refs[0],refs[2]],context_citations=[refs[1]],
                    claims=[{'statement':'Eine Aussage.','verified_citations':[refs[0]]}])
        data=model_payload(payload)['project_facts']['facts'][0]
        self.assertEqual([r['quote'] for r in data['evidence']],['Erste Regel.\n','Nicht ausgewählt.\n'])
        self.assertEqual(data['context_evidence'][0]['quote'],'Zweite Regel.\n')
        self.assertEqual(data['claims'][0]['evidence'][0]['quote'],'Erste Regel.\n')

    def test_missing_or_conflicting_fact_sources_stop_before_any_provider_call(self):
        payload=fixture();payload['project_facts']['source_documents']=[]
        with self.assertRaisesRegex(ValueError,'requires its original source'):model_payload(payload)
        payload=fixture();conflict=copy.deepcopy(payload['project_facts']['source_documents'][0])
        conflict['pages'][0]['text']='Widerspruch.'
        payload['project_facts']['source_documents'].append(conflict)
        with self.assertRaisesRegex(ValueError,'Conflicting original'):model_payload(payload)

    def test_literal_blank_unicode_and_long_page_texts_are_not_truncated(self):
        payload=fixture()
        payload['source_pages']=[{'pdf_page':1,'text':'\n\t\n'},
                                 {'pdf_page':2,'text':'Übergröße € '*2000+'\r\n'}]
        self.assertEqual([''.join(s['text'] for s in p['passages']) for p in model_payload(payload)['source_pages']],
                         [p['text'] for p in payload['source_pages']])

    def test_request_size_does_not_depend_on_other_pages_or_known_offers(self):
        payload=fixture();first=model_payload(payload)
        payload['source_documents']*=100
        payload['known_items']*=100
        self.assertEqual(model_payload(payload),first)


class OfferQuotationTests(unittest.TestCase):
    def pages(self,text):return [{'source_key':'local','document_id':'D1','pdf_page':1,'text':text}]

    def test_only_whitespace_is_normalized_and_derived_quote_uses_original_offsets(self):
        pages=self.pages('Größere Fenster sind\r\n  gegen Aufpreis möglich.\n')
        raw={'offers':[offer([{'pdf_page':1,'quote':'Größere Fenster sind gegen Aufpreis möglich.'}])]}
        before=copy.deepcopy(raw)
        resolved,audit,issues=resolve_response(raw,pages)
        self.assertEqual(issues,[])
        span=resolved['offers'][0]['citations'][0]
        self.assertEqual(span['quote'],pages[0]['text'][span['start']:span['end']])
        self.assertIn('\r\n  ',span['quote'])
        self.assertEqual(audit['bindings'][0]['method'],'unique_whitespace_quote')
        self.assertEqual(raw,before)

    def test_rewording_punctuation_changes_and_noncontiguous_fragments_remain_blocking(self):
        pages=self.pages('Größere Fenster sind gegen Aufpreis möglich. Dazwischen. Montage separat.')
        for quote in ('Größere Fenster sind kostenlos möglich.',
                      'Größere Fenster sind gegen Aufpreis möglich!',
                      'Größere Fenster sind gegen Aufpreis möglich. Montage separat.'):
            raw={'offers':[offer([{'pdf_page':1,'quote':quote}])]}
            _,_,issues=resolve_response(raw,pages)
            self.assertEqual(issues[0]['code'],'citation_quote_not_found')

    def test_repeated_including_overlapping_occurrences_are_ambiguous(self):
        for text,quote in [('Zusatz. Zusatz.','Zusatz.'),('a a a','a a')]:
            _,_,issues=resolve_response({'offers':[offer([{'pdf_page':1,'quote':quote}])]},self.pages(text))
            self.assertEqual(issues[0]['reason'],'ambiguous')

    def test_audit_uses_current_primary_scope_and_preserves_raw(self):
        from bauvo_working_units.citation_repair import repair_saved_result
        target=doc('D1','local',[(1,'Zusatz.\nNur auf Wunsch.')])
        raw={'offers':[offer([{'passage':'P1-1'}])]}
        helper=boundaries.OfferBoundaryTests();helper.setUp()
        result=helper.run_offers(lambda _:copy.deepcopy(raw),documents=[target],context=[])
        response=result['gemini_responses'][-1]
        result['review_findings'].append({'response_id':response['id'],'code':'unknown_passage_id',
                                         'path':'$.offers[0].citations[0]'})
        before=copy.deepcopy(result)
        audited=repair_saved_result(result,[target],model=type('NoCall',(),{'extract':lambda *_:self.fail('No call')})())
        self.assertEqual(audited['review_findings'],[])
        self.assertEqual(audited['offers'][0]['derived']['citations'],result['offers'][0]['derived']['citations'])
        self.assertEqual(audited['gemini_responses'][-1]['gemini'],raw)
        self.assertEqual(result,before)
