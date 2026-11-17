"""OCR tolerance restores original evidence; it never rewrites provider assertions."""
import copy
import unittest
from types import SimpleNamespace

from bauvo_working_units.phases.timing.quote_binding import bind_quote, POLICY
from bauvo_working_units.phases.timing.response import resolve_response
from bauvo_working_units.phases.offers.response import bind


class TimingQuoteBindingTests(unittest.TestCase):
    def pages(self, text):
        return [{'source_key': 'source', 'document_id': 'D1', 'pdf_page': 2, 'text': text}]

    def test_one_confusable_inside_word_binds_to_original_with_explicit_audit(self):
        pages = self.pages('Es gilt It. Vertrag eine Frist von 14 Tagen.')
        raw = {'assessments': [{'unit_id': 'U1', 'answer': '14 Tage.', 'citations': [
            {'pdf_page': 2, 'quote': 'Es gilt lt. Vertrag eine Frist von 14 Tagen.'}]}]}
        before = copy.deepcopy(raw)
        resolved, audit, issues = resolve_response(raw, pages, {})
        self.assertEqual(issues, [])
        citation = resolved['assessments'][0]['citations'][0]
        self.assertEqual(citation['quote'], pages[0]['text'])
        self.assertEqual(citation['start'], 0)
        self.assertEqual(citation['end'], len(pages[0]['text']))
        self.assertEqual(audit['bindings'][0]['quote_binding_policy'], POLICY)
        self.assertEqual(audit['bindings'][0]['ocr_confusable_substitutions'], 1)
        self.assertEqual(raw, before)
        # The settled Offers boundary is deliberately unaffected.
        self.assertEqual(bind(raw['assessments'][0]['citations'][0], pages)[1], 'citation_quote_not_found')

    def test_ocr_tolerance_and_whitespace_tolerance_can_combine(self):
        span, code, _, _ = bind_quote({'pdf_page': 2, 'quote': 'Es gilt lt. Vertrag.'},
                                      self.pages('Es gilt\nIt. Vertrag.'))
        self.assertIsNone(code)
        self.assertEqual(span['quote'], 'Es gilt\nIt. Vertrag.')

    def test_numbers_words_punctuation_diacritics_and_multiple_changes_stay_blocking(self):
        source = 'Es gilt It. Vertrag eine Frist von 14 Tagen.'
        for quote in ('Es gilt lt. Vertrag eine Frist von 15 Tagen.',
                      'Es gilt lt. Vertrag eine Frist von l4 Tagen.',
                      'Es gilt lt. Vertrag eine Dauer von 14 Tagen.',
                      'Es gilt lt. Vertrag eine Frist von 14 Tagen!',
                      'Es giIt lt. Vertrag eine Frist von 14 Tagen.'):
            with self.subTest(quote=quote):
                span, code, reason, _ = bind_quote({'pdf_page': 2, 'quote': quote}, self.pages(source))
                self.assertIsNone(span)
                self.assertEqual((code, reason), ('citation_quote_not_found', 'missing'))
        self.assertIsNotNone(bind_quote({'pdf_page': 2, 'quote': 'Für den Bau.'},
                                        self.pages('Fur den Bau.'))[1])

    def test_standalone_letters_and_roman_numerals_are_never_substituted(self):
        for original, quote in [('Abschnitt I.', 'Abschnitt l.'), ('Abschnitt II.', 'Abschnitt Il.')]:
            with self.subTest(original=original):
                self.assertIsNotNone(bind_quote({'pdf_page': 2, 'quote': quote}, self.pages(original))[1])

    def test_unique_exact_match_wins_but_ambiguous_fallback_never_selects_a_span(self):
        span, code, _, details = bind_quote({'pdf_page': 2, 'quote': 'lt. Vertrag.'},
                                           self.pages('lt. Vertrag. It. Vertrag.'))
        self.assertIsNone(code)
        self.assertEqual(span['start'], 0)
        self.assertEqual(details, {})
        for source, quote in [('It. Vertrag. It. Vertrag.', 'lt. Vertrag.'),
                              ('llt. Vertrag. IIt. Vertrag.', 'Ilt. Vertrag.')]:
            with self.subTest(source=source):
                self.assertEqual(bind_quote({'pdf_page': 2, 'quote': quote}, self.pages(source))[1:3],
                                 ('citation_quote_not_found', 'ambiguous'))

    def test_invalid_shape_and_wrong_page_do_not_gain_fuzzy_access_to_other_sources(self):
        for citation, expected in [({'pdf_page': 1, 'quote': 'lt. Vertrag.'}, 'citation_page_outside_window'),
                                   ({'pdf_page': 2, 'quote': None}, 'invalid_citation')]:
            self.assertEqual(bind_quote(citation, self.pages('It. Vertrag.'))[1], expected)

    def test_saved_timing_rebinds_offline_without_losing_raw_answers_or_old_findings(self):
        from clause_fixtures import decode_prompt
        from bauvo_working_units.citation_repair import repair_saved_result
        from bauvo_working_units.page_flow import extract_page_units
        from bauvo_working_units.project_flow import Document
        text = 'Der Kunde zahlt It. Vertrag binnen 14 Tagen.'
        document = Document('D1', 'Fiktiver Vertrag', 'synthetic.pdf', schema_version=6,
                            source_key='source', pages=({'pdf_page': 1, 'text': text},))
        def provider(prompt):
            if prompt.startswith('PAGE_EXTRACTION.'):
                return {'working_units': [{'title': 'Zahlen', 'actor': 'Kunde', 'quote': text}]}
            if prompt.startswith('PAGE_OFFERS.'):
                return {'offers': []}
            data = decode_prompt(prompt)
            return {'assessments': [{'unit_id': data['working_units'][0]['id'], 'answer': 'Binnen 14 Tagen.',
                'citations': [{'pdf_page': 1, 'quote': text.replace('It.', 'lt.')}]}]}
        saved = extract_page_units([document], SimpleNamespace(extract=provider), stop_after='PAGE_TIMING')
        response = saved['gemini_responses'][-1]
        previous = {'response_id': response['id'], 'path': '$.assessments[0].citations[0]',
                    'code': 'citation_quote_not_found', 'reason': 'missing'}
        saved['review_findings'].append(previous)
        response['citation_evidence']['findings'] = [copy.deepcopy(previous)]
        before = copy.deepcopy(saved)
        result = repair_saved_result(saved, [document])
        self.assertEqual(result['review_findings'], [])
        self.assertEqual([r['gemini'] for r in result['gemini_responses']],
                         [r['gemini'] for r in before['gemini_responses']])
        self.assertEqual(result['citation_repair']['original_review_findings'], [previous])
        self.assertEqual(result['gemini_responses'][-1]['citation_evidence_history'][-1],
                         before['gemini_responses'][-1]['citation_evidence'])
        self.assertEqual(result['working_units'][0]['reviews'][0]['derived']['resolved_evidence']
                         ['citations'][0]['quote'], text)
        self.assertEqual(saved, before)
