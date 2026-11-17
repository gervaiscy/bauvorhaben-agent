"""Timing model input: original window and source-backed facts without duplicated catalogs."""
import copy
from ..project_facts.index import source_text

PROTOCOL = 'timing-page-quotes-v1'


def model_payload(payload):
    return {**{k: copy.deepcopy(payload[k]) for k in ('source_key', 'document') if k in payload},
            'source_pages': copy.deepcopy(payload['source_pages']),
            'working_units': copy.deepcopy(payload.get('working_units', [])),
            'project_facts': {'facts': [{'id': f['id'], 'text': f.get('text', source_text(f.get('citations', [])))}
                for f in payload.get('project_facts', {}).get('facts', [])]}}
