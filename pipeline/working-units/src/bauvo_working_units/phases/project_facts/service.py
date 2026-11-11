"""Collect source-backed interpretations with exact evidence and original context."""
import copy
from ...core import digest
from .index import source_text


def fact_citations(fact):
    """Adapt field names for checks; keep provider statements and quotes untouched."""
    quotes = fact.get('zitate') if isinstance(fact, dict) else None
    if not isinstance(quotes, list):
        return quotes
    return [{**{k: v for k, v in q.items() if k != 'text'},
             **({'quote': q['text']} if 'text' in q else {})}
            if isinstance(q, dict) else q for q in quotes]


def identity(document):
    return (document.source_key or document.source or document.id, document.id)


def require_current_catalog(collection):
    if collection.get('schema_version') in (2, 3, 4, 5, 6, 7, 8, 9, 10):
        raise ValueError('Project facts use a historical collection format; regenerate derived facts offline with --replay-project-facts for saved claim collections, or extract from saved source pages')


def collect_project_facts(documents, model, max_chars=80000, chunk_pages=10, overlap_pages=1, language='de'):
    # Keep the shared entry-point arguments; only task passes use page windows.
    from ...shared.source_documents import full_source_documents
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
    from .rights import collect
    return collect(sources, snapshots, excluded, model, language)


def facts_view(collection):
    """Expose model interpretations alongside evidence, uncertainty and full sources."""
    view = {'status': collection.get('status', 'not_collected'),
            'catalog_protocol': collection.get('catalog_protocol'),
            'facts': [{**{k: copy.deepcopy(record[k]) for k in ('id', 'gemini', 'review_findings')},
                       'source_key': record['derived']['source_key'],
                       'document_id': record['derived']['document_id'],
                       'document_title': record['derived']['document_title'],
                       **({'sources': copy.deepcopy(record['derived']['sources'])}
                          if 'sources' in record['derived'] else {}),
                       **({'claims': copy.deepcopy(record['derived']['claims'])}
                          if 'claims' in record['derived'] else {}),
                       **({'context_citations': copy.deepcopy(record['derived']['context_citations'])}
                          if 'context_citations' in record['derived'] else {}),
                       'text': (record['derived']['text'] if collection.get('schema_version') in (4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15)
                                else source_text(record['derived'].get('verified_citations', []))),
                       'citations': copy.deepcopy(record['derived'].get('verified_citations', []))}
                      for record in collection.get('facts', [])],
            'excluded_passages': copy.deepcopy(collection.get('excluded_passages', [])),
            'review_findings': copy.deepcopy(collection.get('review_findings', [])),
            'source_snapshots': copy.deepcopy(collection.get('source_snapshots', [])),
            'excluded_sources': copy.deepcopy(collection.get('excluded_sources', []))}
    if collection.get('schema_version') in (5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15):
        view['evidence_protocol'] = collection['evidence_protocol']
        view['source_documents'] = copy.deepcopy(collection['source_documents'])
        for fact, record in zip(view['facts'], collection.get('facts', [])):
            raw = fact['gemini'] if isinstance(fact['gemini'], dict) else {}
            fact['interpretation'] = (record['derived'].get('interpretation')
                                      if collection['schema_version'] in (11, 12, 13, 14, 15) else raw.get('interpretation'))
            if collection['schema_version'] != 15:
                fact['uncertainty'] = (record['derived'].get('uncertainty')
                                        if collection['schema_version'] in (11, 12, 13, 14) else raw.get('uncertainty'))
            for field in ('provision_id', 'status', 'related_provision_ids', 'related_fact_ids'):
                if field in record['derived']:
                    fact[field] = copy.deepcopy(record['derived'][field])
    if collection.get('schema_version') in (14, 15):
        view['extraction_scope'] = collection['extraction_scope']
        view['processing'] = copy.deepcopy(collection['processing'])
    return view


def evidence_pages(collection):
    """Keep exclusions citable too: their relevance is a model judgment, not a fact."""
    if collection.get('schema_version') in (5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15):
        from ...shared.source_documents import citation_pages
        return citation_pages(collection['source_documents'])
    grouped = {}
    citations = [c for record in collection.get('facts', [])
                 for c in record.get('derived', {}).get('verified_citations', [])]
    citations += [record['derived']['citation'] for record in collection.get('excluded_passages', [])
                  if record['derived'].get('citation') is not None]
    for c in citations:
        key = (c['source_key'], c['document_id'], c['pdf_page'])
        grouped.setdefault(key, []).append(c['quote'])
    return [{'source_key': key[0], 'document_id': key[1], 'pdf_page': key[2],
             'text': '\n[separate source quote]\n'.join(dict.fromkeys(quotes))}
            for key, quotes in grouped.items()]


def document_facts_view(collection, document):
    """All facts for this exact source/document, with uncertainty left visible."""
    key = identity(document)
    view = facts_view(collection)
    view['facts'] = [f for f in view['facts'] if any(
        (c['source_key'], c['document_id']) == key for c in f['citations'])]
    ids = {f['id'] for f in view['facts']}
    view['review_findings'] = [f for f in view['review_findings']
                              if f.get('fact_id') in ids or
                              (f.get('source_key'), f.get('document_id')) == key or
                              any((s['source_key'], s['document_id']) == key for s in f.get('sources', []))]
    view['excluded_passages'] = [r for r in view['excluded_passages']
        if (r['derived']['source_key'], r['derived']['document_id']) == key]
    for field in ('source_snapshots', 'excluded_sources'):
        view[field] = [s for s in view[field] if (s.get('source_key'), s.get('document_id')) == key]
    return view
