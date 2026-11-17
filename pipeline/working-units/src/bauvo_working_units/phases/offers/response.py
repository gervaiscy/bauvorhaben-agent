"""Bind offer quotations to original primary pages locally; never rewrite provider JSON."""
import copy
import re

from ...citation_evidence import evidence_span
from ...core import digest
REFERENCE_PROTOCOL = 'offer-original-quote-v1'  # Historical completed responses only.


def bind(citation, pages):
    if (not isinstance(citation, dict) or set(citation) != {'pdf_page', 'quote'}
            or type(citation['pdf_page']) is not int or citation['pdf_page'] < 1
            or not isinstance(citation['quote'], str) or not citation['quote'].strip()):
        return None, 'invalid_citation', None
    page = next((p for p in pages if p['pdf_page'] == citation['pdf_page']), None)
    if page is None:
        return None, 'citation_page_outside_window', None
    # Whitespace only: preserve all words, punctuation and case. Count overlapping
    # occurrences as well; repeated quotations must not select an arbitrary span.
    pattern = r'\s+'.join(re.escape(word) for word in citation['quote'].split())
    matches = list(re.finditer('(?=(' + pattern + '))', page['text']))
    if len(matches) != 1:
        return None, 'citation_quote_not_found', 'ambiguous' if matches else 'missing'
    match = matches[0]
    return evidence_span(page, match.start(1), match.end(1)), None, None


def resolve_response(response, pages):
    """Only supplied primary pages may support offers, even if page IDs collide elsewhere."""
    result = copy.deepcopy(response)
    findings, bindings = [], []
    rows = result.get('offers') if isinstance(result, dict) else None
    if isinstance(rows, list):
        for n, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get('citations'), list):
                continue
            resolved = []
            for k, citation in enumerate(row['citations']):
                path = f'$.offers[{n}].citations[{k}]'
                span, code, reason = bind(citation, pages)
                if code:
                    findings.append({'path': path, 'code': code, **({'reason': reason} if reason else {})})
                    resolved.append(copy.deepcopy(citation))
                else:
                    resolved.append(span)
                    method = 'unique_literal_quote' if span['quote'] == citation['quote'] else 'unique_whitespace_quote'
                    bindings.append({'path': path, 'method': method, **span})
            row['citations'] = resolved
    audit = {'quote_protocol': REFERENCE_PROTOCOL, 'source_pages_sha256': digest(pages),
             'bindings': bindings, 'original_findings': copy.deepcopy(findings), 'findings': copy.deepcopy(findings)}
    return result, audit, findings
