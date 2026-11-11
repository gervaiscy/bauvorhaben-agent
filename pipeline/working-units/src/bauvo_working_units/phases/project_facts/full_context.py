"""One complete contract context, with source-qualified evidence and dispositions."""
import copy

from ...citation_evidence import catalog
from ...core import digest
from .index import index_response


CONTEXT_SCOPE = 'full_contract_package'
EVIDENCE_PROTOCOL = 'source-backed-interpretations-v2'
LEGACY_BATCHED_EVIDENCE_PROTOCOL = 'source-backed-interpretations-v3'
STRICT_CLAIMS_EVIDENCE_PROTOCOL = 'source-backed-interpretations-v4'
CONTEXT_CLAIMS_EVIDENCE_PROTOCOL = 'source-backed-interpretations-v5'
UNION_EVIDENCE_PROTOCOL = 'source-backed-interpretations-v6'
AUTHORITATIVE_CLAIMS_EVIDENCE_PROTOCOL = 'source-backed-interpretations-v7'
LABELED_EVIDENCE_PROTOCOL = 'source-backed-interpretations-v8'
BATCHED_EVIDENCE_PROTOCOL = 'source-backed-interpretations-v9'
TARGET_BATCH_SIZE = 50


def source_windows(sources):
    return [{'source_key': source['source_key'], 'document_id': source['document_id'],
             'document_title': source['title'],
             'pages': [p['pdf_page'] for p in source['pages']],
             'primary_pages': [p['pdf_page'] for p in source['pages']]}
            for source in sources]


def context(sources):
    """Retain all supplied pages; task-window settings do not bound this request."""
    pages = []
    for source in sources:
        seen = set()
        for page in source['pages']:
            number = page.get('pdf_page')
            if (type(number) is not int or number < 1 or number in seen
                    or not isinstance(page.get('text'), str)):
                raise ValueError('Invalid full-contract source page')
            seen.add(number)
            pages.append({**page, 'source_key': source['source_key'],
                          'document_id': source['document_id']})
    windows = source_windows(sources)
    metadata = {'context_scope': CONTEXT_SCOPE, 'sources': windows}
    response_id = 'contract-' + digest(sources)[:16] + ':PF0001'
    return pages, catalog(pages), metadata, response_id


def target_batches(sources, *, owned=False):
    """Partition the output assignment, never the source context."""
    pages, entries, metadata, rid = context(sources)
    if owned:
        from .provisions import batches
        return batches(pages, entries, metadata, rid, TARGET_BATCH_SIZE)
    prefix = rid.rsplit(':', 1)[0]
    return [({**metadata, 'target_evidence_ids': [e['evidence_id'] for e in entries[start:start + TARGET_BATCH_SIZE]]},
             f'{prefix}:PF{number:04d}')
            for number, start in enumerate(range(0, len(entries), TARGET_BATCH_SIZE), 1)]


def records(response, sources, *, target_evidence_ids=None, response_id=None, require_claims=False,
            allow_context_citations=False, derive_fact_citations=False, authoritative_claims=False,
            reference_map=None, provisions=None, target_provision_ids=None):
    """Recompute evidence and scope without inventing a single owner for cross-document facts."""
    pages, entries, metadata, default_id = context(sources)
    if reference_map is not None:
        from .payload import reference_map as source_reference_map
        if reference_map != source_reference_map(entries):
            raise ValueError('Project-fact reference mapping does not match original sources')
    if provisions is not None:
        from .provisions import provisions as source_provisions
        if provisions != source_provisions(pages, entries):
            raise ValueError('Project-fact provisions do not match original sources')
        metadata['target_provision_ids'] = target_provision_ids
    response_id = response_id or default_id
    if target_evidence_ids is not None:
        metadata = {**metadata, 'target_evidence_ids': target_evidence_ids}
    values, omitted, coverage, issues = index_response(response, entries, pages,
        target_evidence_ids=target_evidence_ids, require_claims=require_claims,
        allow_context_citations=allow_context_citations, derive_fact_citations=derive_fact_citations,
        authoritative_claims=authoritative_claims, reference_map=reference_map,
        provisions=provisions, target_provision_ids=target_provision_ids)
    facts, exclusions = [], []

    def provenance(citations):
        keys = {(c['source_key'], c['document_id']) for c in citations}
        scopes = [scope for scope in metadata['sources']
                  if (scope['source_key'], scope['document_id']) in keys]
        own = scopes[0] if len(scopes) == 1 else {}
        numbers = list(dict.fromkeys(c['pdf_page'] for c in citations))
        return {'context_scope': CONTEXT_SCOPE, 'sources': copy.deepcopy(scopes),
                **({'target_evidence_ids': list(target_evidence_ids)} if target_evidence_ids is not None else {}),
                **({'target_provision_ids': list(target_provision_ids)} if target_provision_ids is not None else {}),
                'source_key': own.get('source_key'), 'document_id': own.get('document_id'),
                'document_title': own.get('document_title'), 'pages': numbers,
                'primary_pages': numbers, 'response_id': response_id}

    rows = response.get('verpflichtungen', []) if isinstance(response, dict) else []
    for position, value in enumerate(values):
        path = f'$.verpflichtungen[{position}]'
        own_issues = [issue for issue in issues if issue['path'] == path or issue['path'].startswith(path+'.')]
        facts.append({'id': f'{response_id}:fact:{position:03d}',
                      'gemini': copy.deepcopy(rows[position]), 'review_findings': own_issues,
                      'derived': {**provenance(value['verified_citations']),
                                  'response_position': position, **value}})
    rows = response.get('nicht_als_fakt_erfasst', []) if isinstance(response, dict) else []
    for position, value in enumerate(omitted):
        citations = [value['citation']] if value['citation'] is not None else []
        exclusions.append({'id': f'{response_id}:excluded:{position:03d}',
                           'gemini': copy.deepcopy(rows[position]),
                           'derived': {**provenance(citations), 'response_position': position, **value}})
    findings = [{'pass': 'PAGE_PROJECT_FACTS', 'response_id': response_id, **metadata, **issue}
                for issue in issues]
    return facts, exclusions, coverage, findings
