from consequence_fixtures import assessment_answer
"""Task selection, literal review text, shared evidence and stage handoff offline."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from bauvo_working_units.cache import CachedModel
from bauvo_working_units.call_budget import BudgetedModel, CallBudget
from bauvo_working_units.page_flow import extract_page_units, _view
from bauvo_working_units.offer_stage import extract_offers
from bauvo_working_units.schemas import RESPONSE_SCHEMAS
from bauvo_working_units.trace import RawRunRecorder, TracingModel
from test_aufgaben_labelled_input import document, data, contract_facts


def answer(*rows, carry=''):
    return {'working_units': list(rows), 'carry_forward': carry}


def task(title='Baustrom beantragen', refs=('S1',), actor=None):
    return {'title': title, 'actor': actor, 'evidence_ids': list(refs)}


def extract(doc, raw, **kwargs):
    return extract_page_units([doc], SimpleNamespace(extract=lambda _: copy.deepcopy(raw)),
                              stop_after='PAGE_EXTRACTION', **kwargs)


class SourceSelectedTaskTests(unittest.TestCase):
    def test_live_schema_requests_title_nullable_actor_and_source_labels(self):
        schema = RESPONSE_SCHEMAS['PAGE_EXTRACTION']
        unit = schema['properties']['working_units']['items']
        self.assertEqual(set(unit['required']), {'title', 'actor', 'quote'})
        self.assertFalse(unit['additionalProperties'])
        self.assertEqual(unit['properties']['quote']['minLength'], 1)
        self.assertEqual(unit['properties']['actor'], {'anyOf': [{'type': 'string'}, {'type': 'null'}]})

    def test_separate_tasks_with_shared_evidence_keep_distinct_ids_and_literal_review_text(self):
        text = ('Der Bauherr beantragt den Baustromanschluss und stellt einen Verteiler bereit.\n'
                'Beides ist nur bei gesonderter Vereinbarung geschuldet.')
        raw = answer(task('Baustromanschluss beantragen', ('S1',), actor='Bauherr'),
                     task('Baustromverteiler bereitstellen', ('S1',), actor='Bauherr'))
        before = copy.deepcopy(raw)
        result = extract(document(text), raw)
        self.assertEqual(len(result['working_units']), 2)
        first, second = result['working_units']
        self.assertNotEqual(first['id'], second['id'])
        for i, unit in enumerate(result['working_units']):
            self.assertEqual(unit['gemini'], raw['working_units'][i])
            self.assertEqual(unit['derived']['source_text'], text)
            self.assertEqual([c['quote'] for c in unit['derived']['citations']],
                             text.splitlines(keepends=True))
            self.assertEqual(_view(unit)['action'], text)
            self.assertEqual(unit['derived']['actor'], 'Bauherr')
            self.assertEqual(_view(unit)['actor'], 'Bauherr')
        self.assertEqual(result['gemini_responses'][0]['gemini'], raw)
        self.assertEqual(raw, before)
        self.assertEqual(result['review_findings'], [])

    def test_literal_text_uses_source_order_whitespace_and_retains_qualifications(self):
        text = ('Baustrom bereitstellen.\n\n'
                'Nur bei Beauftragung; alternativ kann der Auftragnehmer übernehmen.\n')
        raw = answer(task('Navigationstitel ohne Beweiswert', ('S2', 'S1', 'S2')))
        result = extract(document(text), raw)
        unit = result['working_units'][0]
        self.assertEqual(unit['derived']['source_text'], text)
        self.assertEqual(len(unit['derived']['citations']), 2)
        self.assertEqual(unit['gemini']['evidence_ids'], ['S2', 'S1', 'S2'])
        self.assertEqual(_view(unit)['name'], 'Navigationstitel ohne Beweiswert')
        self.assertNotIn('Navigationstitel', _view(unit)['action'])

    def test_omitted_passages_remain_visible_and_do_not_join_disconnected_sentences(self):
        text = 'Baustrom beantragen.\n\nAndere Regelung.\n\nNur nach Freigabe.\n'
        result = extract(document(text), answer(task(refs=('S1', 'S3'))))
        restored = result['working_units'][0]['derived']['source_text']
        self.assertIn('[separate source passage]', restored)
        self.assertNotIn('Andere Regelung.', restored)
        self.assertIn('Nur nach Freigabe.', restored)

    def test_empty_extraction_is_valid_and_creates_no_task(self):
        result = extract(document('Allgemeine Werbung.'), answer())
        self.assertEqual(result['working_units'], [])
        self.assertEqual(result['status'], 'recorded')

    def test_commissioning_and_execution_keep_distinct_actors_on_the_same_passage(self):
        text = 'Der Bauherr beauftragt einen Installateur mit der Herstellung des Bauwasseranschlusses.'
        raw = answer(task('Installateur beauftragen', actor='Bauherr'),
                     task('Bauwasseranschluss herstellen', actor='Installateur'))
        calls = []
        result = extract_page_units([document(text)], SimpleNamespace(extract=lambda p:
            (calls.append(p), copy.deepcopy(raw))[1]), stop_after='PAGE_EXTRACTION')
        units = result['working_units']
        self.assertEqual(len(calls), 1)
        self.assertEqual([_view(u)['actor'] for u in units], ['Bauherr', 'Installateur'])
        self.assertEqual([u['derived']['source_text'] for u in units], [text, text])
        self.assertEqual([u['gemini'] for u in units], raw['working_units'])
        self.assertEqual(units[0]['derived']['citations'], units[1]['derived']['citations'])
        self.assertEqual(result['review_findings'], [])

    def test_actor_support_from_another_segment_is_preserved_with_task_evidence(self):
        text = 'Zuständig: Auftraggeber.\nBaustrom beantragen.\n'
        raw = answer(task(refs=('S1',), actor='Auftraggeber'))
        result = extract(document(text), raw)
        unit = result['working_units'][0]
        self.assertEqual(unit['derived']['source_text'], text)
        self.assertEqual(_view(unit)['actor'], 'Auftraggeber')
        self.assertEqual([c['quote'] for c in unit['derived']['citations']], text.splitlines(keepends=True))
        self.assertEqual(unit['gemini'], raw['working_units'][0])

    def test_unclear_actor_stays_null_without_using_title_or_general_knowledge(self):
        raw = answer(task('Bauherr: Baustrom bereitstellen', actor=None))
        result = extract(document('Baustrom ist bereitzustellen.'), raw)
        unit = result['working_units'][0]
        self.assertIsNone(unit['derived']['actor'])
        self.assertIsNone(_view(unit)['actor'])
        self.assertEqual(result['review_findings'], [])

    def test_historical_source_selected_view_remains_readable_without_inventing_an_actor(self):
        record = {'id': 'U1', 'gemini': {'title': 'Baustrom', 'evidence_ids': ['S1']},
                  'derived': {'task_protocol': 'selected-source-passages-v1',
                              'title': 'Baustrom', 'source_text': 'Baustrom bereitstellen.'}}
        before = copy.deepcopy(record)
        self.assertEqual(_view(record)['action'], 'Baustrom bereitstellen.')
        self.assertIsNone(_view(record)['actor'])
        self.assertEqual(record, before)

    def test_malformed_selections_and_unknown_ids_are_preserved_without_repair_calls(self):
        invalid = [None, {}, {'title': 'X'}, task(refs=()), task(title='  '),
                   {'title': 'X', 'evidence_ids': 'S1'}, task(refs=(None,)),
                   {'title': 'X', 'evidence_ids': ['S1']},
                   *(task(actor=actor) for actor in ('', '   ', 7, [], {})),
                   task(refs=('S999',)), task(refs=('S1', 'S999'))]
        for row in invalid:
            with self.subTest(row=row):
                raw = answer(row)
                calls = []
                result = extract_page_units([document('Baustrom beantragen.')],
                    SimpleNamespace(extract=lambda p: (calls.append(p), copy.deepcopy(raw))[1]),
                    stop_after='PAGE_EXTRACTION')
                self.assertEqual(len(calls), 1)
                self.assertTrue(result['review_findings'])
                self.assertEqual(result['gemini_responses'][0]['gemini'], raw)
                self.assertEqual(result['working_units'][0]['gemini'], row)
                self.assertNotIn('source_text', result['working_units'][0]['derived'])

    def test_contract_fact_evidence_cannot_be_selected_as_a_blb_window_segment(self):
        _, facts = contract_facts()
        original = copy.deepcopy(facts)
        for label in ('S2', facts['reference_map']['S1']):
            with self.subTest(label=label):
                result = extract(document('Baustrom bereitstellen.'), answer(task(refs=(label,))),
                                 project_facts=facts)
                self.assertIn('unknown_evidence_id', {f['code'] for f in result['review_findings']})
                self.assertNotIn('source_text', result['working_units'][0]['derived'])
        self.assertEqual(facts, original)

    def test_overlap_context_and_known_titles_continue_across_windows(self):
        from dataclasses import replace
        doc = replace(document('unused'), pages=tuple(
            {'pdf_page': n, 'text': f'Teil {n}.\n'} for n in (1, 2, 3)))
        calls = []
        def provider(prompt):
            wire = data(prompt)
            calls.append(wire)
            from bauvo_working_units.phases.aufgaben.payload import legacy_input
            legacy, _ = legacy_input(wire)
            selected = [s[0] for p in legacy['source_pages'] for s in p['segments'] if s[0]]
            return answer(task(f'Aufgabe {len(calls)}', selected), carry='Abschnitt wird fortgesetzt.')
        result = extract_page_units([doc], SimpleNamespace(extract=provider),
            chunk_pages=2, overlap_pages=1, stop_after='PAGE_EXTRACTION')
        self.assertEqual(len(calls), 2)
        self.assertNotIn('prior_context', calls[1])
        self.assertEqual(calls[1]['known_items'][0]['name'], 'Aufgabe 1')
        self.assertEqual(result['working_units'][1]['derived']['source_text'],
                         'Teil 2.\n\n[separate source passage]\nTeil 3.\n')

    def test_all_later_stages_receive_original_text_for_each_separate_task(self):
        text = 'Der Bauherr beauftragt einen Installateur mit der Herstellung des Bauwasseranschlusses.'
        raw = answer(task('Installateur beauftragen', actor='Bauherr'),
                     task('Bauwasseranschluss herstellen', actor='Installateur'))
        seen = {}
        def provider(prompt):
            phase = prompt.split('.', 1)[0]
            wire = data(prompt)
            if phase == 'PAGE_EXTRACTION':
                return copy.deepcopy(raw)
            if phase == 'PAGE_OFFERS':
                return {'offers': []}
            if phase == 'PAGE_CONSEQUENCES':
                seen[phase] = [{'id': label} for label in wire['working_units']]
                for unit in wire['working_units'].values():
                    self.assertEqual(unit['quote'], text)
                    self.assertIn('timing', unit)
                return assessment_answer(wire)
            seen[phase] = wire['working_units']
            for unit in wire['working_units']:
                self.assertEqual(unit['action'], text)
                self.assertEqual(unit['citations'][0]['quote'], text)
                expected = {t['title']: t['actor'] for t in raw['working_units']}
                self.assertEqual(unit['actor'], expected[unit['name']])
                self.assertIsNone(unit['performed_by'])
            rows = []
            for unit in wire['working_units']:
                row = {'unit_id': unit['id']}
                if phase == 'PAGE_TIMING':
                    row.update(status='NOT_STATED', timing_rules=[], missing_evidence=[])
                elif phase == 'PAGE_CONSEQUENCES':
                    row.update(status='NOT_STATED', consequences=[])
                else:
                    row.update(applicability='UNKNOWN', reason='Auswahl fehlt.', applicability_rules=[], missing_evidence=[])
                rows.append(row)
            return {'assessments': rows}
        result = extract_page_units([document(text)], SimpleNamespace(extract=provider))
        self.assertEqual(set(seen), {'PAGE_TIMING', 'PAGE_CONSEQUENCES', 'PAGE_APPLICABILITY'})
        for rows in seen.values():
            self.assertEqual(len(rows), 2)
            self.assertNotEqual(rows[0]['id'], rows[1]['id'])
        self.assertEqual([u['gemini'] for u in result['working_units']], raw['working_units'])
        self.assertEqual(result['review_findings'], [])

    def test_saved_aufgaben_can_continue_to_offers_without_rerunning_extraction(self):
        doc = document('Baustrom bereitstellen.')
        baseline = extract(doc, answer(task(actor='Bauherr')))
        before = copy.deepcopy(baseline)
        def provider(prompt):
            self.assertTrue(prompt.startswith('PAGE_OFFERS.'))
            return {'offers': []}
        result = extract_offers(baseline, [doc], SimpleNamespace(extract=provider))
        self.assertEqual(result['working_units'], baseline['working_units'])
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(baseline, before)

    def test_raw_trace_and_cache_preserve_selections_and_replay_without_provider_calls(self):
        doc = document('Baustrom bereitstellen.')
        raw = answer(task(refs=('S1', 'S1'), actor='Bauherr'))
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            class Provider:
                last_raw_response = json.dumps(raw)
                def extract(self, prompt):
                    return copy.deepcopy(raw)
            model = TracingModel(RawRunRecorder(CachedModel(Provider(), root/'cache', {}),
                                                root/'raw.json'), root/'trace')
            first = extract_page_units([doc], model, stop_after='PAGE_EXTRACTION')
            self.assertEqual(json.loads((root/'raw.json').read_text()), [raw])
            self.assertEqual(json.loads((root/'trace/0001-response.json').read_text()), raw)
            self.assertIn('SOURCE DATA:', (root/'trace/0001-prompt.txt').read_text())
            before = {p: p.read_bytes() for p in (root/'cache').glob('*.json')}
            offline = BudgetedModel(lambda: self.fail('Unexpected provider call'), CallBudget(0))
            replay = extract_page_units([doc], CachedModel(offline, root/'cache', {}), stop_after='PAGE_EXTRACTION')
            self.assertEqual(replay, first)
            self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_cli_reuses_saved_facts_and_writes_review_text_raw_responses_and_trace(self):
        import hashlib
        import sys
        from unittest.mock import patch
        from bauvo_working_units.cli import main
        from bauvo_working_units.core import digest
        from bauvo_working_units.project_flow import load_documents
        from bauvo_working_units.project_facts import collect_project_facts
        text = 'Der Bauherr beantragt Baustrom und stellt einen Verteiler bereit.'
        raw = {'working_units': [
            {'title': 'Baustrom beantragen', 'actor': 'Bauherr', 'quote': text},
            {'title': 'Verteiler bereitstellen', 'actor': 'Bauherr', 'quote': text}]}
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pages = [{'pdf_page': 1, 'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                      'checkboxes': []}]
            (root/'pages.json').write_text(json.dumps({'schema_version': 6, 'source': 'synthetic.pdf',
                                                      'page_count': 1, 'pages': pages}))
            (root/'documents.json').write_text(json.dumps({'schema_version': 6, 'pages_sha256': digest(pages),
                'status': 'proposed', 'documents': [{'document_id': 'D1', 'document_title': 'Contract',
                'pdf_pages': [1], 'uncertain': False}], 'page_assignments': [{'pdf_page': 1, 'document_id': 'D1'}]}))
            docs = load_documents(root/'pages.json', root/'documents.json')
            facts = collect_project_facts(docs, SimpleNamespace(extract=lambda _: {
                'ansprueche': [{'aussage': 'Baustromregelung.', 'belege': ['S1']}]}))
            (root/'facts.json').write_text(json.dumps(facts))
            original = {p: p.read_bytes() for p in (root/'facts.json', root/'pages.json', root/'documents.json')}
            calls = []
            class Provider:
                last_raw_response = json.dumps(raw)
                def __init__(self, *args): pass
                def extract(self, prompt):
                    calls.append(prompt)
                    self_test.assertTrue(prompt.startswith('PAGE_EXTRACTION.'))
                    return copy.deepcopy(raw)
            self_test = self
            argv = ['tasks', '--pages', str(root/'pages.json'), '--documents', str(root/'documents.json'),
                '--project-facts', str(root/'facts.json'), '--stop-after', 'aufgaben', '--project', 'offline-test',
                '--max-new-calls', '1', '--output', str(root/'result.json'), '--trace-dir', str(root/'trace')]
            with patch.object(sys, 'argv', argv), patch('bauvo_working_units.cli.GeminiModel', Provider):
                main()
            result = json.loads((root/'result.json').read_text())
            self.assertEqual(len(calls), 1)
            self.assertEqual(result['project_facts'], facts)
            self.assertEqual(result['completed_passes'], ['PAGE_EXTRACTION'])
            self.assertEqual([u['derived']['source_text'] for u in result['working_units']], [text, text])
            self.assertEqual([u['derived']['actor'] for u in result['working_units']], ['Bauherr', 'Bauherr'])
            self.assertEqual(json.loads((root/'result.gemini-raw.json').read_text()), [raw])
            self.assertEqual(json.loads((root/'result.units-only.json').read_text()), {'working_units': raw['working_units']})
            self.assertEqual((root/'trace/0001-prompt.txt').read_text(), calls[0])
            self.assertEqual({p: p.read_bytes() for p in original}, original)
