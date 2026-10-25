"""Response contract owned by this phase."""
from ...shared.response_types import obj, array, TEXT, NULL_TEXT, CITATIONS

APPLICABILITY = obj({'unit_id': TEXT,
    'applicability': {'type': 'string', 'enum': ['APPLIES', 'DOES_NOT_APPLY', 'PARTIALLY_APPLIES', 'CONDITIONAL', 'UNKNOWN']},
    'reason': TEXT})

