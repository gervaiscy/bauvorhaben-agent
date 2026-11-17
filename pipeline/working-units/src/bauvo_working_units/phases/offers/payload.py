"""Current-window original text and compact, source-backed project facts."""
PROTOCOL = 'window-additional-offers-v7'
LEGACY_PROTOCOL = 'window-additional-offers-v6'
REFERENCE_PROTOCOL = 'offer-passage-labels-v1'


def _fact_evidence(fact, aliases, originals):
    """Keep literal quotations once per fact, joining only adjoining original spans."""
    evidence = []
    seen = set()
    for citation in fact.get('citations', []):
        key = (citation.get('source_key'), citation.get('document_id'), citation.get('pdf_page'))
        quote = citation.get('quote')
        if not isinstance(quote, str) or not quote.strip():
            continue
        marker = (*key, quote)
        if marker in seen:
            continue
        seen.add(marker)
        source = originals.get(key)
        start, end = citation.get('start'), citation.get('end')
        verified_span = (source is not None and type(start) is int and type(end) is int
                         and 0 <= start < end <= len(source) and source[start:end] == quote)
        document = aliases.get(key[:2])
        if document is None:
            raise ValueError('Project-fact evidence requires its original source document')
        if (verified_span and evidence and evidence[-1][0] == key
                and evidence[-1][1] == start):
            evidence[-1][1] = end
            evidence[-1][2]['quote'] += quote
        else:
            evidence.append([key, end if verified_span else None,
                {'document': document, 'pdf_page': key[2], 'quote': quote}])
    return [entry[2] for entry in evidence]


def fact_context(view):
    """Interpretations and supporting quotes, without full source documents or IDs."""
    sources = view.get('source_documents', [])
    aliases, titles, originals = {}, {}, {}
    for source in sources:
        key = (source['source_key'], source['document_id'])
        aliases.setdefault(key, f'D{len(aliases) + 1}')
        titles.setdefault(key, source.get('title', ''))
        for page in source['pages']:
            page_key = (*key, page['pdf_page'])
            if page_key in originals and originals[page_key] != page['text']:
                raise ValueError('Conflicting original project-fact source text')
            originals[page_key] = page['text']
    facts = []
    for fact in view.get('facts', []):
        row = {'interpretation': fact.get('interpretation') or fact.get('text', ''),
               'evidence': _fact_evidence(fact, aliases, originals)}
        for field in ('uncertainty', 'status'):
            if fact.get(field):
                row[field] = fact[field]
        if fact.get('context_citations'):
            row['context_evidence'] = _fact_evidence({'citations': fact['context_citations']}, aliases, originals)
        if fact.get('claims'):
            row['claims'] = [{'statement': claim['statement'],
                              'evidence': _fact_evidence({'citations': claim.get('verified_citations', [])},
                                                         aliases, originals)} for claim in fact['claims']]
        facts.append(row)
    result = {'facts': facts}
    if sources:
        result['documents'] = [{'id': alias, 'title': titles[key]} for key, alias in aliases.items()]
    if 'extraction_scope' in view:
        result['extraction_scope'] = view['extraction_scope']
    if 'semantic_completeness' in view.get('processing', {}):
        result['processing'] = {'semantic_completeness': view['processing']['semantic_completeness']}
    return result


def model_payload(payload):
    from .passages import selectable_pages
    # Send each primary page exactly once. Previous overlap pages stay local:
    # otherwise the simple prompt could re-extract their offers in the next call.
    return {'source_pages': selectable_pages(payload['source_pages']),
            'project_facts': fact_context(payload.get('project_facts', {}))}
