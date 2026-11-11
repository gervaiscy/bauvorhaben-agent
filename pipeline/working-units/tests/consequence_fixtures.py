"""Generic structured consequence used by offline boundary tests."""

def claim(citation, classification='DERIVED', identifier='C1'):
    return {'id': identifier, 'classification': classification,
        'claim': 'Ohne vollständigen Antrag kann die Freigabe ausbleiben.', 'trigger': 'Antrag fehlt',
        'conditions': 'Wenn kein alternativer Nachweis vorliegt.', 'exceptions': '',
        'alternatives': 'Ein gleichwertiger Nachweis genügt ebenfalls.', 'citations': [citation],
        'premises': [{'statement': 'Die Freigabe erfordert Antrag oder Nachweis.', 'citations': [citation]}]
                    if classification == 'DERIVED' else [],
        'reasoning': 'Fehlen beide Alternativen, fehlt eine Voraussetzung der Freigabe.'
                     if classification == 'DERIVED' else ''}
