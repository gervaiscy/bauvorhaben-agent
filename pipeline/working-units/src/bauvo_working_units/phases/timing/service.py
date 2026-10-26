"""Timing phase: its own context, batching and derived output policy."""
import sys
from ...page_flow import select_assessment_chunk, unit_batches
from ...project_facts import facts_view, evidence_pages

PASS = 'PAGE_TIMING'
WINDOW_FIELD = 'assessment_window_pages'
TRACK_CONTEXT = True


def select_window(unit, original, chunks):
    return select_assessment_chunk(unit['gemini'].get('citations'), original, chunks)


def batches(candidates):
    return unit_batches(candidates)


def request_data(ctx, document, units):
    data = {'working_units': units, 'supported_anchors': [a for a in ctx.anchors if a['document_id'] == document.id]}
    data['project_facts'] = {**facts_view(ctx.project_facts), 'scope': 'project_wide_contract_context'}
    return data


def review_data(row, document, anchors):
    if 'answer' in row:
        return {}  # Free text is evidence, not a machine-calculable schedule.
    from .date_calculation import calculate_dates
    return {'date_calculations': calculate_dates(row, document, anchors)}  # Legacy structured callers only.


def citation_sources(ctx, document, local_pages):
    supporting = evidence_pages(ctx.project_facts)
    local_numbers = {p['pdf_page'] for p in local_pages}
    local_pages = local_pages + [p for p in supporting
        if (p['source_key'], p['document_id']) ==
           (document.source_key or document.source or document.id, document.id)
        and p['pdf_page'] not in local_numbers]
    return local_pages, supporting


def run(ctx):
    from ...shared.assessments import run as assess
    assess(ctx, sys.modules[__name__])
