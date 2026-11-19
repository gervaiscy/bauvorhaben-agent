"""Compact model input; provenance and raw contract pages stay in the application."""
import copy
from ..timing.artifact import review_view
from ...project_facts import facts_view
from ...shared.source_documents import full_source_documents
from ..timing.schema import timing_findings


def task_rows(units, facts):
    rows = review_view({'working_units': units, 'project_facts': facts, 'status': 'recorded'})['working_units']
    invalid = []
    for row in rows:
        timing = row['timing']
        current = (isinstance(timing, dict) and isinstance(timing.get('answer'), str)
                   and bool(timing['answer'].strip()) and isinstance(timing.get('citations'), list))
        legacy = (isinstance(timing, dict) and timing.get('status') in ('EXPLICIT', 'NOT_STATED', 'AMBIGUOUS')
                  and isinstance(timing.get('timing_rules'), list) and isinstance(timing.get('missing_evidence'), list)
                  and not timing_findings({'assessments': [timing]}))
        if not (current or legacy) or 'additional_timing_assessments' in row:
            invalid.append(row['id'])
        if not isinstance(row['quote'], str) or not row['quote'].strip():
            raise ValueError(f"Task has no usable original quotation: {row['id']}")
    if invalid:
        raise ValueError('Every task requires exactly one timing assessment before consequences: ' + ', '.join(invalid))
    return [{key: copy.deepcopy(row[key]) for key in ('id', 'title', 'actor', 'quote', 'timing')} for row in rows]


def model_payload(payload):
    sources = full_source_documents(payload.get('documents', []))
    facts = facts_view(payload['project_facts']) if 'schema_version' in payload.get('project_facts', {}) else payload.get('project_facts', {})
    return {'source_scope': 'BLB_AND_PROJECT_FACTS' if sources else 'PROJECT_FACTS_ONLY',
            'source_documents': [{'document': f'B{i+1}', 'title': d['title'],
                'pages': [{'pdf_page': p['pdf_page'], 'text': p['text']} for p in d['pages']]}
                for i, d in enumerate(sources)],
            'project_facts': [{'id': f['id'], 'text': f['text']} for f in facts.get('facts', [])],
            'working_units': copy.deepcopy(payload['working_units'])}
