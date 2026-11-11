"""Complete source text with unambiguous source/document identities."""
import copy

from ..project_facts import identity


def full_source_documents(documents):
    sources = {}
    for document in documents:
        key = identity(document)
        source = {'source_key': key[0], 'document_id': key[1], 'title': document.title,
                  'source_type': document.source_type, 'boundary_uncertain': document.boundary_uncertain,
                  'pages': copy.deepcopy(list(document.pages))}
        if key in sources and sources[key] != source:
            raise ValueError('Conflicting source documents')
        sources[key] = source
    return list(sources.values())


def citation_pages(sources):
    return [{**page, 'source_key': source['source_key'], 'document_id': source['document_id']}
            for source in sources for page in source['pages']]
