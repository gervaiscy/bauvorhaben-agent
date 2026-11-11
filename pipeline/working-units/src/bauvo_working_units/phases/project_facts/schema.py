"""Response contract owned by this phase."""
THINKING_LEVEL = 'MEDIUM'

from ...shared.response_types import obj, array, TEXT, NULL_TEXT, CITATIONS
from .provisions import FACT_STATUSES

FACT_CLAIM = obj({'statement': {**TEXT, 'minLength': 1},
                  'evidence_ids': {**array({**TEXT, 'minLength': 1}), 'minItems': 1}})

PROJECT_FACT = obj({'provision_id': TEXT,
                    'status': {**TEXT, 'enum': list(FACT_STATUSES)},
                    'related_provision_ids': array(TEXT),
                    'uncertainty': NULL_TEXT,
                    'claims': {**array(FACT_CLAIM), 'minItems': 1},
                    'zitate': CITATIONS})
EXCLUDED_PASSAGE = obj({'evidence_id': TEXT, 'grund': TEXT})
CLAUSE_INDEX = obj({'verpflichtungen': array(PROJECT_FACT),
                    'nicht_als_fakt_erfasst': array(EXCLUDED_PASSAGE)})


# Current live contract; older clause schemas remain available for historical artifacts.
RIGHT = obj({'aussage': {**TEXT, 'minLength': 1},
             'belege': {**array({**TEXT, 'minLength': 1}), 'minItems': 1}})
RIGHTS = obj({'ansprueche': array(RIGHT)})
