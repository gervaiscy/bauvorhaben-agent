"""Synthetic transport fixtures; these do not establish semantic model accuracy."""
from types import SimpleNamespace
from clause_fixtures import decode_prompt, index_answer
from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.project_facts import collect_project_facts
from bauvo_working_units.project_flow import Document


def document(identifier, key, pages, kind='project'):
    return Document(identifier, identifier, key+'.pdf', schema_version=6, source_key=key,
        source_snapshot=key+'-hash', source_type=kind,
        pages=tuple({'pdf_page': n, 'text': text, 'checkboxes': []} for n, text in pages))


def assessment_answer(data, classification='NOT_STATED', reasoning='Aus den bereitgestellten Unterlagen ergibt sich keine belegbare Folge.'):
    return {label: {'classification': classification, 'reasoning': reasoning} for label in data['working_units']}


def baseline(counts=(1,)):
    docs = [document(f'D{i+1}', 'blb', [(i*13+n, f'Der Bauherr stellt Bauwasser bereit. Originalseite {i*13+n}.')
             for n in range(1,14)], 'blb') for i in range(len(counts))]
    contract = document('C1', 'contract', [(1, 'Eine Freigabe setzt Antrag oder gleichwertigen Nachweis voraus.')])
    facts = collect_project_facts([contract], SimpleNamespace(extract=lambda p: index_answer(decode_prompt(p))))
    def earlier(prompt):
        phase = prompt.split('.', 1)[0]; data = decode_prompt(prompt)
        if phase == 'PAGE_EXTRACTION':
            i = next(i for i, d in enumerate(docs) if d.id == data['document']['id'])
            count = counts[i] if data['source_pages'][0]['pdf_page'] == docs[i].pages[0]['pdf_page'] else 0
            return {'working_units': [{'title': f'Bauwasser {n}', 'actor': 'Bauherr',
                      'quote': data['source_pages'][0]['text']} for n in range(count)]}
        if phase == 'PAGE_OFFERS': return {'offers': []}
        return {'assessments': [{'unit_id': u['id'], 'answer': 'NOT_STATED', 'citations': []}
                                for u in data['working_units']]}
    result = extract_page_units(docs, SimpleNamespace(extract=earlier), context_documents=[contract],
                                  project_facts=facts, stop_after='PAGE_TIMING')
    return result, docs, [contract]


# Historical claim fixture retained only for citation-validator unit tests.
def claim(citation, classification='DERIVED', identifier='C1'):
    return {'id': identifier, 'classification': classification,
        'claim': 'Ohne vollständigen Antrag kann die Freigabe ausbleiben.', 'trigger': 'Antrag fehlt',
        'conditions': 'Wenn kein alternativer Nachweis vorliegt.', 'exceptions': '',
        'alternatives': 'Ein gleichwertiger Nachweis genügt ebenfalls.', 'citations': [citation],
        'premises': [{'statement': 'Die Freigabe erfordert Antrag oder Nachweis.', 'citations': [citation]}]
                    if classification == 'DERIVED' else [],
        'reasoning': 'Fehlen beide Alternativen, fehlt eine Voraussetzung der Freigabe.'
                     if classification == 'DERIVED' else ''}
