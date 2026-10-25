"""Collect cited project statements without ranking, merging or deciding conflicts."""
import copy
from ...core import digest
from ...citation_checks import citation_findings
from ...project_flow import make_page_chunks, source_pages
from ...schemas import PROJECT_FACT
from ...trace import ModelRequestFailed


def fact_citations(fact):
    """Adapt field names for checks; keep provider statements and quotes untouched."""
    quotes = fact.get('zitate') if isinstance(fact, dict) else None
    if not isinstance(quotes, list):
        return quotes
    return [{**{k: v for k, v in q.items() if k != 'text'}, 'quote': q.get('text')}
            if isinstance(q, dict) else q for q in quotes]


def identity(document):
    return (document.source_key or document.source or document.id, document.id)


def collect_project_facts(documents, model, max_chars=80000, chunk_pages=3, overlap_pages=1, language='de'):
    from ...page_flow import _prompt, _payload
    if language not in ('de', 'en'):
        raise ValueError('Unsupported output language')
    if any(d.schema_version != 6 for d in documents):
        raise ValueError('Project facts require schema-6 pages')
    if any(d.source_type not in ('project', 'blb') for d in documents):
        raise ValueError('Source type must be project or blb')
    facts, responses, findings, windows, snapshots, excluded = [], [], [], [], [], []
    seen = {}
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
            continue
        snapshots.append({'source_key': key[0], 'document_id': key[1],
                          'document_sha256': snapshot, 'pages_sha256': doc.source_snapshot})
    # Validate every source and window before the first paid request.
    jobs = []
    seen.clear()
    for doc in documents:
        key = identity(doc)
        if doc.source_type != 'blb' and key not in seen:
            jobs.extend((doc, chunk) for chunk in make_page_chunks(doc, max_chars, chunk_pages, overlap_pages))
            seen[key] = True
    for doc, chunk in jobs:
        key = identity(doc)
        pages = source_pages(doc, chunk)
        metadata = {'source_key': key[0], 'document_id': key[1], 'document_title': doc.title,
                    'pages': list(chunk.pages), 'primary_pages': list(chunk.primary_pages)}
        windows.append(metadata)
        if not any(p['text'].strip() for p in pages if p['primary']):
            continue
        rid = f'{key[0]}:PF{len(responses)+1:04d}'
        try:
            response = model.extract(_prompt('PAGE_PROJECT_FACTS', _payload(doc, chunk), language))
        except ModelRequestFailed as exc:
            issue = {'code': 'model_request_failed', 'pass': 'PAGE_PROJECT_FACTS',
                     'response_id': rid, **metadata, 'attempts': exc.attempts}
            findings.append(issue)
            responses.append({'id': rid, 'pass': 'PAGE_PROJECT_FACTS', **metadata,
                              'status': 'request_failed', 'gemini': None, 'failure': issue})
            continue
        responses.append({'id': rid, 'pass': 'PAGE_PROJECT_FACTS', **metadata, 'gemini': copy.deepcopy(response)})
        rows = response.get('verpflichtungen') if isinstance(response, dict) else None
        if not isinstance(rows, list):
            findings.append({'code': 'missing_or_invalid_project_facts', 'response_id': rid})
            continue
        for index, fact in enumerate(rows):
            fid = f'{rid}:fact:{index:03d}'
            citations = fact_citations(fact)
            issues = citation_findings({'project_facts': [{'citations': citations, 'proposal': fact}]},
                                       pages, source_key=key[0], document_id=key[1])
            verified = []
            valid = isinstance(fact, dict) and set(PROJECT_FACT['required']) == set(fact)
            if valid:
                valid = isinstance(fact['verpflichtung'], str) and bool(fact['verpflichtung'].strip())
                valid = valid and isinstance(fact['zitate'], list) and all(
                    isinstance(q, dict) and set(q) == {'pdf_page', 'text'} for q in fact['zitate'])
            if not valid:
                issues.append({'code': 'invalid_project_fact'})
            if isinstance(citations, list):
                for q, c in zip(fact['zitate'], citations):
                    if isinstance(q, dict) and set(q) == {'pdf_page', 'text'} and not citation_findings(
                            {'citations': [c]}, pages, source_key=key[0], document_id=key[1]):
                        verified.append({**copy.deepcopy(c), 'source_key': key[0], 'document_id': key[1]})
            findings.extend({'fact_id': fid, 'response_id': rid, **issue} for issue in issues)
            facts.append({'id': fid, 'gemini': copy.deepcopy(fact), 'review_findings': issues,
                          'derived': {**metadata, 'response_id': rid, 'response_position': index,
                                      'verified_citations': verified}})
    return {'schema_version': 2, 'status': 'request_failed' if any(f['code']=='model_request_failed' for f in findings)
            else 'needs_review' if findings else 'no_project_sources' if not snapshots else 'recorded', 'facts': facts, 'source_snapshots': snapshots,
            'excluded_sources': excluded, 'windows': windows, 'gemini_responses': responses, 'review_findings': findings}


def facts_view(collection):
    """All statements and issues go to applicability, including contradictory proposals."""
    return {'status': collection.get('status', 'not_collected'),
            'facts': [{**{k: copy.deepcopy(record[k]) for k in ('id', 'gemini', 'review_findings')},
                       'source_key': record['derived']['source_key'],
                       'document_id': record['derived']['document_id']}
                      for record in collection.get('facts', [])],
            'review_findings': copy.deepcopy(collection.get('review_findings', [])),
            'source_snapshots': copy.deepcopy(collection.get('source_snapshots', [])),
            'excluded_sources': copy.deepcopy(collection.get('excluded_sources', []))}


def evidence_pages(collection):
    """Only source-checked quotes support cross-document applicability citations."""
    grouped = {}
    for record in collection.get('facts', []):
        for c in record.get('derived', {}).get('verified_citations', []):
            key = (c['source_key'], c['document_id'], c['pdf_page'])
            grouped.setdefault(key, []).append(c['quote'])
    return [{'source_key': key[0], 'document_id': key[1], 'pdf_page': key[2],
             'text': '\n[separate source quote]\n'.join(dict.fromkeys(quotes))}
            for key, quotes in grouped.items()]


def document_facts_view(collection, document):
    """All facts for this exact source/document, with uncertainty left visible."""
    key = identity(document)
    view = facts_view(collection)
    view['facts'] = [f for f in view['facts'] if (f['source_key'], f['document_id']) == key]
    ids = {f['id'] for f in view['facts']}
    view['review_findings'] = [f for f in view['review_findings']
                              if f.get('fact_id') in ids or
                              (f.get('source_key'), f.get('document_id')) == key]
    for field in ('source_snapshots', 'excluded_sources'):
        view[field] = [s for s in view[field] if (s.get('source_key'), s.get('document_id')) == key]
    return view
