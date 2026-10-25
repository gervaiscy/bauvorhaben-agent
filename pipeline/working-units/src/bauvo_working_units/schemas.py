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



# Per-phase reasoning settings are owned alongside each phase response contract.
from .phases.project_facts.schema import THINKING_LEVEL as PROJECT_FACTS_THINKING
from .phases.aufgaben.schema import THINKING_LEVEL as AUFGABEN_THINKING
from .phases.offers.schema import THINKING_LEVEL as OFFERS_THINKING
from .phases.timing.schema import THINKING_LEVEL as TIMING_THINKING
from .phases.consequences.schema import THINKING_LEVEL as CONSEQUENCES_THINKING
from .phases.applicability.schema import THINKING_LEVEL as APPLICABILITY_THINKING

PHASE_THINKING_LEVELS = {
    'PAGE_PROJECT_FACTS': PROJECT_FACTS_THINKING,
    'PAGE_EXTRACTION': AUFGABEN_THINKING,
    'PAGE_OFFERS': OFFERS_THINKING,
    'PAGE_TIMING': TIMING_THINKING,
    'PAGE_CONSEQUENCES': CONSEQUENCES_THINKING,
    'PAGE_APPLICABILITY': APPLICABILITY_THINKING,
}

def generation_for(pass_name):
    import copy
    result = copy.deepcopy(GENERATION)
    result['thinking_config']['thinking_level'] = PHASE_THINKING_LEVELS.get(pass_name, 'MINIMAL')
    return result
