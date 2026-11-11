"""Lossless, compact full-contract model input; provenance stays in local catalogs."""
from ...citation_evidence import catalog


REFERENCE_PROTOCOL = 'source-labels-v1'


def reference_map(entries):
    """Package-local labels in source order; fingerprints remain application-owned."""
    return {f'S{number}': entry['evidence_id'] for number, entry in enumerate(entries, 1)}


def compact_pages(pages, entries, *, short_labels=False):
    labels = {identifier: label for label, identifier in reference_map(entries).items()} if short_labels else {}
    grouped = {}
    for entry in entries:
        key = tuple(entry[k] for k in ('source_key', 'document_id', 'pdf_page'))
        grouped.setdefault(key, []).append(entry)
    result = []
    for page in pages:
        key = tuple(page[k] for k in ('source_key', 'document_id', 'pdf_page'))
        segments, cursor = [], 0
        for entry in grouped.get(key, []):
            start, end = entry['start'], entry['end']
            if start < cursor or page['text'][start:end] != entry['quote']:
                raise ValueError('Compact context differs from original evidence')
            if start > cursor:
                segments.append([None, page['text'][cursor:start]])
            segments.append([labels.get(entry['evidence_id'], entry['evidence_id']), page['text'][start:end]])
            cursor = end
        if cursor < len(page['text']):
            segments.append([None, page['text'][cursor:]])
        result.append({**{k: page[k] for k in ('source_key', 'document_id', 'pdf_page')}, 'segments': segments})
    return result


def expand_payload(payload):
    """Reconstruct and verify every compact ID against original page text, offline."""
    if 'evidence_catalog' in payload:
        return payload
    pages = []
    selected = []
    for page in payload['source_pages']:
        segments = page['segments']
        pages.append({**{k: page[k] for k in ('source_key', 'document_id', 'pdf_page')},
                      'text': ''.join(segment[1] for segment in segments)})
        selected.extend((identifier, text) for identifier, text in segments if identifier is not None)
    entries = catalog(pages)
    labeled = payload.get('evidence_protocol') in ('source-backed-interpretations-v8', 'source-backed-interpretations-v9', 'contractual-rights-v1', 'contractual-rights-v2', 'contractual-rights-v3')
    mapping = reference_map(entries) if labeled else None
    expected = ([(label, entry['quote']) for label, entry in zip(mapping, entries)] if labeled
                else [(entry['evidence_id'], entry['quote']) for entry in entries])
    if selected != expected:
        raise ValueError('Compact context IDs do not match original sources')
    if labeled and payload.get('reference_protocol') != REFERENCE_PROTOCOL:
        raise ValueError('Unknown project-fact reference protocol')
    targets = payload.get('target_evidence_ids')
    if labeled and (not isinstance(targets, list) or any(not isinstance(i, str) or i not in mapping for i in targets)):
        raise ValueError('Project-fact targets must be exact source labels')
    ownership = {}
    if payload.get('evidence_protocol') == 'source-backed-interpretations-v9':
        from .provisions import provisions, model_provisions, PROVISION_PROTOCOL
        units = provisions(pages, entries)
        if (payload.get('provisions') != model_provisions(units, mapping)
                or payload.get('provision_protocol') != PROVISION_PROTOCOL):
            raise ValueError('Project-fact provisions do not match original sources')
        assigned = payload.get('target_provision_ids')
        lookup = {u['id']: u for u in units}
        if (not isinstance(assigned, list) or any(not isinstance(i, str) or i not in lookup for i in assigned)
                or len(set(assigned)) != len(assigned)
                or [mapping[i] for i in targets] != [i for p in assigned for i in lookup[p]['evidence_ids']]):
            raise ValueError('Project-fact ownership must match the assigned source provisions')
        ownership = {'provisions': units}
    return {**payload, **ownership, 'source_pages': pages, 'evidence_catalog': entries,
            **({'reference_map': mapping, 'target_evidence_ids': [mapping[i] for i in targets]} if labeled else {})}
