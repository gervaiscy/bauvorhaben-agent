"""Offer extraction uses saved extraction context without re-extracting working units."""
from ...shared.occurrences import run as run_occurrences

PASS = 'PAGE_OFFERS'
KEY = 'offers'
KIND = 'offer'
TARGET = 'offers'
TRACK_WINDOWS = False


def request_context(ctx, document, chunk, carry):
    return {'prior_context': ctx.extraction_context.get((document.id, chunk.pages), ''),
            'working_units': []}


def carry_forward(ctx, document, chunk, response):
    return ''


def valid_proposal(item):
    return isinstance(item, dict)


def run(ctx):
    import sys
    run_occurrences(ctx, sys.modules[__name__])
