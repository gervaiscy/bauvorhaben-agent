"""Response contract owned by this phase."""
from ...shared.response_types import obj, array, TEXT, NULL_TEXT, CITATIONS

PROJECT_FACT = obj({'verpflichtung': TEXT,
                    'zitate': array(obj({'pdf_page': {'type': 'integer'}, 'text': TEXT}))})

