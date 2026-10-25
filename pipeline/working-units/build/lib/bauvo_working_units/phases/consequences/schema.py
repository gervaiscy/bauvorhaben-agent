"""Response contract owned by this phase."""
from ...shared.response_types import obj, array, TEXT, NULL_TEXT, CITATIONS

CONSEQUENCES = obj({'unit_id': TEXT, 'status': {'type': 'string', 'enum': ['EXPLICIT', 'NOT_STATED']},
                    'consequences': array(TEXT), 'citations': CITATIONS})

