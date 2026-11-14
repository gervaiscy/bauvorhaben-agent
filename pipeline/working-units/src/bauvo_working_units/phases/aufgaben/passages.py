"""Layout-based task selections; original line evidence remains authoritative."""
from ...citation_evidence import catalog, evidence_span

REFERENCE_PROTOCOL = 'source-paragraph-labels-v1'


def table_row(line):
    """Explicit columns only; indentation and prose are not semantic boundaries."""
    return (len([cell for cell in line.rstrip('\r\n').split('\t') if cell.strip()]) >= 2
            or line.lstrip().startswith('|') and line.rstrip().endswith('|')
            and len([cell for cell in line.split('|') if cell.strip()]) >= 2)


def spans(text):
    """Blank lines end paragraphs; a new table row starts a selectable row.

    Wrapped row descriptions stay with their row. Page boundaries are retained;
    a continuation on another supplied page needs an additional selection.
    """
    start, offset = None, 0
    for line in text.splitlines(keepends=True):
        if not line.strip():
            if start is not None:
                yield start, offset
                start = None
        else:
            if start is not None and table_row(line):
                yield start, offset
                start = None
            if start is None:
                start = offset
        offset += len(line)
    if start is not None:
        yield start, offset


def passage_catalog(pages):
    """Every selectable passage binds to all its exact original line spans."""
    result = []
    for page in pages:
        lines = catalog([page])
        for start, end in spans(page['text']):
            result.append({**evidence_span(page, start, end), 'line_evidence_ids': [
                line['evidence_id'] for line in lines if start <= line['start'] and line['end'] <= end]})
    return result
