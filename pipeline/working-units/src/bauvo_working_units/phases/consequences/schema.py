"""One two-field assessment per short task label; no claim or evidence graph."""
import json
from ...shared.response_types import obj, TEXT

THINKING_LEVEL = 'MEDIUM'
BATCH_SIZE = 10
CLASSIFICATIONS = ('EXPLICIT', 'DERIVED', 'NOT_STATED')
ASSESSMENT = obj({'classification': {'type': 'string', 'enum': list(CLASSIFICATIONS)},
                  'reasoning': TEXT})


def response_schema(labels):
    labels = list(labels)
    if not 1 <= len(labels) <= BATCH_SIZE or labels != [f'U{i+1}' for i in range(len(labels))]:
        raise ValueError('Consequences require one to ten consecutive task labels')
    return obj({label: ASSESSMENT for label in labels})


def schema_for_prompt(prompt):
    data = json.loads(prompt.split('SOURCE DATA:\n', 1)[1])
    return response_schema(data['working_units'])


CONSEQUENCES = response_schema([f'U{i+1}' for i in range(BATCH_SIZE)])

