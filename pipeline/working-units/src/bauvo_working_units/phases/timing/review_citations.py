"""Compact literal evidence for display without deleting selections from audit data."""
import copy

from ...core import digest


def compact_citations(citations, pages):
    result, seen = [], set()
    for value in citations:
        citation = copy.deepcopy(value)
        if not isinstance(citation, dict) or not all(k in citation for k in (
                'source_key', 'document_id', 'pdf_page', 'page_sha256', 'start', 'end', 'quote')):
            result.append(citation)
            continue
        identity = tuple(citation[k] for k in ('source_key', 'document_id', 'pdf_page', 'page_sha256'))
        key = (*identity, citation['start'], citation['end'], citation['quote'])
        if key in seen:
            continue  # Same exact source span, still present in the full audit.
        seen.add(key)
        previous = result[-1] if result else None
        if isinstance(previous, dict) and all(previous.get(k) == citation[k] for k in (
                'source_key', 'document_id', 'pdf_page', 'page_sha256')):
            start, end = previous.get('end'), citation['start']
            gap = '' if start == end else None
            page = pages.get(identity[:3])
            if (isinstance(start, int) and start < end and isinstance(page, str)
                    and digest(page) == citation['page_sha256']
                    and page[previous['start']:start] == previous['quote']
                    and page[end:citation['end']] == citation['quote']
                    and not page[start:end].strip()):
                gap = page[start:end]
            if gap is not None:
                previous['quote'] += gap + citation['quote']
                previous['end'] = citation['end']
                continue
        result.append(citation)
    return result
