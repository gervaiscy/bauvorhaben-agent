"""Offer extraction uses saved extraction context without re-extracting working units."""
from ...shared.occurrences import run as run_occurrences
from ...shared.source_documents import citation_pages, full_source_documents

PASS = 'PAGE_OFFERS'
KEY = 'offers'
KIND = 'offer'
TARGET = 'offers'
TRACK_WINDOWS = False


def request_context(ctx, document, chunk, carry):
    return {'prior_context': ctx.extraction_context.get((document.id, chunk.pages), ''),
            'working_units': [], 'source_documents': ctx.offer_sources}


def citation_sources(ctx, document, local_pages):
    return local_pages, citation_pages(ctx.offer_sources)


def carry_forward(ctx, document, chunk, response):
    return ''


def valid_proposal(item):
    return isinstance(item, dict)


def run(ctx):
    import sys
    ctx.offer_sources = full_source_documents([*ctx.documents, *ctx.context_documents])
    run_occurrences(ctx, sys.modules[__name__])
