"""Load a saved facts-stage artifact against its original source documents."""
import copy
import json
from pathlib import Path

from ...core import digest
from ...project_facts import identity, fact_citations
from ...citation_checks import citation_findings


def validate_facts(collection, documents):
    """Preserve findings and raw values; reject unusable or mismatched handoffs."""
    if not isinstance(collection, dict) or collection.get('schema_version') != 2:
        raise ValueError('Saved facts require collection schema 2; use working-units.project-facts.json')
    for key in ('facts', 'source_snapshots', 'excluded_sources', 'windows', 'gemini_responses', 'review_findings'):
        if not isinstance(collection.get(key), list):
            raise ValueError('Saved facts have missing or invalid ' + key)
    if collection.get('status') not in ('recorded', 'needs_review', 'request_failed', 'no_project_sources'):
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
    ids = set()
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
        for citation in verified:
            if (not isinstance(citation, dict) or citation.get('source_key') != key[0]
                    or citation.get('document_id') != key[1]
                    or not isinstance(raw_quotes, list)
                    or {'pdf_page': citation.get('pdf_page'), 'quote': citation.get('quote')} not in raw_quotes
                    or citation_findings({'citations': [citation]}, snapshots[key].pages,
                                         source_key=key[0], document_id=key[1])):
                raise ValueError('Saved verified citation is not supported by its original source and proposal')
    return copy.deepcopy(collection)


def load_facts(path, documents):
    return validate_facts(json.loads(Path(path).read_text(encoding='utf-8')), documents)
