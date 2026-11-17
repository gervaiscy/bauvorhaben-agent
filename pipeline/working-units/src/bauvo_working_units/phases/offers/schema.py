"""Offers select original passages; Gemini never transcribes quotation text."""
from ...shared.response_types import obj, array

THINKING_LEVEL = 'MINIMAL'

CITATION = obj({
    'passage': {'type': 'string', 'description': 'Kurze Kennung einer Quelltextstelle im aktuellen Auszug, z. B. P58-1.'},
})
OFFER = obj({
    'name': {'type': 'string', 'description': 'Kurzer Name der angebotenen Leistung.'},
    'provider': {'type': ['string', 'null'], 'description': 'Anbieter der Leistung, soweit erkennbar.'},
    'description': {'type': 'string', 'description': 'Kurze Beschreibung der angebotenen Leistung.'},
    'terms': {**array({'type': 'string'}),
              'description': 'In der Quelle genannte Bedingungen, Preise und Grenzen dieses Angebots.'},
    'citations': {**array(CITATION), 'minItems': 1},
})
RESPONSE = obj({'offers': array(OFFER)})
