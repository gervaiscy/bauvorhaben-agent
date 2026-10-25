"""Response contract owned by this phase."""
from ...shared.response_types import obj, array, TEXT, NULL_TEXT, CITATIONS

UNIT = obj({'name': TEXT, 'actor': NULL_TEXT, 'performed_by': NULL_TEXT,
            'action': TEXT, 'citations': CITATIONS})

