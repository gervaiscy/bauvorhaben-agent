"""Short source selections eliminate transcription errors, not interpretation errors."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from bauvo_working_units.cache import CachedModel
from bauvo_working_units.call_budget import BudgetedModel, CallBudget, CallLimitExceeded
from bauvo_working_units.citation_evidence import evidence_span
from bauvo_working_units.citation_repair import repair_saved_result
from bauvo_working_units.phases.offers.passages import selectable_pages, reference_map, resolve_response
from bauvo_working_units.phases.offers.stage import extract_offers
from bauvo_working_units.phases.timing.stage import extract_timing
from test_offer_boundaries import baseline, doc, offer, wire


class OfferPassageTests(unittest.TestCase):
    def pages(self,text,number=58):
        return [{'source_key':'local','document_id':'D1','pdf_page':number,'text':text}]

    def test_missing_prefix_binds_only_to_an_existing_local_label_with_audit_and_unchanged_raw(self):
        pages=self.pages('Erste Regel.\n\nEin Zusatzgerät ist separat bestellbar.',44)
        raw={'offers':[offer([{'passage':'44-2'}])]};before=copy.deepcopy(raw)
        resolved,audit,issues=resolve_response(raw,pages)
        self.assertEqual(issues,[])
        selected=resolved['offers'][0]['citations'][0]
        self.assertEqual(selected,reference_map(pages)['P44-2'])
        binding=audit['bindings'][0]
        self.assertEqual(binding['method'],'missing_prefix_normalization')
        self.assertEqual(binding['original_passage'],'44-2')
        self.assertEqual(binding['passage'],'P44-2')
        self.assertEqual(raw,before)

    def test_missing_prefix_never_guesses_unknown_pages_passages_or_other_identifier_changes(self):
        pages=self.pages('Ein Zusatzgerät ist separat bestellbar.',44)
        for label in ['43-1','44-2','044-1','44-01','p44-1',' 44-1','44-1 ','P44-01',
                      '44_1','D44-1','-44-1','0-1','44-0','４４-1']:
            with self.subTest(label=label):
                raw={'offers':[offer([{'passage':label}])]}
                resolved,audit,issues=resolve_response(raw,pages)
                self.assertEqual(issues[0]['code'],'unknown_passage_id')
                self.assertEqual(audit['bindings'],[])
                self.assertEqual(resolved,raw)

    def test_saved_missing_prefix_response_replays_and_enters_next_stage_without_calls(self):
        document=doc('D1','local',[(1,'Ein Zusatzgerät ist separat bestellbar.')])
        raw={'offers':[offer([{'passage':'1-1'}])]};prior=baseline([document])
        with TemporaryDirectory() as directory:
            # Completed provider responses are stored independently of validation.
            model=CachedModel(SimpleNamespace(extract=lambda _:copy.deepcopy(raw)),directory,{})
            captured=[]
            extract_offers(prior,[document],SimpleNamespace(extract=lambda p:(captured.append(p) or raw)))
            model.extract(captured[0])
            before={p:p.read_bytes() for p in Path(directory).glob('*.json')}
            offline=BudgetedModel(lambda:self.fail('No provider construction'),CallBudget(0))
            result=extract_offers(prior,[document],CachedModel(offline,directory,{}))
            self.assertEqual(before,{p:p.read_bytes() for p in before})
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(result['offers'][0]['gemini'],raw['offers'][0])
        audited=repair_saved_result(result,[document],SimpleNamespace(extract=lambda _:self.fail('No call')))
        self.assertEqual(audited['status'],'recorded')
        timing=extract_timing(audited,[document],SimpleNamespace(extract=lambda _:self.fail('No tasks to assess')))
        self.assertIn('PAGE_TIMING',timing['completed_passes'])

    def test_original_page_round_trip_including_blank_lines_wrapping_and_table_text(self):
        texts=['', '\n\t\n','\n\r\n  Größere Geräte.\r\nZweite Zeile.\r\n \t\r\nNeuer Absatz.\r\n',
               'Position\tBetrag\nA\t12 EUR\n\nAndere Tabelle\n| B | 15 EUR |\n']
        for text in texts:
            with self.subTest(text=text):
                pages=self.pages(text);before=copy.deepcopy(pages)
                supplied=selectable_pages(pages)[0]['passages']
                self.assertEqual(''.join(p['text'] for p in supplied),text)
                mapping=reference_map(pages)
                self.assertEqual([p['passage'] for p in supplied],list(mapping))
                self.assertEqual([p['text'] for p in supplied],[s['quote'] for s in mapping.values()])
                self.assertEqual(pages,before)
                for label,span in mapping.items():
                    self.assertEqual(span,evidence_span(pages[0],span['start'],span['end']))
                    self.assertTrue(label.startswith('P58-'))

    def test_awkward_grammar_and_prohibitions_are_restored_without_model_transcription(self):
        text='Für die Halterung der Bauteilen ist ein Aufpreis zu vereinbaren.\n\nDas Gerät darf nicht verdeckt werden.'
        raw={'offers':[offer([{'passage':'P58-1'},{'passage':'P58-2'}])]};before=copy.deepcopy(raw)
        resolved,audit,issues=resolve_response(raw,self.pages(text))
        self.assertEqual(issues,[])
        self.assertEqual([c['quote'] for c in resolved['offers'][0]['citations']],
                         ['Für die Halterung der Bauteilen ist ein Aufpreis zu vereinbaren.\n\n',
                          'Das Gerät darf nicht verdeckt werden.'])
        self.assertEqual(raw,before)
        self.assertTrue(all(b['method']=='original_passage_selection' for b in audit['bindings']))

    def test_repeated_identical_paragraphs_remain_individually_selectable(self):
        text='Ein Sensor ist gesondert bestellbar.\n\nEin Sensor ist gesondert bestellbar.'
        resolved,_,issues=resolve_response({'offers':[offer([{'passage':'P58-2'}])]},self.pages(text))
        self.assertEqual(issues,[])
        span=resolved['offers'][0]['citations'][0]
        self.assertEqual(span['start'],text.rindex('Ein Sensor'))

    def test_unknown_wrong_window_fact_ids_and_malformed_citations_never_fallback_to_text(self):
        pages=self.pages('Ein Sensor ist separat bestellbar.')
        for citation in [None,{}, {'passage':None},{'passage':58},{'passage':''},
                         {'passage':'P57-1'},{'passage':'P58-99'},{'passage':'D1'},
                         {'passage':'P58-1','quote':pages[0]['text']},
                         {'pdf_page':58,'quote':pages[0]['text']}]:
            with self.subTest(citation=citation):
                raw={'offers':[offer([citation])]}
                resolved,_,issues=resolve_response(raw,pages)
                self.assertTrue(issues)
                self.assertEqual(resolved['offers'][0]['citations'][0],citation)

    def test_stale_or_forged_saved_reference_maps_are_rejected(self):
        pages=self.pages('Original.');mapping=reference_map(pages)
        for changed in [{},{'P58-1':{**mapping['P58-1'],'quote':'Anderer Text.'}}]:
            with self.assertRaisesRegex(ValueError,'original source window'):
                resolve_response({'offers':[]},pages,saved_map=changed)
        with self.assertRaisesRegex(ValueError,'Ambiguous primary page'):
            reference_map(pages+self.pages('Anderer Text.'))

    def test_multiple_offers_share_a_passage_and_multiple_passages_support_one_offer(self):
        document=doc('D1','local',[(1,'Ein Sensor und eine Kamera sind separat bestellbar.\n\nJeweils gegen Aufpreis.')])
        raw={'offers':[offer([{'passage':'P1-1'},{'passage':'P1-2'}],name=name) for name in ['Sensor','Kamera']]}
        result=extract_offers(baseline([document]),[document],SimpleNamespace(extract=lambda _:copy.deepcopy(raw)))
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(len(result['offers']),2)
        self.assertEqual([o['gemini'] for o in result['offers']],raw['offers'])
        for record in result['offers']:
            self.assertEqual(len(record['derived']['citations']),2)
            self.assertEqual(len(record['derived']['review_context']),2)
        response=result['gemini_responses'][-1]
        self.assertEqual(response['reference_protocol'],'offer-passage-labels-v1')
        self.assertEqual(set(response['reference_map']),{'P1-1','P1-2'})
        self.assertNotIn('quote_protocol',response)

    def test_unknown_selection_stays_blocking_after_offline_reaudit_without_paid_repair(self):
        document=doc('D1','local',[(1,'Ein Sensor ist separat bestellbar.')])
        raw={'offers':[offer([{'passage':'P1-1'},{'passage':'P1-99'}])]}
        result=extract_offers(baseline([document]),[document],SimpleNamespace(extract=lambda _:raw))
        audited=repair_saved_result(result,[document],SimpleNamespace(extract=lambda _:self.fail('No call')))
        self.assertEqual(audited['status'],'incomplete')
        self.assertTrue(any(f['code']=='unknown_passage_id' for f in audited['review_findings']))
        with self.assertRaisesRegex(ValueError,'offer validation'):
            extract_timing(audited,[document],SimpleNamespace(extract=lambda _:self.fail('No call')))

    def test_old_quote_requests_cannot_supply_new_label_responses_from_cache(self):
        from bauvo_working_units.cache import provider_request, digest
        from bauvo_working_units.page_flow import _prompt
        document=doc('D1','local',[(1,'Ein Sensor ist separat bestellbar.')]);prior=baseline([document])
        current=[]
        extract_offers(prior,[document],SimpleNamespace(extract=lambda p:(current.append(p) or {'offers':[]})))
        request=provider_request(current[0],{})
        old=copy.deepcopy(request);old['response_schema']['properties']['offers']['items']['properties']['citations']['items']={
            'type':'object','properties':{'pdf_page':{'type':'integer'},'quote':{'type':'string'}}}
        old['prompt']='PAGE_OFFERS.\nHistorical quote prompt'
        raw={'offers':[offer([{'pdf_page':1,'quote':'Ein Sensor ist separat bestellbar.'}])]}
        saved={'request':old,'request_sha256':digest(old),'response':raw,'raw_text':None,'finish_reason':'STOP'}
        saved['response_sha256']=digest({k:saved[k] for k in ('response','raw_text','finish_reason')})
        with TemporaryDirectory() as directory:
            (Path(directory)/(digest(old)+'.json')).write_text(json.dumps(saved))
            no_calls=BudgetedModel(lambda:self.fail('No provider construction'),CallBudget(0))
            with self.assertRaises(CallLimitExceeded):
                extract_offers(prior,[document],CachedModel(no_calls,directory,{}))


if __name__ == '__main__':
    unittest.main()
