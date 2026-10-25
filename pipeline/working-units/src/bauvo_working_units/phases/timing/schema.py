"""Response contract owned by this phase."""
THINKING_LEVEL = 'MEDIUM'

from ...shared.response_types import obj, array, TEXT, NULL_TEXT, CITATIONS

NULL = {'type': 'null'}

CALCULATION = {'anyOf': [obj({'anchor_id': TEXT, 'offset_amount': {'type': 'integer', 'minimum': 0},
    'offset_unit': {'type': 'string', 'enum': ['DAYS', 'WEEKS', 'MONTHS', 'YEARS']}}), NULL]}

TIMING_COMMON = {'scope': TEXT, 'expression': TEXT, 'conditions': array(TEXT),
                 'alternative_group': NULL_TEXT, 'calculation': CALCULATION, 'citations': CITATIONS}

TIMING_RULE = {'anyOf': [
    obj({**TIMING_COMMON, 'kind': {'type': 'string', 'enum': ['DEADLINE', 'EVENT_RELATIVE']},
         'anchor_events': {**array(TEXT), 'maxItems': 1},
         'relation': {'anyOf': [{'type': 'string', 'enum': ['BEFORE', 'AFTER', 'BY', 'ON', 'WITHIN_AFTER']}, NULL]},
         'offset': NULL_TEXT, 'duration': NULL, 'date': NULL_TEXT}),
    obj({**TIMING_COMMON, 'kind': {'type': 'string', 'enum': ['PREREQUISITE']},
         'anchor_events': {**array(TEXT), 'maxItems': 1},
         'relation': {'anyOf': [{'type': 'string', 'enum': ['BEFORE', 'AFTER']}, NULL]},
         'offset': NULL, 'duration': NULL, 'date': NULL}),
    obj({**TIMING_COMMON, 'kind': {'type': 'string', 'enum': ['DURATION']},
         'anchor_events': {**array(TEXT), 'maxItems': 0},
         'relation': NULL, 'offset': NULL, 'duration': TEXT, 'date': NULL}),
]}

TIMING = obj({'unit_id': TEXT, 'status': {'type': 'string', 'enum': ['EXPLICIT', 'NOT_STATED', 'AMBIGUOUS']},
              'timing_rules': array(TIMING_RULE), 'missing_evidence': array(TEXT)})


def timing_findings(response):
    """Flag inconsistent timing fields only; no source interpretation or text correction."""
    findings = []
    rows = response.get('assessments') if isinstance(response, dict) else None
    if not isinstance(rows, list):
        return findings
    for position, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get('timing_rules'), list):
            continue  # Routing/JSON schema checks handle malformed collections.
        rules = row['timing_rules']
        if (row.get('status') == 'NOT_STATED' and rules) or (row.get('status') == 'EXPLICIT' and not rules):
            findings.append({'row': position, 'code': 'inconsistent_timing_status'})
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                findings.append({'row': position, 'rule': index, 'code': 'invalid_timing_rule'})
                continue
            kind, anchors = rule.get('kind'), rule.get('anchor_events')
            valid = isinstance(anchors, list) and all(isinstance(a, str) and a.strip() for a in anchors)
            if kind == 'DURATION':
                valid = valid and not anchors and isinstance(rule.get('duration'), str) and bool(rule['duration'].strip())
                valid = valid and all(rule.get(k) is None for k in ('relation', 'offset', 'date'))
            elif kind in ('DEADLINE', 'EVENT_RELATIVE', 'PREREQUISITE'):
                valid = valid and len(anchors) <= 1 and rule.get('duration') is None
                relations = (None, 'BEFORE', 'AFTER') if kind == 'PREREQUISITE' else (None, 'BEFORE', 'AFTER', 'BY', 'ON', 'WITHIN_AFTER')
                valid = valid and rule.get('relation') in relations
                if kind == 'PREREQUISITE':
                    valid = valid and rule.get('offset') is None and rule.get('date') is None
            else:
                valid = False
            if not valid:
                findings.append({'row': position, 'rule': index, 'code': 'inconsistent_timing_rule'})
            elif kind in ('DEADLINE', 'EVENT_RELATIVE') and not anchors and all(rule.get(k) is None for k in ('relation', 'offset', 'date')):
                findings.append({'row': position, 'rule': index, 'code': 'empty_relative_timing_rule'})
            elif kind in ('DEADLINE', 'EVENT_RELATIVE') and row.get('status') == 'EXPLICIT' and not anchors and rule.get('date') is None:
                findings.append({'row': position, 'rule': index, 'code': 'unresolved_timing_reference'})
    return findings

