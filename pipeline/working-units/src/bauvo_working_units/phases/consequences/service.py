"""Consequences use all supplied source documents, independently of target windows."""
import sys
from ...page_flow import select_assessment_chunk, unit_batches
from ...shared.source_documents import citation_pages, full_source_documents

PASS = 'PAGE_CONSEQUENCES'
WINDOW_FIELD = 'assessment_window_pages'
TRACK_CONTEXT = False


def select_window(unit, original, chunks):
    return select_assessment_chunk(unit['derived'].get('citations', unit['gemini'].get('citations')), original, chunks)


def batches(candidates):
    return unit_batches(candidates)


def request_data(ctx, document, units):
    return {'working_units': units, 'source_documents': ctx.consequence_sources}


def review_data(row, document, anchors):
    return {'context_scope': 'all_supplied_documents'}


def citation_sources(ctx, document, local_pages):
    return list(document.pages), citation_pages(ctx.consequence_sources)


def run(ctx):
    from ...shared.assessments import run as assess
    ctx.consequence_sources = full_source_documents([*ctx.documents, *ctx.context_documents])
    assess(ctx, sys.modules[__name__])
