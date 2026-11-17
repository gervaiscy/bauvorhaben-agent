"""Deterministic review context is original source text, never selected evidence."""
import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from bauvo_working_units.cache import CachedModel
from bauvo_working_units.call_budget import BudgetedModel, CallBudget
from bauvo_working_units.citation_evidence import evidence_span
from bauvo_working_units.citation_repair import repair_saved_result
from bauvo_working_units.phases.offers.context import review_context
from bauvo_working_units.phases.offers.stage import extract_offers
from test_offer_boundaries import baseline, doc, offer, wire


def span(document, page_number, quote):
    page = next(p for p in document.pages if p['pdf_page'] == page_number)
    page = {**page, 'source_key': document.source_key, 'document_id': document.id}
    start = page['text'].index(quote)
    return evidence_span(page, start, start + len(quote))


class OfferReviewContextTests(unittest.TestCase):
    def test_reference_at_paragraph_start_keeps_previous_paragraph_and_exact_whitespace(self):
        document = doc('D1','local',[(1,'Ältere Regel.\r\n\r\nDie Pergola ist separat zu vereinbaren.\r\n \t\r\n'
                                     '  Gleiches gilt für Pavillons.\r\nEine Grenze.\r\n\r\nSpäteres Thema.')])
        selected = span(document,1,'Gleiches gilt für Pavillons.')
        before = copy.deepcopy(selected)
        entry = review_context([selected],document)[0]
        self.assertEqual(entry['kind'],'surrounding_source_context')
        self.assertEqual(entry['generated_by'],'application')
        context = entry['spans'][0]
        self.assertEqual(context['text'],'Die Pergola ist separat zu vereinbaren.\r\n \t\r\n'
                                         '  Gleiches gilt für Pavillons.\r\nEine Grenze.\r\n')
        self.assertEqual(context['text'],document.pages[0]['text'][context['start']:context['end']])
        self.assertEqual(context['page_sha256'],selected['page_sha256'])
        self.assertNotIn('evidence_id',context)
        self.assertEqual(selected,before)

    def test_mid_paragraph_quote_keeps_whole_paragraph_but_not_previous_one(self):
        document = doc('D1','local',[(1,'Anderes Thema.\n\nEine Pergola ist separat zu vereinbaren. '
                                     'Gleiches gilt für Pavillons.\n\nNoch etwas.')])
        context = review_context([span(document,1,'Gleiches gilt für Pavillons.')],document)[0]
        self.assertEqual(context['spans'][0]['text'],'Eine Pergola ist separat zu vereinbaren. '
                                                   'Gleiches gilt für Pavillons.\n')

    def test_quote_across_paragraphs_keeps_all_original_intervening_text(self):
        document = doc('D1','local',[(1,'Vorher.\n\nErste Leistung.\n\nZweite Leistung.\n\nNachher.')])
        context = review_context([span(document,1,'Erste Leistung.\n\nZweite Leistung.')],document)[0]
        self.assertEqual(context['spans'][0]['text'],'Vorher.\n\nErste Leistung.\n\nZweite Leistung.\n')

    def test_reference_at_page_start_can_include_previous_page_outside_current_window(self):
        document = doc('D1','local',[(1,'Früheres Thema.\n\nGesonderte Vereinbarung erforderlich.'),
                                     (2,'Gleiches gilt für Pavillons.\n\nSpäteres Thema.')])
        entry = review_context([span(document,2,'Gleiches gilt für Pavillons.')],document)[0]
        self.assertEqual([s['pdf_page'] for s in entry['spans']],[1,2])
        self.assertEqual(entry['spans'][0]['text'],'Gesonderte Vereinbarung erforderlich.')

    def test_page_gaps_blank_previous_page_and_document_start_do_not_import_other_sources(self):
        for pages in [[(2,'Zusatzleistung.')],[(1,'\n\t\n'),(2,'Zusatzleistung.')],
                      [(1,'Fremder Zusammenhang.'),(3,'Zusatzleistung.')]]:
            with self.subTest(pages=pages):
                document = doc('D1','local',pages)
                number = pages[-1][0]
                entry = review_context([span(document,number,'Zusatzleistung.')],document)[0]
                self.assertEqual([s['pdf_page'] for s in entry['spans']],[number])

    def test_invalid_unbound_or_stale_spans_get_no_context(self):
        document = doc('D1','local',[(1,'Ein Angebot.')]); valid = span(document,1,'Ein Angebot.')
        invalid = [None,{'pdf_page':1,'quote':'Ein Angebot.'}]
        invalid += [{**valid,key:value} for key,value in [
            ('source_key','other'),('document_id','D2'),('page_sha256','old'),('quote','Umformuliert'),
            ('start',True),('end',-1),('end',999),('pdf_page',99)]]
        self.assertEqual(review_context(invalid,document),[])

    def test_same_paragraph_can_support_separate_rows_without_merging_or_leaking_to_wire(self):
        text = 'Beim Lieferanten sind Sensor und Kamera jeweils separat beauftragbar.'
        document = doc('D1','local',[(1,text)]); prior = baseline([document]); captured=[]
        raw = {'offers':[offer([{'passage':'P1-1'}],name=name) for name in ['Sensor','Kamera']]}
        def provider(prompt):
            captured.append(wire(prompt));return copy.deepcopy(raw)
        result = extract_offers(prior,[document],SimpleNamespace(extract=provider))
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(len(result['offers']),2)
        self.assertEqual([o['gemini'] for o in result['offers']],raw['offers'])
        for record in result['offers']:
            self.assertEqual(record['derived']['review_context'][0]['spans'][0]['text'],text)
            self.assertEqual(len(record['derived']['citations']),1)
            self.assertNotIn('review_context',record['gemini'])
        self.assertEqual(set(captured[0]),{'source_pages','project_facts'})

    def test_invalid_quote_stays_blocking_even_with_source_context_for_a_second_quote(self):
        document = doc('D1','local',[(1,'Beim Lieferanten ist eine Kamera separat beauftragbar.')])
        citations = [{'passage':'P1-1'}, {'passage':'P1-99'}]
        raw = {'offers':[offer(citations)]}
        result = extract_offers(baseline([document]),[document],SimpleNamespace(extract=lambda _:raw))
        self.assertEqual(result['status'],'incomplete')
        record = result['offers'][0]
        self.assertEqual([c['citation_position'] for c in record['derived']['review_context']],[0])
        self.assertEqual(record['gemini'],raw['offers'][0])

    def test_cache_replay_and_offline_audit_rebuild_context_without_provider_calls(self):
        document = doc('D1','local',[(1,'Eine Pergola ist separat zu vereinbaren.\n\nGleiches gilt für Pavillons.')])
        prior = baseline([document]);raw = {'offers':[offer([{'passage':'P1-2'}])]}
        with TemporaryDirectory() as directory:
            model = CachedModel(SimpleNamespace(extract=lambda _:copy.deepcopy(raw)),Path(directory),{})
            first = extract_offers(prior,[document],model)
            no_calls = BudgetedModel(lambda:self.fail('No provider construction'),CallBudget(0))
            replay = extract_offers(prior,[document],CachedModel(no_calls,Path(directory),{}))
        self.assertEqual(first,replay)
        replay['offers'][0]['derived'].pop('review_context')
        before = copy.deepcopy(replay)
        audited = repair_saved_result(replay,[document],model=SimpleNamespace(extract=lambda _:self.fail('No call')))
        self.assertEqual(audited['offers'][0]['derived']['review_context'],first['offers'][0]['derived']['review_context'])
        self.assertEqual(audited['offers'][0]['gemini'],raw['offers'][0])
        self.assertEqual(before,replay)
