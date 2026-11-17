"""Narrow OCR tolerance for timing quotations; canonical text always comes from source."""
import re

from ...citation_evidence import evidence_span
from ..offers.response import bind

POLICY = 'timing-quote-ocr-Il-v1'


def bind_quote(citation, pages):
    span, code, reason = bind(citation, pages)
    if code != 'citation_quote_not_found' or reason != 'missing':
        return span, code, reason, {}
    quote = citation['quote']
    # Only I/l inside words. Never substitute digits, standalone letters/Roman
    # numerals, punctuation, ordinary spelling, or more than one character.
    words = []
    for word in quote.split():
        letters = ''.join(c for c in word if c.isalpha())
        eligible = len(letters) >= 2 and any(c not in 'Il' for c in letters)
        words.append(''.join('[Il]' if eligible and c in 'Il' else re.escape(c) for c in word))
    pattern = r'\s+'.join(words)
    page = next(p for p in pages if p['pdf_page'] == citation['pdf_page'])
    matches = []
    expected = ' '.join(quote.split())
    for match in re.finditer('(?=(' + pattern + '))', page['text']):
        actual = ' '.join(match[1].split())
        changes = [(a, b) for a, b in zip(expected, actual) if a != b]
        if len(actual) == len(expected) and len(changes) == 1 and set(changes[0]) == {'I', 'l'}:
            matches.append(match)
    if len(matches) != 1:
        return None, code, 'ambiguous' if matches else reason, {}
    match = matches[0]
    return evidence_span(page, match.start(1), match.end(1)), None, None, {
        'quote_binding_policy': POLICY, 'ocr_confusable_substitutions': 1}
