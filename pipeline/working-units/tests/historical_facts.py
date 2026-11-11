"""Synthetic schema-13 fixture builder for historical artifact/replay regressions.

This is deliberately not the live extraction entry point. Historical tests retain
old ownership, status and passage-accounting semantics; current rights extraction
has separate end-to-end tests. Providers here must be offline test doubles.
"""
import copy
from bauvo_working_units.core import digest
from bauvo_working_units.trace import ModelRequestFailed
from bauvo_working_units.citation_evidence import CATALOG_PROTOCOL
from bauvo_working_units.phases.project_facts.service import identity

def collect_historical_facts(documents, model, max_chars=80000, chunk_pages=10, overlap_pages=1, language='de'):
    # Keep the shared entry-point arguments; only task passes use page windows.
    from bauvo_working_units.page_flow import _prompt
    from bauvo_working_units.shared.source_documents import full_source_documents
    from bauvo_working_units.phases.project_facts.full_context import (context, records, source_windows, target_batches,
                               BATCHED_EVIDENCE_PROTOCOL, CONTEXT_SCOPE, TARGET_BATCH_SIZE)
    documents = list(documents)
    if language not in ('de', 'en'):
        raise ValueError('Unsupported output language')
    if any(d.schema_version != 6 for d in documents):
        raise ValueError('Project facts require schema-6 pages')
    if any(d.source_type not in ('project', 'blb') for d in documents):
        raise ValueError('Source type must be project or blb')
    snapshots, excluded, seen = [], [], {}
    for doc in documents:
        key = identity(doc)
        snapshot = digest(list(doc.pages))
        if key in seen:
            if seen[key] != (snapshot, doc.source_type):
                raise ValueError('Conflicting source snapshots for the same document')
            continue
        seen[key] = (snapshot, doc.source_type)
        if doc.source_type == 'blb':
            excluded.append({'source_key': key[0], 'document_id': key[1],
                             'document_sha256': snapshot, 'reason': 'general_blb'})
        else:
            snapshots.append({'source_key': key[0], 'document_id': key[1],
                              'document_sha256': snapshot, 'pages_sha256': doc.source_snapshot})
    sources = full_source_documents([d for d in documents if d.source_type != 'blb'])
    pages, entries, _, _ = context(sources)
    from bauvo_working_units.phases.project_facts.payload import reference_map, REFERENCE_PROTOCOL
    references = reference_map(entries)
    from bauvo_working_units.phases.project_facts.provisions import provisions, resolve_links, PROVISION_PROTOCOL
    units = provisions(pages, entries)
    facts, exclusions, responses, findings = [], [], [], []
    requests = []
    for metadata, rid in target_batches(sources, owned=True):
        payload = {**metadata, 'source_pages': pages, 'evidence_catalog': entries,
                   'catalog_protocol': CATALOG_PROTOCOL, 'evidence_protocol': BATCHED_EVIDENCE_PROTOCOL,
                   'provisions': units}
        requests.append((metadata, rid, _prompt('PAGE_PROJECT_FACTS', payload, language)))
    from bauvo_working_units.call_budget import prepare_requests
    prepare_requests(model, [prompt for _, _, prompt in requests])
    for metadata, rid, prompt in requests:
        try:
            response = model.extract(prompt)
        except ModelRequestFailed as exc:
            issue = {'code': 'model_request_failed', 'pass': 'PAGE_PROJECT_FACTS',
                     'response_id': rid, **metadata, 'attempts': exc.attempts}
            findings.append(issue)
            responses.append({'id': rid, 'pass': 'PAGE_PROJECT_FACTS', **metadata,
                              'status': 'request_failed', 'gemini': None, 'failure': issue})
        else:
            batch_facts, batch_exclusions, coverage, batch_findings = records(response, sources,
                target_evidence_ids=metadata['target_evidence_ids'], response_id=rid,
                require_claims=True, allow_context_citations=True, derive_fact_citations=True, authoritative_claims=True,
                reference_map=references, provisions=units,
                target_provision_ids=metadata['target_provision_ids'])
            facts.extend(batch_facts)
            exclusions.extend(batch_exclusions)
            findings.extend(batch_findings)
            responses.append({'id': rid, 'pass': 'PAGE_PROJECT_FACTS', **metadata,
                              'gemini': copy.deepcopy(response), 'coverage': coverage})
    for response in responses:
        response['reference_map_sha256'] = digest(references)
        response['provisions_sha256'] = digest(units)
    findings.extend(resolve_links(facts, responses))
    return {'schema_version': 13, 'evidence_protocol': BATCHED_EVIDENCE_PROTOCOL,
            'reference_protocol': REFERENCE_PROTOCOL, 'reference_map': references,
            'provision_protocol': PROVISION_PROTOCOL, 'provisions': units,
            'target_batch_size': TARGET_BATCH_SIZE,
            'context_scope': CONTEXT_SCOPE, 'catalog_protocol': CATALOG_PROTOCOL,
            'status': 'request_failed' if any(f['code']=='model_request_failed' for f in findings)
            else 'incomplete' if findings else 'no_project_sources' if not snapshots else 'recorded',
            'facts': facts, 'excluded_passages': exclusions, 'source_snapshots': snapshots,
            'source_documents': sources, 'excluded_sources': excluded,
            'windows': source_windows(sources), 'gemini_responses': responses,
            'review_findings': findings}

