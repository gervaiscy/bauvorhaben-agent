"""Paragraph selection is atomic; original evidence stays line-bound and exact."""
import copy
import json
from types import SimpleNamespace
import unittest

from bauvo_working_units.citation_evidence import catalog, resolve
from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.phases.aufgaben.passages import passage_catalog, REFERENCE_PROTOCOL
from bauvo_working_units.phases.aufgaben.payload import legacy_input as model_input
from test_aufgaben_labelled_input import document, data


def response(*titles, refs=('S1',)):
    return {'working_units': [{'title': t, 'actor': None, 'evidence_ids': list(refs)} for t in titles],
            'carry_forward': ''}


class AufgabenParagraphTests(unittest.TestCase):
    def test_one_label_restores_credit_prerequisite_timing_deductions_and_offset_rule(self):
        paragraph = ('Der Auftragnehmer erstattet die Gebühr, wenn der Bauherr alle Geräte\n'
                     'rechtzeitig zurückgibt. Die Erstattung erfolgt binnen zwei Wochen.\n'
                     'Bei Beschädigung wird der Betrag gemindert. Eine vorherige\n'
                     'Verrechnung ist ausgeschlossen.\n')
        original = paragraph + '\nDie Zentrale ist montags erreichbar.\n'
        calls = []
        raw = response('Gebühr erstatten')
        result = extract_page_units([document(original)], SimpleNamespace(extract=lambda p:
            (calls.append(p), copy.deepcopy(raw))[1]), stop_after='PAGE_EXTRACTION')
        wire = data(calls[0])
        self.assertEqual(wire['source_pages'][0]['text'], original)
        legacy, _ = model_input(wire)
        labelled = [(label, text) for page in legacy['source_pages'] for label, text in page['segments'] if label]
        self.assertEqual(labelled[0], ('S1', paragraph))
        self.assertEqual(result['working_units'][0]['derived']['source_text'], paragraph)
        entries = result['working_units'][0]['derived']['citations']
        self.assertEqual([c['quote'] for c in entries], paragraph.splitlines(keepends=True))
        self.assertEqual(result['gemini_responses'][0]['reference_map']['S1'], [c['evidence_id'] for c in entries])
        self.assertEqual(result['gemini_responses'][0]['reference_protocol'], REFERENCE_PROTOCOL)
        self.assertEqual(result['gemini_responses'][0]['gemini'], raw)
        self.assertEqual(result['review_findings'], [])
        self.assertNotIn('line_evidence_ids', calls[0])

    def test_independent_tasks_share_a_whole_paragraph_without_merging(self):
        text = 'Der Bauherr beantragt den Anschluss\nund stellt einen Verteiler bereit.\n'
        raw = response('Anschluss beantragen', 'Verteiler bereitstellen')
        result = extract_page_units([document(text)], SimpleNamespace(extract=lambda _: copy.deepcopy(raw)),
                                    stop_after='PAGE_EXTRACTION')
        first, second = result['working_units']
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(first['derived']['source_text'], text)
        self.assertEqual(first['derived']['citations'], second['derived']['citations'])
        self.assertEqual([u['gemini'] for u in result['working_units']], raw['working_units'])

    def test_table_rows_keep_wrapped_descriptions_without_swallowing_next_row(self):
        text = ('Zahlungsplan:\n\n'
                '1. Rate\t20 %\tnach Übergabe der\n'
                'fertiggestellten Planungsunterlagen.\n'
                '2. Rate\t80 %\tnach Abnahme.\n\n'
                'Eine Verrechnung ist ausgeschlossen.\n')
        payload = {'source_key': 's', 'document': {'id': 'D1'},
                   'source_pages': [{'pdf_page': 1, 'primary': True, 'text': text}]}
        wire, mapping = model_input(payload)
        spans = [(label, quote) for label, quote in wire['source_pages'][0]['segments'] if label]
        self.assertEqual(spans, [('S1', 'Zahlungsplan:\n'),
            ('S2', '1. Rate\t20 %\tnach Übergabe der\nfertiggestellten Planungsunterlagen.\n'),
            ('S3', '2. Rate\t80 %\tnach Abnahme.\n'), ('S4', 'Eine Verrechnung ist ausgeschlossen.\n')])
        self.assertEqual(len(mapping['S2']), 2)
        self.assertEqual(''.join(q for _, q in wire['source_pages'][0]['segments']), text)

    def test_crlf_blank_lines_and_indentation_are_preserved_losslessly(self):
        text = '  Erste Regel.\r\n  Ihre Fortsetzung.\r\n \t\r\nZweite Regel.\r\n'
        wire, _ = model_input({'source_key': 's', 'document': {'id': 'D1'},
                              'source_pages': [{'pdf_page': 1, 'text': text}]})
        segments = wire['source_pages'][0]['segments']
        self.assertEqual(segments, [['S1', '  Erste Regel.\r\n  Ihre Fortsetzung.\r\n'],
                                    [None, ' \t\r\n'], ['S2', 'Zweite Regel.\r\n']])
        self.assertEqual(''.join(q for _, q in segments), text)

    def test_long_paragraph_remains_one_choice_and_restores_all_original_line_spans(self):
        text = ('Lange Originalregel mit unverändertem Wortlaut. ' * 80) + '\nFortsetzung ohne Leerzeile.\n'
        result = extract_page_units([document(text)], SimpleNamespace(extract=lambda _: response('Aufgabe')),
                                    stop_after='PAGE_EXTRACTION')
        self.assertEqual(result['working_units'][0]['derived']['source_text'], text)
        self.assertEqual(set(result['gemini_responses'][0]['reference_map']), {'S1'})
        self.assertGreater(len(result['working_units'][0]['derived']['citations']), 2)

    def test_missing_layout_separators_do_not_trigger_guessed_sentence_boundaries(self):
        page = {'source_key': 's', 'document_id': 'D1', 'pdf_page': 1,
                'text': 'Erste Regel. Zweite Regel.\nUnabhängige Aussage ohne Layoutgrenze.\n'}
        blocks = passage_catalog([page])
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]['quote'], page['text'])

    def test_pipe_rows_are_separate_and_indented_prose_is_not_a_table(self):
        page = {'source_key': 's', 'document_id': 'D1', 'pdf_page': 1,
                'text': '\tEingerückter Text.\nFortsetzung.\n\n| Rate | Anteil |\n| Eins | 20 % |\n'}
        quotes = [b['quote'] for b in passage_catalog([page])]
        self.assertEqual(quotes, ['\tEingerückter Text.\nFortsetzung.\n', '| Rate | Anteil |\n', '| Eins | 20 % |\n'])

    def test_page_scope_and_identical_text_keep_distinct_original_evidence(self):
        pages = [{'source_key': 's', 'document_id': 'D1', 'pdf_page': p, 'text': 'Gleicher Text.\n'} for p in (1, 2)]
        blocks = passage_catalog(pages)
        self.assertNotEqual(blocks[0]['line_evidence_ids'], blocks[1]['line_evidence_ids'])
        self.assertEqual([b['pdf_page'] for b in blocks], [1, 2])

    def test_unknown_line_inside_a_paragraph_map_does_not_partially_bind_the_paragraph(self):
        pages = [{'pdf_page': 1, 'text': 'Erste Zeile.\nZweite Zeile.\n'}]
        entries = catalog([{**p, 'source_key': 's', 'document_id': 'D1'} for p in pages])
        raw = {'working_units': [{'citations': [{'evidence_id': 'S1'}]}]}
        for selected in ([], [entries[0]['evidence_id'], 'Eunknown']):
            with self.subTest(selected=selected):
                derived, audit, issues = resolve(raw, pages, [], 's', 'D1', reference_map={'S1': selected})
                self.assertIn('unknown_evidence_id', {i['code'] for i in issues})
                self.assertEqual(derived, raw)
                self.assertEqual(audit['bindings'], [])

    def test_historical_single_line_reference_maps_remain_supported(self):
        pages = [{'pdf_page': 1, 'text': 'Originaltext.'}]
        entry = catalog([{**pages[0], 'source_key': 's', 'document_id': 'D1'}])[0]
        raw = {'working_units': [{'citations': [{'evidence_id': 'S1'}]}]}
        derived, _, issues = resolve(raw, pages, [], 's', 'D1', reference_map={'S1': entry['evidence_id']})
        self.assertEqual(issues, [])
        self.assertEqual(derived['working_units'][0]['citations'][0]['quote'], 'Originaltext.')

    def test_unknown_reference_keeps_correct_audit_path_after_a_paragraph_expands(self):
        pages = [{'pdf_page': 1, 'text': 'Erste Zeile.\nZweite Zeile.\n\nDritte Zeile.\n'}]
        entries = catalog([{**pages[0], 'source_key': 's', 'document_id': 'D1'}])
        raw = {'working_units': [{'citations': [{'evidence_id': label} for label in ('S1', 'Sbad', 'S2')]}]}
        before = copy.deepcopy(raw)
        mapping = {'S1': [e['evidence_id'] for e in entries[:2]], 'S2': [entries[2]['evidence_id']]}
        derived, audit, issues = resolve(raw, pages, [], 's', 'D1', reference_map=mapping)
        self.assertEqual(raw, before)
        self.assertEqual(derived['working_units'][0]['citations'][2], {'evidence_id': 'Sbad'})
        self.assertEqual({i['path'] for i in issues}, {'$.working_units[0].citations[2]'})
        self.assertEqual([b['path'] for b in audit['bindings']],
                         ['$.working_units[0].citations[0]', '$.working_units[0].citations[1]',
                          '$.working_units[0].citations[3]'])
