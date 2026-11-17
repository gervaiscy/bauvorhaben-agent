"""Short window-local passage labels; all quotations come from original source slices."""
import copy
import re

from ...citation_evidence import evidence_span
from ...core import digest
from .context import paragraphs
from .payload import REFERENCE_PROTOCOL


def page_spans(text):
    """Blank-line paragraph boundaries, preserving every original character once."""
    if not text:
        return []
    starts = [0] + [start for start, _ in list(paragraphs(text))[1:]]
    return list(zip(starts, starts[1:] + [len(text)]))


def selectable_pages(pages):
    return [{'pdf_page': p['pdf_page'], 'passages': [
                {'passage': f"P{p['pdf_page']}-{n}", 'text': p['text'][start:end]}
                for n, (start, end) in enumerate(page_spans(p['text']), 1)]}
            for p in pages if p.get('primary', True)]


def reference_map(pages):
    result = {}
    for page in pages:
        if not page.get('primary', True):
            continue
        for n, (start, end) in enumerate(page_spans(page['text']), 1):
            label = f"P{page['pdf_page']}-{n}"
            if label in result:
                raise ValueError('Ambiguous primary page identity in Offers window')
            result[label] = evidence_span(page, start, end)
    return result


def resolve_response(response, pages, saved_map=None):
    mapping = reference_map(pages)
    if saved_map is not None and saved_map != mapping:
        raise ValueError('Saved Offers passage references differ from the original source window')
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
                valid = (isinstance(citation, dict) and set(citation) == {'passage'}
                         and isinstance(citation['passage'], str) and bool(citation['passage'].strip()))
                label = citation['passage'] if valid else None
                original_label = label
                if (valid and label not in mapping and re.fullmatch(r'[1-9][0-9]*-[1-9][0-9]*', label)
                        and 'P' + label in mapping):
                    label = 'P' + label
                span = mapping.get(label) if valid else None
                if span is None:
                    findings.append({'path': path, 'code': 'unknown_passage_id' if valid else 'invalid_citation',
                                     **({'passage': citation['passage']} if valid else {})})
                    resolved.append(copy.deepcopy(citation))
                else:
                    resolved.append(copy.deepcopy(span))
                    normalized = label != original_label
                    bindings.append({'path': path,
                        'method': 'missing_prefix_normalization' if normalized else 'original_passage_selection',
                        'passage': label, **({'original_passage': original_label} if normalized else {}), **span})
            row['citations'] = resolved
    audit = {'reference_protocol': REFERENCE_PROTOCOL, 'source_pages_sha256': digest(pages),
             'bindings': bindings, 'original_findings': copy.deepcopy(findings), 'findings': copy.deepcopy(findings)}
    return result, audit, findings
