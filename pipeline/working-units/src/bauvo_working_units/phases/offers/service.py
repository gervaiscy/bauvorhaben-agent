"""Offer extraction uses only original primary-page windows and saved project facts."""
from ...shared.occurrences import run as run_occurrences
from .validation import valid_proposal, validate
from .payload import PROTOCOL
from .context import PROTOCOL as CONTEXT_PROTOCOL, review_context

PASS = 'PAGE_OFFERS'
KEY = 'offers'
KIND = 'offer'
TARGET = 'offers'
TRACK_WINDOWS = False


def request_context(ctx, document, chunk, carry):
    return {}


def citation_sources(ctx, document, local_pages):
    return [p for p in local_pages if p.get('primary', True)], []


def carry_forward(ctx, document, chunk, response):
    return ''


def derived_data(item, resolved, document, chunk):
    citations = resolved.get('citations', []) if isinstance(resolved, dict) else []
    return {'review_context_protocol': CONTEXT_PROTOCOL,
            'review_context': review_context(citations, document) if isinstance(citations, list) else []}


def run(ctx):
    import sys
    from ...project_flow import source_pages
    # There are no earlier-answer dependencies in the new wire input. Plan all
    # exact requests before any paid call, including cached/replayable answers.
    prompts = [ctx.request(PASS, document, chunk, {}, plan_only=True)
               for document in ctx.documents for chunk in ctx.chunks[document.id]
               if any(p['text'].strip() for p in source_pages(document, chunk) if p['primary'])]
    ctx.plan_requests(prompts)
    run_occurrences(ctx, sys.modules[__name__])
    # A bad quotation must not hide offers in later windows. Validate the whole
    # collected pass once; page_flow still blocks downstream stages on findings.
    validate(ctx)
