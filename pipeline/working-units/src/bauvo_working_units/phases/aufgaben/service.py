"""Working-unit extraction owns primary-page traversal and continuation context."""
from ...shared.occurrences import run as run_occurrences

PASS = 'PAGE_EXTRACTION'
KEY = 'working_units'
KIND = 'working_unit'
TARGET = 'units'
TRACK_WINDOWS = True


def request_context(ctx, document, chunk, carry):
    return {'prior_context': carry}


def carry_forward(ctx, document, chunk, response):
    carry = response.get('carry_forward', '') if isinstance(response, dict) else ''
    if not isinstance(carry, str):
        carry = ''
    ctx.extraction_context[(document.id, chunk.pages)] = carry
    return carry


def valid_proposal(item):
    return isinstance(item, dict) and isinstance(item.get('action'), str)


def run(ctx):
    import sys
    run_occurrences(ctx, sys.modules[__name__])
