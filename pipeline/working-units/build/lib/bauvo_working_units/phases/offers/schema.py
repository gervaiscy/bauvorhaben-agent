"""Response contract owned by this phase."""
from ...shared.response_types import obj, array, TEXT, NULL_TEXT, CITATIONS

OFFER = obj({'name': TEXT, 'provider': NULL_TEXT,
             'description': TEXT, 'terms': array(TEXT), 'citations': CITATIONS})

