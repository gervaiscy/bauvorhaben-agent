"""Response contract owned by this phase."""
THINKING_LEVEL = 'MEDIUM'

from ...shared.response_types import obj, array, TEXT, NULL_TEXT

UNIT = obj({'title': {**TEXT, 'minLength': 1},
            'actor': NULL_TEXT,
            'quote': {**TEXT, 'minLength': 1}})

