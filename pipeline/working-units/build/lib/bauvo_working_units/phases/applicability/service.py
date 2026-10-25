"""Applicability phase: its own context, batching and derived output policy."""
import copy
import sys
from ...page_flow import select_assessment_chunk, unit_batches
from ...project_facts import facts_view, evidence_pages

PASS = 'PAGE_APPLICABILITY'
WINDOW_FIELD = 'applicability_window_pages'
TRACK_CONTEXT = False


def select_window(unit, original, chunks):
    return (original, None)


def batches(candidates):
    return [candidates] if candidates else []


def request_data(ctx, document, units):
    data = {'working_units': units, 'supported_anchors': [a for a in ctx.anchors if a['document_id'] == document.id]}
    data['project_facts'] = facts_view(ctx.project_facts)
    return data


def review_data(row, document, anchors):
    return {}


def citation_sources(ctx, document, local_pages):
    return local_pages, evidence_pages(ctx.project_facts)


def model_payload(payload):
    # Keep application fingerprints and collection state in artifacts, not model input.
    payload = {key: copy.deepcopy(payload[key]) for key in
               ('source_key', 'document', 'source_pages', 'working_units', 'project_facts')
               if key in payload}
    payload['source_pages'] = [{key: page[key] for key in ('pdf_page', 'text')}
                               for page in payload['source_pages']]
    payload['project_facts'] = {key: value for key, value in payload.get('project_facts', {}).items()
                                if key in ('facts', 'review_findings')}
    return payload


def run(ctx):
    from ...shared.assessments import run as assess
    assess(ctx, sys.modules[__name__])
