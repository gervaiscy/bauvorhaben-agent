"""Mechanical offer boundaries; no semantic filtering or deletion of proposals."""
import copy

from ...core import digest
from .payload import PROTOCOL, LEGACY_PROTOCOL


def valid_citation(citation):
    return (isinstance(citation, dict) and set(citation) == {'passage'}
            and isinstance(citation['passage'], str) and bool(citation['passage'].strip()))


def legacy_citation(citation):
    return (isinstance(citation, dict) and set(citation) == {'pdf_page', 'quote'}
            and type(citation['pdf_page']) is int and citation['pdf_page'] > 0
            and isinstance(citation['quote'], str) and bool(citation['quote'].strip()))


def valid_proposal(row, protocol=PROTOCOL):
    return (isinstance(row, dict) and set(row) == {'name', 'provider', 'description', 'terms', 'citations'}
            and all(isinstance(row[k], str) and row[k].strip() for k in ('name', 'description'))
            and (row['provider'] is None or isinstance(row['provider'], str) and bool(row['provider'].strip()))
            and isinstance(row['terms'], list) and all(isinstance(t, str) for t in row['terms'])
            and isinstance(row['citations'], list) and bool(row['citations'])
            and all((legacy_citation if protocol == LEGACY_PROTOCOL else valid_citation)(c) for c in row['citations']))


def primary_citations(record):
    metadata = record['derived']
    source_key = metadata['source_key'] or metadata.get('source_document') or metadata['document_id']
    return [copy.deepcopy(c) for c in metadata.get('citations', record['gemini'].get('citations', []))
            if isinstance(c, dict) and isinstance(c.get('quote'), str) and c['quote'].strip()
            and (c.get('source_key', source_key), c.get('document_id', metadata['document_id'])) ==
                (source_key, metadata['document_id'])
            and c.get('pdf_page') in metadata['primary_pages']]


def duplicate_key(record):
    row = record['gemini']
    normalize = lambda text: ' '.join(text.split())
    return digest({'source': (record['derived']['source_key'], record['derived']['document_id']),
        'provider': normalize(row['provider']) if row['provider'] is not None else None,
        'name': normalize(row['name']), 'description': normalize(row['description']),
        'terms': [normalize(t) for t in row['terms']],
        'evidence': sorted(c.get('evidence_id') or digest(c) for c in primary_citations(record))})


def validate(ctx, protocol=PROTOCOL):
    """Invalid rows remain in offers and raw JSON; active findings block handoff."""
    seen = {}
    for response in ctx.responses:
        if response['pass'] != 'PAGE_OFFERS' or response.get('status') == 'request_failed':
            continue
        raw = response.get('gemini')
        if not isinstance(raw, dict) or set(raw) != {'offers'} or not isinstance(raw.get('offers'), list):
            ctx.findings.append({'response_id': response['id'], 'code': 'offer_validation_failed',
                                 'reasons': ['invalid_offer_response']})
    for record in ctx.offers:
        metadata, row = record['derived'], record['gemini']
        reasons = []
        if not valid_proposal(row, protocol):
            reasons.append('invalid_offer_shape')
        origin = primary_citations(record) if isinstance(row, dict) and isinstance(row.get('citations'), list) else []
        if not origin:
            reasons.append('no_primary_offer_evidence')
        if any(f.get('response_id') == metadata['response_id'] and
               (f.get('path', '').startswith(f"$.offers[{metadata['response_position']}]")
                or f.get('row') == metadata['response_position']) for f in ctx.findings):
            reasons.append('unresolved_offer_evidence')
        if not reasons:
            key = duplicate_key(record)
            if key in seen:
                reasons.append('duplicate_offer')
                metadata['duplicate_of'] = seen[key]
            else:
                seen[key] = record['id']
        metadata.update(offer_protocol=protocol, origin_citations=origin,
                        offer_validation='invalid' if reasons else 'valid')
        if reasons:
            ctx.findings.append({'response_id': metadata['response_id'],
                'row': metadata['response_position'], 'offer_id': record['id'],
                'code': 'offer_validation_failed', 'reasons': reasons})
