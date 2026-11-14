"""Actual Aufgaben wire format and local citation binding; no provider calls."""
import copy
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from bauvo_working_units.cache import CachedModel
from bauvo_working_units.call_budget import BudgetedModel, CallBudget
from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.offer_stage import extract_offers
from bauvo_working_units.project_facts import collect_project_facts
from bauvo_working_units.project_flow import Document


def document(text, key='blb'):
    return Document('D1', 'Synthetic source', key+'.pdf', schema_version=6,
                    source_key=key, pages=({'pdf_page': 1, 'text': text},))


def data(prompt):
    return json.loads(prompt.split('SOURCE DATA:\n', 1)[1])


def task(refs):
    return {'working_units': [{'name': 'Anschluss herstellen', 'action': 'Anschluss herstellen',
        'actor': 'Auftragnehmer', 'performed_by': None,
        'citations': [{'evidence_id': ref} for ref in refs]}], 'carry_forward': ''}


def contract_facts():
    contract = document('Die Anschlussleistung ist im Festpreis enthalten.\n'
                        'Eine nicht ausgewählte Vertragsklausel bleibt nur in der Quelldatei.', 'contract')
    facts = collect_project_facts([contract], SimpleNamespace(extract=lambda p: {
        'ansprueche': [{'aussage': 'Die Anschlussleistung gehört zum Festpreis.', 'belege': ['S1']}]}))
    return contract, facts


class AufgabenLabelledInputTests(unittest.TestCase):
    def test_fact_evidence_in_the_contract_window_keeps_its_quote_without_a_segment_label(self):
        local = document('Anschluss herstellen.', 'contract')
        facts = collect_project_facts([local], SimpleNamespace(extract=lambda p: {
            'ansprueche': [{'aussage': 'Anschluss ist enthalten.', 'belege': ['S1']}]}))
        before = copy.deepcopy(facts)
        prompts = []
        result = extract_page_units([local], SimpleNamespace(extract=lambda p:
            (prompts.append(p), task(['S1']))[1]), project_facts=facts, stop_after='PAGE_EXTRACTION')
        wire = data(prompts[0])
        reference = wire['project_facts']['facts'][0]['citations'][0]
        self.assertNotIn('evidence_id', reference)
        self.assertEqual(wire['source_pages'][0]['text'], local.pages[0]['text'])
        self.assertEqual(reference['quote'], 'Anschluss herstellen.')
        self.assertEqual(reference['source_key'], 'contract')
        self.assertEqual(reference['document_id'], 'D1')
        self.assertEqual(reference['pdf_page'], 1)
        self.assertEqual(prompts[0].count('Anschluss herstellen.'), 2)
        self.assertEqual(facts, before)
        self.assertEqual(result['working_units'][0]['derived']['citations'][0]['quote'], 'Anschluss herstellen.')

    def test_window_and_only_selected_fact_evidence_are_sent_without_catalog_or_full_sources(self):
        contract, facts = contract_facts()
        local = document('  Anschluss herstellen.\n\n☑ Auswahl bestätigt.\n')
        local = replace(local, pages=({**local.pages[0], 'checkboxes': [{'checked': True, 'text': 'Auswahl bestätigt.'}]},))
        before = copy.deepcopy(facts)
        prompts = []
        def provider(prompt):
            prompts.append(prompt)
            return task(['S1'])
        result = extract_page_units([local], SimpleNamespace(extract=provider),
                                    project_facts=facts, stop_after='PAGE_EXTRACTION')
        wire = data(prompts[0])
        self.assertNotIn('evidence_catalog', wire)
        self.assertNotIn('evidence_catalog', prompts[0])
        self.assertNotIn('source_documents', wire['project_facts'])
        self.assertNotIn('nicht ausgewählte Vertragsklausel', prompts[0])
        page = wire['source_pages'][0]
        self.assertEqual(page['text'], local.pages[0]['text'])
        self.assertTrue(page['primary'])
        self.assertEqual(page['checkboxes'], local.pages[0]['checkboxes'])
        self.assertNotIn('segments', page)
        self.assertEqual(wire['project_facts']['facts'][0]['interpretation'],
                         facts['facts'][0]['derived']['interpretation'])
        citation = wire['project_facts']['facts'][0]['citations'][0]
        self.assertNotIn('evidence_id', citation)
        self.assertEqual(citation['quote'], contract.pages[0]['text'].splitlines(keepends=True)[0])
        for key in ('start', 'end', 'page_sha256', 'catalog_protocol'):
            self.assertNotIn(key, citation)
        self.assertNotIn('text', wire['project_facts']['facts'][0])
        self.assertEqual(facts, before)
        self.assertEqual(result['review_findings'], [])

    def test_blb_labels_bind_only_blb_segments_while_contract_quotes_remain_context(self):
        _, facts = contract_facts()
        local = document('Anschluss herstellen.')
        raw = task(['S1'])
        prompts = []
        result = extract_page_units([local], SimpleNamespace(extract=lambda p: (prompts.append(p), copy.deepcopy(raw))[1]),
                                    project_facts=facts, stop_after='PAGE_EXTRACTION')
        unit = result['working_units'][0]
        self.assertEqual(unit['gemini'], raw['working_units'][0])
        citations = unit['derived']['citations']
        self.assertEqual([c['source_key'] for c in citations], ['blb'])
        self.assertEqual([c['quote'] for c in citations], ['Anschluss herstellen.'])
        context = data(prompts[0])['project_facts']['facts'][0]['citations'][0]
        self.assertEqual(context['quote'], 'Die Anschlussleistung ist im Festpreis enthalten.\n')
        self.assertNotIn('evidence_id', context)
        self.assertTrue(all(c['evidence_id'].startswith('E') for c in citations))
        response = result['gemini_responses'][0]
        self.assertEqual(response['gemini'], raw)
        self.assertEqual(response['reference_map'],
                         {'S1': [citations[0]['evidence_id']]})
        self.assertEqual([b['source_label'] for b in response['citation_evidence']['bindings']], ['S1'])

    def test_unknown_or_unsent_labels_remain_findings_without_an_extra_model_call(self):
        contract, facts = contract_facts()
        hidden = facts['reference_map']['S2']
        selected_contract_evidence = facts['reference_map']['S1']
        for reference in ('S2', 'S999', hidden, selected_contract_evidence, None):
            calls = []
            def provider(prompt):
                calls.append(prompt)
                return task([reference])
            result = extract_page_units([document('Anschluss herstellen.')],
                SimpleNamespace(extract=provider), project_facts=facts, stop_after='PAGE_EXTRACTION')
            with self.subTest(reference=reference):
                self.assertEqual(len(calls), 1)
                self.assertIn('unknown_evidence_id', {f['code'] for f in result['review_findings']})
                self.assertEqual(result['working_units'][0]['gemini'], task([reference])['working_units'][0])

    def test_overlap_markers_carry_and_known_tasks_remain_available_with_request_local_labels(self):
        local = replace(document('unused'), pages=tuple(
            {'pdf_page': n, 'text': f'Anschluss {n} herstellen.'} for n in (1, 2, 3)))
        calls = []
        def provider(prompt):
            wire = data(prompt)
            calls.append(wire)
            from bauvo_working_units.phases.aufgaben.payload import legacy_input
            legacy, _ = legacy_input(wire)
            primary = [p for p in legacy['source_pages'] if p['primary']][-1]
            answer = task([primary['segments'][0][0]])
            answer['carry_forward'] = 'Fortsetzung Anschlussplanung'
            return answer
        result = extract_page_units([local], SimpleNamespace(extract=provider),
                                    chunk_pages=2, overlap_pages=1, stop_after='PAGE_EXTRACTION')
        self.assertEqual(len(calls), 2)
        self.assertEqual([(p['pdf_page'], p['primary']) for p in calls[1]['source_pages']], [(2, False), (3, True)])
        self.assertNotIn('prior_context', calls[1])
        self.assertEqual(calls[1]['known_items'][0]['id'], result['working_units'][0]['id'])
        self.assertEqual([u['derived']['citations'][0]['pdf_page'] for u in result['working_units']], [2, 3])

    def test_identical_contract_and_blb_text_cannot_bind_a_blb_citation_to_the_contract(self):
        local = document('Anschluss herstellen.')
        contract = document('Anschluss herstellen.', 'contract')
        facts = collect_project_facts([contract], SimpleNamespace(extract=lambda p: {
            'ansprueche': [{'aussage': 'Anschluss herstellen.', 'belege': ['S1']}]}))
        result = extract_page_units([local], SimpleNamespace(extract=lambda p: task(['S1'])),
                                    project_facts=facts, stop_after='PAGE_EXTRACTION')
        citations = result['working_units'][0]['derived']['citations']
        self.assertEqual([c['source_key'] for c in citations], ['blb'])
        self.assertNotEqual(citations[0]['evidence_id'], facts['reference_map']['S1'])
        self.assertEqual(result['gemini_responses'][0]['reference_map'], {'S1': [citations[0]['evidence_id']]})

    def test_completed_compact_requests_replay_from_cache_with_zero_provider_calls(self):
        _, facts = contract_facts()
        local = document('Anschluss herstellen.')
        with TemporaryDirectory() as tmp:
            calls = []
            def provider(prompt):
                calls.append(prompt)
                return task(['S1'])
            first = extract_page_units([local], CachedModel(SimpleNamespace(extract=provider), tmp, {}),
                                        project_facts=facts, stop_after='PAGE_EXTRACTION')
            saved = {p: p.read_bytes() for p in Path(tmp).glob('*.json')}
            offline = BudgetedModel(lambda: self.fail('Unexpected provider construction'), CallBudget(0))
            replayed = extract_page_units([local], CachedModel(offline, tmp, {}),
                                           project_facts=facts, stop_after='PAGE_EXTRACTION')
            self.assertEqual(first, replayed)
            self.assertEqual(len(calls), 1)
            self.assertEqual(saved, {p: p.read_bytes() for p in saved})

    def test_next_stage_receives_original_citations_while_extraction_raw_keeps_short_labels(self):
        contract, facts = contract_facts()
        local = document('Anschluss herstellen.')
        raw = task(['S1'])
        baseline = extract_page_units([local], SimpleNamespace(extract=lambda p: copy.deepcopy(raw)),
                                       project_facts=facts, stop_after='PAGE_EXTRACTION')
        before = copy.deepcopy(baseline)
        calls = []
        def provider(prompt):
            self.assertTrue(prompt.startswith('PAGE_OFFERS.'))
            calls.append(data(prompt))
            return {'offers': []}
        result = extract_offers(baseline, [local], SimpleNamespace(extract=provider), context_documents=[contract])
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['working_units'][0]['gemini'], raw['working_units'][0])
        self.assertEqual(result['working_units'][0]['derived']['citations'],
                         baseline['working_units'][0]['derived']['citations'])
        self.assertEqual(result['review_findings'], [])
        self.assertEqual(baseline, before)
