"""Compatibility exports and registry for phase-owned response contracts."""
PROTOCOL = 'page-working-units-v44'

UNIT_BATCH_SIZE = 5

GENERATION = {'temperature': 0, 'response_mime_type': 'application/json',
              'max_output_tokens': 16384, 'thinking_config': {'thinking_level': 'MINIMAL'}}

from .shared.response_types import *  # noqa: F401,F403
from .phases.project_facts.schema import PROJECT_FACT
from .phases.aufgaben.schema import UNIT
from .phases.offers.schema import OFFER
from .phases.applicability.schema import APPLICABILITY
from .phases.timing.schema import NULL, CALCULATION, TIMING_COMMON, TIMING_RULE, TIMING, timing_findings
from .phases.consequences.schema import CONSEQUENCES

RESPONSE_SCHEMAS = {
    'PAGE_PROJECT_FACTS': obj({'verpflichtungen': array(PROJECT_FACT)}),
    'PAGE_EXTRACTION': obj({'working_units': array(UNIT), 'carry_forward': TEXT}),
    'PAGE_OFFERS': obj({'offers': array(OFFER)}),
    'PAGE_APPLICABILITY': obj({'assessments': array(APPLICABILITY)}),
    'PAGE_TIMING': obj({'assessments': array(TIMING)}),
    'PAGE_CONSEQUENCES': obj({'assessments': array(CONSEQUENCES)}),
}

