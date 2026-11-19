"""Structural validation only: does not certify legal or semantic correctness."""
from .schema import CLASSIFICATIONS


def response_findings(response, bindings):
    def issue(reason, label=None):
        return {'code': 'consequence_validation_failed', 'reason': reason,
                **({'unit_id': bindings[label]} if label in bindings else {})}
    if not isinstance(response, dict):
        return [issue('response_not_object')]
    findings = []
    if response.keys() != bindings.keys():
        findings.append(issue('task_mapping_mismatch'))
    for label in bindings:
        row = response.get(label)
        if not isinstance(row, dict) or set(row) != {'classification', 'reasoning'}:
            findings.append(issue('invalid_assessment_fields', label))
            continue
        if row['classification'] not in CLASSIFICATIONS:
            findings.append(issue('invalid_classification', label))
        if not isinstance(row['reasoning'], str) or not row['reasoning'].strip():
            findings.append(issue('empty_or_invalid_reasoning', label))
    return findings


def require_complete(result):
    """A standalone downstream command must honor the same structural gate."""
    if any(f.get('code', '').startswith('consequence_') for f in result.get('review_findings', [])):
        raise ValueError('Complete consequences before continuing; inspect preserved validation findings')
    for unit in result['working_units']:
        rows = [r['gemini'] for r in unit['reviews'] if r['kind'] == 'PAGE_CONSEQUENCES']
        if len(rows) != 1 or response_findings({'U1': rows[0]}, {'U1': unit['id']}):
            raise ValueError('Complete consequences before continuing: ' + unit['id'])
