"""Load a saved facts-stage artifact against its original source documents."""
import copy
import json
from pathlib import Path

from ...core import digest
from ...project_facts import identity, fact_citations
from ...citation_checks import citation_findings
from ...citation_evidence import catalog, canonical, CATALOG_PROTOCOL, LEGACY_CATALOG
from .index import index_response


def validate_facts(collection, documents):
    """Preserve findings and raw values; reject unusable or mismatched handoffs."""
    if not isinstance(collection, dict) or collection.get('schema_version') not in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15):
        raise ValueError('Saved facts require collection schema 2–15; use working-units.project-facts.json')
    for key in ('facts', 'source_snapshots', 'excluded_sources', 'windows', 'gemini_responses', 'review_findings'):
        if not isinstance(collection.get(key), list):
            raise ValueError('Saved facts have missing or invalid ' + key)
    if collection.get('status') not in ('recorded', 'needs_review', 'request_failed', 'no_project_sources', 'incomplete'):
        raise ValueError('Saved facts have an invalid collection status')
    sources = {}
    for doc in documents:
        key = identity(doc)
        if key in sources and sources[key] != doc:
            raise ValueError('Conflicting source documents for saved facts')
        sources[key] = doc
    snapshots = {}
    for snapshot in collection['source_snapshots']:
        if not isinstance(snapshot, dict):
            raise ValueError('Invalid saved fact source snapshot')
        key = (snapshot.get('source_key'), snapshot.get('document_id'))
        if not all(isinstance(v, str) for v in key) or key in snapshots:
            raise ValueError('Invalid or duplicate saved fact source identity')
        doc = sources.get(key)
        if doc is None or doc.schema_version != 6 or doc.source_type == 'blb':
            raise ValueError('Provide the original non-BLB pages/documents for every saved fact source')
        if snapshot.get('document_sha256') != digest(list(doc.pages)) or snapshot.get('pages_sha256') != doc.source_snapshot:
            raise ValueError('Saved fact source snapshot does not match supplied pages/documents')
        snapshots[key] = doc
    if collection['schema_version'] in (14, 15):
        from ...shared.source_documents import full_source_documents
        from .rights import validate_collection
        validate_collection(collection, full_source_documents(list(snapshots.values())))
        return copy.deepcopy(collection)
    if collection['schema_version'] in (6, 7, 8, 9, 10, 11, 12, 13):
        validate_full_context(collection, snapshots)
        return copy.deepcopy(collection)
    if collection['schema_version'] in (3, 4, 5):
        validate_clause_index(collection, snapshots)
        return copy.deepcopy(collection)
    ids = set()
    source_catalogs = {key: {entry['evidence_id']: canonical(entry) for entry in catalog([
        {**page, 'source_key': key[0], 'document_id': key[1]} for page in doc.pages],
        protocol=LEGACY_CATALOG)}
        for key, doc in snapshots.items()}
    for record in collection['facts']:
        if not isinstance(record, dict) or not isinstance(record.get('id'), str) or record['id'] in ids:
            raise ValueError('Invalid or duplicate saved fact record')
        ids.add(record['id'])
        meta = record.get('derived')
        if not isinstance(meta, dict) or not isinstance(record.get('review_findings'), list) or 'gemini' not in record:
            raise ValueError('Invalid saved fact metadata')
        key = (meta.get('source_key'), meta.get('document_id'))
        if not all(isinstance(v, str) for v in key) or key not in snapshots:
            raise ValueError('Saved fact record has no matching source snapshot')
        verified = meta.get('verified_citations')
        if not isinstance(verified, list):
            raise ValueError('Invalid saved fact evidence')
        raw_quotes = fact_citations(record['gemini'])
        source_catalog = source_catalogs[key]
        for citation in verified:
            if not isinstance(citation, dict):
                raise ValueError('Invalid saved fact evidence')
            if 'evidence_id' in citation and not isinstance(citation['evidence_id'], str):
                raise ValueError('Invalid saved fact evidence identifier')
            raw_reference = isinstance(raw_quotes, list) and {'evidence_id': citation.get('evidence_id')} in raw_quotes
            selected_reference = raw_reference and source_catalog.get(citation.get('evidence_id')) == citation
            audit_reference = False
            if isinstance(raw_quotes, list) and not selected_reference:
                # A repaired fact must retain the original failed selection and the
                # recorded repair decision; the literal source is checked below.
                response = next((r for r in collection['gemini_responses']
                                 if r.get('id') == meta.get('response_id')), {})
                audit = response.get('citation_evidence', {})
                prefix = f"$.project_facts[{meta.get('response_position')}].citations["
                bound = any(binding.get('method') == 'model_repair'
                    and binding.get('path', '').startswith(prefix)
                    and {k: binding.get(k) for k in ('evidence_id','source_key','document_id','pdf_page','quote')} == citation
                    for binding in audit.get('bindings', []))
                decisions = audit.get('repair', {}).get('gemini', {}).get('decisions', [])
                selected = any(isinstance(row, dict) and row.get('status') == 'SUPPORTED'
                    and row.get('path') == prefix[:-1] and
                    {'evidence_id': citation.get('evidence_id')} in row.get('citations', []) for row in decisions)
                audit_reference = bound and selected and source_catalog.get(citation.get('evidence_id')) == citation
            if (not isinstance(citation, dict) or citation.get('source_key') != key[0]
                    or citation.get('document_id') != key[1]
                    or not isinstance(raw_quotes, list)
                    or (not selected_reference and not audit_reference and
                        {'pdf_page': citation.get('pdf_page'), 'quote': citation.get('quote')} not in raw_quotes)
                    or citation_findings({'citations': [citation]}, snapshots[key].pages,
                                         source_key=key[0], document_id=key[1])):
                raise ValueError('Saved verified citation is not supported by its original source and proposal')
    return copy.deepcopy(collection)


def validate_clause_index(collection, sources):
    """Recompute text, exclusions and coverage from the preserved provider responses."""
    protocol = LEGACY_CATALOG if collection['schema_version'] == 3 else CATALOG_PROTOCOL
    evidence_protocol = 'source-clause-index-v1' if collection['schema_version'] == 3 else 'source-clause-index-v2'
    if collection['schema_version'] == 5:
        evidence_protocol = 'source-backed-interpretations-v1'
        from ...shared.source_documents import full_source_documents
        original = full_source_documents(list(sources.values()))
        saved = collection.get('source_documents')
        if (not isinstance(saved, list) or not all(isinstance(d, dict) for d in saved)
                or sorted(saved, key=lambda d: (d.get('source_key', ''), d.get('document_id', ''))) !=
                   sorted(original, key=lambda d: (d['source_key'], d['document_id']))):
            raise ValueError('Saved project source documents do not match original sources')
    if (collection.get('evidence_protocol') != evidence_protocol
            or collection.get('catalog_protocol', LEGACY_CATALOG) != protocol
            or not isinstance(collection.get('excluded_passages'), list)):
        raise ValueError('Invalid saved clause-index format')
    windows, covered = {}, {key: set() for key in sources}
    for window in collection['windows']:
        key = (window.get('source_key'), window.get('document_id'))
        doc = sources.get(key)
        if doc is None:
            raise ValueError('Clause-index window has no source')
        numbers = window.get('pages')
        primary = window.get('primary_pages')
        available = {p['pdf_page'] for p in doc.pages}
        if (not isinstance(numbers, list) or not isinstance(primary, list)
                or any(type(n) is not int for n in numbers + primary)
                or not set(primary) <= set(numbers) <= available
                or len(set(numbers)) != len(numbers) or len(set(primary)) != len(primary)
                or covered[key] & set(primary) or window.get('document_title') != doc.title):
            raise ValueError('Invalid clause-index source window')
        covered[key].update(primary)
        pages = [{**p, 'source_key': key[0], 'document_id': key[1]}
                 for p in doc.pages if p['pdf_page'] in numbers]
        if any(p['text'].strip() for p in pages if p['pdf_page'] in primary):
            windows[(*key, tuple(numbers))] = (window, catalog(pages, protocol=protocol), pages)
    if any(covered[key] != {p['pdf_page'] for p in doc.pages} for key, doc in sources.items()):
        raise ValueError('Clause-index windows do not cover their source documents')
    expected_facts, expected_exclusions, seen = {}, {}, set()
    incomplete = False
    for response in collection['gemini_responses']:
        key = (response.get('source_key'), response.get('document_id'), tuple(response.get('pages', [])))
        if key not in windows or key in seen:
            raise ValueError('Missing or duplicate clause-index response window')
        seen.add(key)
        metadata, entries, pages = windows[key]
        if any(response.get(k) != v for k, v in metadata.items()):
            raise ValueError('Clause-index response metadata changed')
        if response.get('status') == 'request_failed':
            incomplete = True
            continue
        raw = response.get('gemini')
        facts, exclusions, coverage, issues = index_response(raw, entries,
            pages if collection['schema_version'] in (4, 5) else (),
            interpretations=collection['schema_version'] == 5)
        if response.get('coverage') != coverage:
            raise ValueError('Clause-index passage accounting changed')
        incomplete = incomplete or bool(issues)
        for issue in issues:
            if {**metadata, **issue, 'pass': 'PAGE_PROJECT_FACTS',
                    'response_id': response['id']} not in collection['review_findings']:
                raise ValueError('Clause-index findings were discarded')
        for kind, derived, field, target in (
                ('fact', facts, 'verpflichtungen', expected_facts),
                ('excluded', exclusions, 'nicht_als_fakt_erfasst', expected_exclusions)):
            for position, value in enumerate(derived):
                identifier = f"{response['id']}:{kind}:{position:03d}"
                if identifier in target:
                    raise ValueError('Duplicate clause-index record identity')
                target[identifier] = {'gemini': raw[field][position],
                    'derived': {**metadata, 'response_id': response['id'],
                                'response_position': position, **value}}
    if seen != set(windows):
        raise ValueError('Clause-index window has no preserved response')
    for records, expected in ((collection['facts'], expected_facts),
                              (collection['excluded_passages'], expected_exclusions)):
        if len(records) != len(expected):
            raise ValueError('Clause-index records were discarded or added')
        identifiers = set()
        for record in records:
            if not isinstance(record, dict):
                raise ValueError('Invalid clause-index record')
            identifier = record.get('id')
            if not isinstance(identifier, str) or identifier in identifiers or identifier not in expected:
                raise ValueError('Invalid clause-index record identity')
            if records is collection['facts'] and not isinstance(record.get('review_findings'), list):
                raise ValueError('Invalid clause-index fact findings')
            identifiers.add(identifier)
            if any(record.get(k) != v for k, v in expected[identifier].items()):
                raise ValueError('Clause-index text, provenance or original selection changed')
    if incomplete and collection['status'] not in ('incomplete', 'request_failed'):
        raise ValueError('Incomplete clause index cannot be marked complete')


def validate_full_context(collection, sources):
    from ...shared.source_documents import full_source_documents
    from .full_context import (context, records, source_windows, target_batches, CONTEXT_SCOPE,
                               EVIDENCE_PROTOCOL, BATCHED_EVIDENCE_PROTOCOL, UNION_EVIDENCE_PROTOCOL,
                               AUTHORITATIVE_CLAIMS_EVIDENCE_PROTOCOL, LABELED_EVIDENCE_PROTOCOL,
                               LEGACY_BATCHED_EVIDENCE_PROTOCOL, STRICT_CLAIMS_EVIDENCE_PROTOCOL,
                               CONTEXT_CLAIMS_EVIDENCE_PROTOCOL, TARGET_BATCH_SIZE)
    originals = full_source_documents(list(sources.values()))
    batched = collection['schema_version'] in (7, 8, 9, 10, 11, 12, 13)
    expected_protocol = {6: EVIDENCE_PROTOCOL, 7: LEGACY_BATCHED_EVIDENCE_PROTOCOL,
                         8: STRICT_CLAIMS_EVIDENCE_PROTOCOL,
                         9: CONTEXT_CLAIMS_EVIDENCE_PROTOCOL,
                         10: UNION_EVIDENCE_PROTOCOL, 11: AUTHORITATIVE_CLAIMS_EVIDENCE_PROTOCOL,
                         12: LABELED_EVIDENCE_PROTOCOL, 13: BATCHED_EVIDENCE_PROTOCOL}[collection['schema_version']]
    if (collection.get('evidence_protocol') != expected_protocol
            or collection.get('catalog_protocol') != CATALOG_PROTOCOL
            or collection.get('context_scope') != CONTEXT_SCOPE
            or collection.get('source_documents') != originals
            or collection.get('windows') != source_windows(originals)):
        raise ValueError('Saved full-contract context does not match original sources')
    pages, entries, metadata, rid = context(originals)
    if collection['schema_version'] in (12, 13):
        from .payload import reference_map, REFERENCE_PROTOCOL
        if (collection.get('reference_protocol') != REFERENCE_PROTOCOL
                or collection.get('reference_map') != reference_map(entries)):
            raise ValueError('Saved project-fact reference mapping does not match original sources')
    units = None
    if collection['schema_version'] == 13:
        from .provisions import provisions, resolve_links, PROVISION_PROTOCOL
        units = provisions(pages, entries)
        if collection.get('provisions') != units or collection.get('provision_protocol') != PROVISION_PROTOCOL:
            raise ValueError('Saved project-fact provisions do not match original sources')
    specs = target_batches(originals, owned=units is not None) if batched else (
        [(metadata, rid)] if any(p['text'].strip() for p in pages) else [])
    if batched and collection.get('target_batch_size') != TARGET_BATCH_SIZE:
        raise ValueError('Full-contract target batch size changed')
    responses = collection['gemini_responses']
    if len(responses) != len(specs):
        raise ValueError('Full-contract facts require one preserved response per target batch')
    expected_facts, expected_exclusions = [], []
    for response, (metadata, rid) in zip(responses, specs):
        if collection['schema_version'] in (12, 13) and response.get('reference_map_sha256') != digest(collection['reference_map']):
            raise ValueError('Saved project-fact response reference mapping changed')
        if units is not None and response.get('provisions_sha256') != digest(units):
            raise ValueError('Saved project-fact response provision ownership changed')
        if (response.get('id') != rid or response.get('pass') != 'PAGE_PROJECT_FACTS'
                or any(response.get(k) != v for k, v in metadata.items())):
            raise ValueError('Full-contract response source scope changed')
        if response.get('status') == 'request_failed':
            failure = response.get('failure')
            if (not isinstance(failure, dict) or failure not in collection['review_findings']
                    or response.get('gemini') is not None
                    or collection['status'] not in ('request_failed', 'incomplete')):
                raise ValueError('Full-contract request failure was discarded')
            continue
        facts, exclusions, coverage, findings = records(response.get('gemini'), originals,
            target_evidence_ids=metadata.get('target_evidence_ids'), response_id=rid,
            require_claims=collection['schema_version'] in (8, 9, 10, 11, 12, 13),
            allow_context_citations=collection['schema_version'] in (9, 10, 11, 12, 13),
            derive_fact_citations=collection['schema_version'] in (10, 11, 12, 13),
            authoritative_claims=collection['schema_version'] in (11, 12, 13),
            reference_map=collection.get('reference_map') if collection['schema_version'] in (12, 13) else None,
            provisions=units, target_provision_ids=metadata.get('target_provision_ids'))
        expected_facts.extend(facts)
        expected_exclusions.extend(exclusions)
        if response.get('coverage') != coverage:
            raise ValueError('Full-contract passage accounting changed')
        if any(issue not in collection['review_findings'] for issue in findings):
            raise ValueError('Full-contract findings were discarded')
        if findings and collection['status'] not in ('incomplete', 'request_failed'):
            raise ValueError('Incomplete full-contract facts cannot be marked complete')
    if units is not None:
        links = resolve_links(expected_facts, responses)
        if any(issue not in collection['review_findings'] for issue in links):
            raise ValueError('Full-contract provision link findings were discarded')
        if links and collection['status'] not in ('incomplete', 'request_failed'):
            raise ValueError('Unresolved provision references cannot be marked complete')
    if (len(collection['facts']) != len(expected_facts)
            or collection.get('excluded_passages') != expected_exclusions):
        raise ValueError('Full-contract interpretation, evidence or provenance changed')
    for actual, expected in zip(collection['facts'], expected_facts):
        if (not isinstance(actual, dict)
                or any(actual.get(k) != v for k, v in expected.items() if k != 'review_findings')
                or not isinstance(actual.get('review_findings'), list)
                or any(issue not in actual['review_findings'] for issue in expected['review_findings'])):
            raise ValueError('Full-contract interpretation, evidence or provenance changed')


def load_facts(path, documents):
    return validate_facts(json.loads(Path(path).read_text(encoding='utf-8')), documents)
