"""Consequences phase: its own context, batching and derived output policy."""
import sys
from ...page_flow import select_assessment_chunk, unit_batches
from ...project_facts import facts_view, evidence_pages

PASS = 'PAGE_CONSEQUENCES'
WINDOW_FIELD = 'assessment_window_pages'
TRACK_CONTEXT = False


def select_window(unit, original, chunks):
    return select_assessment_chunk(unit['gemini'].get('citations'), original, chunks)


def batches(candidates):
    return unit_batches(candidates)


def request_data(ctx, document, units):
    data = {'working_units': units, 'supported_anchors': [a for a in ctx.anchors if a['document_id'] == document.id]}
    return data


def review_data(row, document, anchors):
    return {}


def citation_sources(ctx, document, local_pages):
    return local_pages, []


def run(ctx):
    from ...shared.assessments import run as assess
    assess(ctx, sys.modules[__name__])
