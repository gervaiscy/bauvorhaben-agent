"""Historical source-selection fixtures, not a measure of provider accuracy.

Check the production wire contract, source restoration and context boundaries.
All provider answers below are authored; no API calls are made.
"""
import copy
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

from bauvo_working_units.citation_evidence import resolve
from bauvo_working_units.page_flow import _prompt, _view, extract_page_units
from bauvo_working_units.phases.aufgaben.payload import legacy_input as model_input
from bauvo_working_units.phases.aufgaben.response import derived_data, for_resolution, valid_selection
from bauvo_working_units.project_facts import collect_project_facts
from bauvo_working_units.project_flow import Document
from bauvo_working_units.schemas import RESPONSE_SCHEMAS


PROMPT = Path(__file__).resolve().parents[1] / 'src/bauvo_working_units/phases/aufgaben/prompt.txt'


def examples():
    return json.loads(Path(__file__).with_name('legacy_aufgaben_examples.json').read_text())


def payload_for(source):
    pages = [{**{k: p[k] for k in ('source_key', 'document_id', 'pdf_page', 'primary')},
              'text': ''.join(text for _, text in p['segments'])} for p in source['source_pages']]
    return {'source_key': pages[0]['source_key'], 'document': {'id': pages[0]['document_id']},
            'source_pages': pages, 'project_facts': copy.deepcopy(source['project_facts'])}


def bind(source, answer):
    payload = payload_for(source)
    _, mapping = model_input(payload)
    resolved, _, findings = resolve(for_resolution(answer), payload['source_pages'], [],
                                   payload['source_key'], payload['document']['id'], reference_map=mapping)
    doc = Document(payload['document']['id'], 'Fictional example', 'fictional.pdf', schema_version=6,
                   source_key=payload['source_key'], pages=tuple(payload['source_pages']))
    chunk = SimpleNamespace(pages=[p['pdf_page'] for p in doc.pages])
    tasks = [{'id': None, 'gemini': raw, 'derived': derived_data(raw, row, doc, chunk)}
             for raw, row in zip(answer['working_units'], resolved['working_units'])]
    return tasks, findings


class AufgabenPromptExampleTests(unittest.TestCase):
    def test_historical_fixtures_keep_their_original_input_and_response_contract(self):
        pairs = examples()
        self.assertEqual(len(pairs), 8)
        for source, answer in pairs:
            with self.subTest(answer=answer):
                wire, _ = model_input(payload_for(source))
                self.assertEqual(wire['source_pages'], source['source_pages'])
                self.assertEqual(wire['project_facts'], source['project_facts'])
                self.assertEqual(set(answer), {'working_units', 'carry_forward'})
                self.assertIsInstance(answer['carry_forward'], str)
                self.assertTrue(all(valid_selection(row) for row in answer['working_units']))

    def test_every_selected_paragraph_and_actor_survives_without_mutating_the_answer(self):
        for source, answer in examples():
            before = copy.deepcopy((source, answer))
            selected = {label: text for page in source['source_pages'] for label, text in page['segments']
                        if label is not None}
            tasks, findings = bind(source, answer)
            self.assertEqual(findings, [])
            self.assertEqual(len(tasks), len(answer['working_units']))
            for task, row in zip(tasks, answer['working_units']):
                self.assertEqual(task['gemini'], row)
                self.assertEqual(_view(task)['actor'], row['actor'])
                for label in row['evidence_ids']:
                    self.assertIn(selected[label], _view(task)['action'])
            self.assertEqual((source, answer), before)

    def test_equivalent_consents_keep_both_companies_and_the_source_supported_withdrawal_role(self):
        source, answer = examples()[2]
        tasks, findings = bind(source, answer)
        self.assertEqual(findings, [])
        self.assertEqual(len(tasks), 4)
        self.assertEqual(_view(tasks[1])['actor'], _view(tasks[3])['actor'])
        self.assertNotEqual(_view(tasks[0])['actor'], _view(tasks[2])['actor'])
        receipt = source['source_pages'][0]['segments'][-1][1]
        self.assertTrue(all(receipt not in _view(t)['action'] for t in tasks))

    def test_penalty_selects_both_its_own_clause_and_the_full_deadline_definition(self):
        source, answer = examples()[3]
        tasks, _ = bind(source, answer)
        texts = {label: text for label, text in source['source_pages'][0]['segments'] if label}
        self.assertIn(texts['S1'], _view(tasks[1])['action'])
        self.assertIn(texts['S3'], _view(tasks[1])['action'])
        self.assertNotIn(texts['S2'], _view(tasks[1])['action'])

    def test_standard_scope_and_exclusion_stay_with_the_positive_construction_task(self):
        source, answer = examples()[4]
        tasks, _ = bind(source, answer)
        self.assertEqual(len(tasks), 1)
        texts = {label: text for label, text in source['source_pages'][0]['segments'] if label}
        for label in ('S1', 'S2', 'S3'):
            self.assertIn(texts[label], _view(tasks[0])['action'])
        self.assertNotIn(texts['S4'], _view(tasks[0])['action'])

    def test_mixed_paragraph_preserves_joint_and_individual_actors_on_shared_evidence(self):
        source, answer = examples()[5]
        tasks, _ = bind(source, answer)
        self.assertEqual(len(tasks), 3)
        self.assertNotEqual(_view(tasks[0])['actor'], _view(tasks[1])['actor'])
        self.assertEqual(_view(tasks[1])['actor'], _view(tasks[2])['actor'])
        self.assertTrue(all(t['derived']['citations'] == tasks[0]['derived']['citations'] for t in tasks))

    def test_context_only_amendment_reaches_real_extraction_but_is_not_a_selectable_window_label(self):
        source, answer = examples()[1]
        context = source['project_facts']['facts'][0]
        evidence = context['citations'][0]
        amendment = Document(evidence['document_id'], 'Fictional amendment', 'amendment.pdf',
                             schema_version=6, source_key=evidence['source_key'],
                             pages=({'pdf_page': evidence['pdf_page'], 'text': evidence['quote']},))
        facts = collect_project_facts([amendment], SimpleNamespace(extract=lambda _: {
            'ansprueche': [{'aussage': context['interpretation'], 'belege': ['S1']}]}))
        self.assertEqual(facts['status'], 'recorded')
        payload = payload_for(source)
        offer = Document('B1', 'Fictional offer', 'offer.pdf', schema_version=6,
                         source_key=payload['source_key'], pages=tuple(payload['source_pages']))
        calls = []
        def provider(prompt):
            calls.append(json.loads(prompt.split('SOURCE DATA:\n', 1)[1]))
            return copy.deepcopy(answer)
        result = extract_page_units([offer], SimpleNamespace(extract=provider), project_facts=facts,
                                    stop_after='PAGE_EXTRACTION')
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['review_findings'], [])
        self.assertEqual(result['gemini_responses'][0]['gemini'], answer)
        citation = calls[0]['project_facts']['facts'][0]['citations'][0]
        self.assertEqual(citation['quote'], evidence['quote'])
        self.assertEqual(citation['document_id'], 'B2')
        self.assertNotIn('evidence_id', citation)
        self.assertNotIn(evidence['quote'], result['working_units'][0]['derived']['source_text'])
        self.assertEqual({c['document_id'] for c in result['working_units'][0]['derived']['citations']}, {'B1'})
        self.assertEqual(len(result['gemini_responses'][0]['reference_map']), 5)

    def test_fictional_inputs_do_not_become_actual_project_evidence(self):
        text = 'Der Auftragnehmer übergibt den Wartungsplan.'
        payload = {'source_key': 'actual', 'document': {'id': 'A1'},
                   'source_pages': [{'pdf_page': 1, 'primary': True, 'text': text}]}
        prompt = _prompt('PAGE_EXTRACTION', payload, 'de')
        wire = json.loads(prompt.split('SOURCE DATA:\n', 1)[1])
        self.assertEqual(wire['source_pages'][0]['text'], text)
        self.assertEqual(wire['project_facts']['facts'], [])
        self.assertEqual(wire['document']['id'], 'A1')

    def test_new_evaluation_sources_are_kept_out_of_the_prompt_demonstrations(self):
        identifiers = {
            'task-evidence-credit-reference-context-only-amendment',
            'task-evidence-equivalent-consents-separate-documents',
            'task-evidence-penalty-with-later-deadline-definition',
            'task-evidence-series-specification-as-scope',
            'task-evidence-independent-actions-different-parties-one-paragraph',
        }
        cases = json.loads((Path(__file__).resolve().parents[1] / 'evaluation/cases.json').read_text())['cases']
        held_out = [c for c in cases if c['id'] in identifiers]
        self.assertEqual({c['id'] for c in held_out}, identifiers)
        demonstrations = {text.strip() for source, _ in examples() for page in source['source_pages']
                          for label, text in page['segments'] if label}
        for case in held_out:
            for page in case['pages']:
                self.assertFalse(set(page['text'].strip().split('\n\n')) & demonstrations)
