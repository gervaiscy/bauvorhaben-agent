"""Bind task quotations locally and restore original text without changing raw answers."""
import copy

from ...citation_evidence import evidence_span
from ...core import digest
from .quote_matching import locate, PROTOCOL as MATCHING_PROTOCOL

PROTOCOL = 'task-original-quote-v1'


def is_quotation(item):
    return isinstance(item, dict) and 'quote' in item


def valid_quotation(item):
    return (is_quotation(item) and set(item) == {'title', 'actor', 'quote'}
            and all(isinstance(item[k], str) and item[k].strip() for k in ('title', 'quote'))
            and (item['actor'] is None or isinstance(item['actor'], str) and bool(item['actor'].strip())))


def bind(quote, pages):
    citations, issue, _ = _bind(quote, pages)
    return citations, issue


def _bind(quote, pages):
    """Restore a unique bounded match; retain exact offsets across page boundaries."""
    if not isinstance(quote, str) or not quote.strip():
        return [], 'invalid_task_quote', None
    text = '\n'.join(page['text'] for page in pages)
    match, issue = locate(quote, text)
    if issue:
        return [], issue, None
    start, end = match['start'], match['end']
    offset, citations = 0, []
    for page in pages:
        a, b = max(start, offset), min(end, offset + len(page['text']))
        if a < b:
            citations.append(evidence_span(page, a - offset, b - offset))
        offset += len(page['text']) + 1
    return citations, None, {**match, 'source_text': text[start:end]}


def resolve_quotations(response, pages):
    """Keep invalid proposals and raw JSON visible; derive source locations only."""
    resolved = copy.deepcopy(response)
    issues, bindings = [], []
    rows = resolved.get('working_units') if isinstance(resolved, dict) else None
    if not isinstance(resolved, dict) or set(resolved) != {'working_units'} or not isinstance(rows, list):
        issues.append({'path': '$', 'code': 'invalid_task_response'})
    if isinstance(rows, list):
        for n, row in enumerate(rows):
            if not is_quotation(row):
                continue
            path = f'$.working_units[{n}].quote'
            citations, issue, match = _bind(row.get('quote'), pages)
            row['citations'] = citations
            if issue:
                issues.append({'path': path, 'code': issue})
            else:
                row['source_text'] = match['source_text']
                details = {k: v for k, v in match.items() if k not in ('start', 'end', 'source_text')}
                bindings.extend({'path': path, **details, **c} for c in citations)
    return resolved, {'quote_protocol': PROTOCOL, 'matching_protocol': MATCHING_PROTOCOL,
                      'source_pages_sha256': digest(pages),
                      'bindings': bindings, 'original_findings': copy.deepcopy(issues),
                      'findings': copy.deepcopy(issues)}, issues


def derived_data(item, resolved):
    result = {'task_protocol': PROTOCOL, 'title': item.get('title'), 'actor': item.get('actor')}
    citations = resolved.get('citations') if isinstance(resolved, dict) else None
    if valid_quotation(item) and isinstance(citations, list) and citations:
        result.update(citations=copy.deepcopy(citations), source_text=resolved['source_text'])
    return result
