import copy
import unittest
from bauvo_working_units.citation_checks import citation_findings


class CitationChecksTests(unittest.TestCase):
    pages = [{'pdf_page': 3, 'text': 'Die Wände\n werden nach Abnahme verputzt.'}]

    def check(self, citations):
        response = {'working_units': [{'action': 'Verputzen', 'citations': citations}]}
        before = copy.deepcopy(response)
        findings = citation_findings(response, self.pages)
        self.assertEqual(response, before)
        return findings

    def test_whitespace_and_unicode_normalization_only(self):
        self.assertEqual(self.check([{'pdf_page': 3, 'quote': 'Wa\u0308nde werden nach Abnahme'}]), [])
        self.assertEqual(self.check([{'pdf_page': 3, 'quote': 'Wande werden'}])[0]['code'], 'citation_quote_not_found')

    def test_missing_malformed_wrong_page_and_wrong_quote(self):
        for citations, expected in [([], 'missing_citations'), (None, 'invalid_citations'),
             ([None], 'invalid_citation'), ([{'pdf_page': True, 'quote': 'Die'}], 'invalid_citation'),
             ([{'pdf_page': 4, 'quote': 'Die'}], 'citation_page_outside_window'),
             ([{'pdf_page': 3, 'quote': 'erfunden'}], 'citation_quote_not_found')]:
            with self.subTest(citations=citations):
                self.assertEqual(self.check(citations)[0]['code'], expected)

    def test_rules_and_consequences_checked_but_not_stated_allows_empty_evidence(self):
        response = {'assessments': [{'unit_id': 'U1', 'status': 'NOT_STATED', 'citations': [],
                    'timing_rules': [{'citations': [{'pdf_page': 3, 'quote': 'erfunden'}]}]}]}
        findings = citation_findings(response, self.pages)
        self.assertEqual(len(findings), 1)
        self.assertIn('timing_rules[0]', findings[0]['path'])
        self.assertEqual(citation_findings({'assessments': [{'status': 'NOT_STATED', 'citations': []}]}, self.pages), [])

    def test_missing_evidence_is_not_an_application_failure(self):
        response = {'assessments': [{'unit_id': 'U1', 'status': 'EXPLICIT', 'consequences': ['Delay'], 'citations': []}]}
        self.assertEqual(citation_findings(response, self.pages)[0]['code'], 'missing_citations')

    def test_character_findings_cover_explanations_actions_and_quotes_without_substitution(self):
        response={'working_units':[{'action':'W\u0004nde verputzen',
                    'citations':[{'pdf_page':3,'quote':'Die W\u0004nde'}]}],
                  'assessments':[{'reason':'Fertigstellung unklar\ufffd','missing_evidence':['Ma\u0085e']} ]}
        before=copy.deepcopy(response)
        findings=citation_findings(response,self.pages)
        chars=[f for f in findings if f['code']=='suspicious_text_character']
        self.assertEqual({f['path'] for f in chars},{'$.working_units[0].action',
            '$.working_units[0].citations[0].quote','$.assessments[0].reason','$.assessments[0].missing_evidence[0]'})
        self.assertEqual(chars[0]['codepoints'],['U+0004'])
        self.assertIn('citation_quote_not_found',[f['code'] for f in findings])
        self.assertEqual(response,before)

    def test_legitimate_unicode_layout_and_unknown_spelling_are_not_guessed(self):
        from bauvo_working_units.citation_checks import text_findings
        response={'reason':'Wände\nGröße\r\nKosten\t€ 100 — \'Angebot\'\u00a0²',
                  'names':['東京','العربية','Wa\u0308nde'],
                  'unrecognized_encoding_error':'W$nde / W%20nde'}
        self.assertEqual(text_findings(response),[])
