"""Original paragraph context for review, separate from model-selected evidence."""
from ...citation_evidence import evidence_span

PROTOCOL = 'offer-paragraph-context-v1'


def paragraphs(text):
    """Blank lines delimit paragraphs; wrapped lines and original whitespace stay intact."""
    start, offset = None, 0
    for line in text.splitlines(keepends=True):
        if line.strip():
            if start is None:
                start = offset
        elif start is not None:
            yield start, offset
            start = None
        offset += len(line)
    if start is not None:
        yield start, offset


def source_context(page, start, end):
    span = evidence_span(page, start, end)
    return {key: span[key] for key in
            ('source_key', 'document_id', 'pdf_page', 'page_sha256', 'start', 'end')} | {
                'text': span['quote']}


def review_context(citations, document):
    """Expand only verified spans; context never becomes a selected citation.

    If the quotation starts its first paragraph, include the preceding paragraph.
    At a page boundary this may be on the immediately preceding page of this
    document, including outside the extraction window. Never cross documents.
    """
    source_key = document.source_key or document.source or document.id
    pages = {p['pdf_page']: {**p, 'source_key': source_key, 'document_id': document.id}
             for p in document.pages}
    result = []
    for position, citation in enumerate(citations):
        if not isinstance(citation, dict):
            continue
        page = pages.get(citation.get('pdf_page'))
        if page is None or type(citation.get('start')) is not int or type(citation.get('end')) is not int:
            continue
        start, end = citation['start'], citation['end']
        if not 0 <= start < end <= len(page['text']):
            continue
        # Recheck the original binding rather than attaching context to unbound
        # raw citations, stale offsets, another document or altered source text.
        if citation != evidence_span(page, start, end):
            continue
        spans = list(paragraphs(page['text']))
        covered = [i for i, (a, b) in enumerate(spans) if a < end and start < b]
        if not covered:
            continue
        first, last = covered[0], covered[-1]
        context_start, context_end = spans[first][0], spans[last][1]
        surrounding = []
        if not page['text'][context_start:start].strip():
            if first:
                context_start = spans[first - 1][0]
            else:
                previous = pages.get(page['pdf_page'] - 1)
                previous_spans = list(paragraphs(previous['text'])) if previous else []
                if previous_spans:
                    surrounding.append(source_context(previous, *previous_spans[-1]))
        surrounding.append(source_context(page, context_start, context_end))
        result.append({'citation_position': position, 'kind': 'surrounding_source_context',
                       'generated_by': 'application', 'spans': surrounding})
    return result
