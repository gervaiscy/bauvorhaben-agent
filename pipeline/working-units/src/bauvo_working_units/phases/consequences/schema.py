"""Response contract owned by this phase."""
THINKING_LEVEL = 'MEDIUM'

from ...shared.response_types import obj, array, TEXT, CITATIONS

QUALIFIED_CITATIONS = CITATIONS

CLAIM = obj({
    'id': TEXT, 'classification': {'type': 'string', 'enum': ['EXPLICIT', 'DERIVED']},
    'claim': TEXT, 'trigger': TEXT, 'conditions': TEXT, 'exceptions': TEXT,
    'alternatives': TEXT, 'citations': CITATIONS,
    'premises': array(obj({'statement': TEXT, 'citations': CITATIONS})), 'reasoning': TEXT,
})
CONSEQUENCES = obj({'unit_id': TEXT, 'status': {'type': 'string', 'enum': ['EXPLICIT', 'DERIVED', 'MIXED', 'NOT_STATED']},
                    'consequences': array(CLAIM)})

