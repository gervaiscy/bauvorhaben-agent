"""Shared JSON schema building blocks."""
def obj(properties):
    return {'type': 'object', 'additionalProperties': False,
            'required': list(properties), 'properties': properties}


def array(item):
    return {'type': 'array', 'items': item}


TEXT = {'type': 'string'}

NULL_TEXT = {'anyOf': [TEXT, {'type': 'null'}]}

CITATION = obj({'pdf_page': {'type': 'integer'}, 'quote': TEXT})

CITATION['properties'].update(source_key=TEXT, document_id=TEXT)
CITATIONS = array(CITATION)

MODALITY = {'type': 'string', 'enum': ['REQUIRED', 'RECOMMENDED', 'OPTIONAL', 'PERMISSION', 'UNCLEAR']}

