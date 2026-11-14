"""Synthetic answers exercise transport; they do not establish model accuracy."""
from bauvo_working_units.citation_evidence import catalog


def index_answer(data, include=lambda entry: True):
    from bauvo_working_units.phases.project_facts.payload import expand_payload
    data = expand_payload(data) if 'evidence_catalog' not in data else data
    if data.get('evidence_protocol') in ('contractual-rights-v1', 'contractual-rights-v2', 'contractual-rights-v3'):
        # A transport-only current-schema test double, not a semantic extractor.
        targets = set(data['target_evidence_ids'])
        groups = {}
        for entry in data['evidence_catalog']:
            if entry['evidence_id'] in targets and include(entry):
                groups.setdefault((entry['source_key'], entry['document_id'], entry['pdf_page']), []).append(entry['evidence_id'])
        return {'ansprueche': [{'aussage': 'Synthetic homeowner entitlement',
                               'belege': refs, **({'unklarheit': None} if data['evidence_protocol']=='contractual-rights-v1' else {})} for refs in groups.values()]}
    groups, omitted = {}, []
    targets = set(data.get('target_evidence_ids', [e['evidence_id'] for e in data['evidence_catalog']]))
    for entry in data['evidence_catalog']:
        if entry['evidence_id'] not in targets:
            continue
        if include(entry):
            key = (entry['source_key'], entry['document_id'], entry['pdf_page'])
            groups.setdefault(key, []).append({'evidence_id': entry['evidence_id']})
        else:
            omitted.append({'evidence_id': entry['evidence_id'],
                            'grund': 'Outside this synthetic fixture selection'})
    return owned_answer(data, {'verpflichtungen': [interpreted(refs) for refs in groups.values()],
                              'nicht_als_fakt_erfasst': omitted})


def owned_answer(data, answer):
    """Supply explicit metadata for synthetic providers, never for real responses."""
    import copy
    answer = copy.deepcopy(answer)
    if data.get('evidence_protocol') != 'source-backed-interpretations-v9':
        return answer
    owners = {i: u['id'] for u in data['provisions'] for i in u['evidence_ids']}
    for row in answer.get('verpflichtungen', []):
        if not isinstance(row, dict):
            continue
        claims = row.get('claims', [])
        selected = [i for c in claims if isinstance(c, dict)
                    for i in (c.get('evidence_ids') if isinstance(c.get('evidence_ids'), list) else [])
                    if isinstance(i, str)] if isinstance(claims, list) else []
        owner = next((owners[i] for i in selected if i in owners), data['target_provision_ids'][0])
        row.setdefault('provision_id', owner)
        row.setdefault('status', 'CONTRACT_RULE')
        row.setdefault('related_provision_ids', [])
    return answer


def entries_for(doc):
    return catalog([{**p, 'source_key': doc.source_key or doc.source or doc.id,
                     'document_id': doc.id} for p in doc.pages])


def interpreted(refs, interpretation='Synthetic interpretation', uncertainty=None):
    return {'interpretation': interpretation, 'uncertainty': uncertainty,
            'claims': [{'statement': interpretation, 'evidence_ids': [ref.get('evidence_id') for ref in refs]}],
            'zitate': refs}


def remap_references(answer, mapping):
    """Prepare synthetic provider selections; never change their prose."""
    import copy
    answer = copy.deepcopy(answer)
    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'evidence_id' and isinstance(item, str):
                    value[key] = mapping.get(item, item)
                elif key == 'evidence_ids' and isinstance(item, list):
                    value[key] = [mapping.get(i, i) if isinstance(i, str) else i for i in item]
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(answer)
    # Explicitly constructed historical fixtures use their original response shape.
    if mapping and all(isinstance(i, str) and i.startswith('E') for i in mapping.values()):
        for row in answer.get('verpflichtungen', []):
            if isinstance(row, dict):
                for key in ('provision_id', 'status', 'related_provision_ids'):
                    row.pop(key, None)
    return answer


def labeled_answer(documents, answer):
    from bauvo_working_units.phases.project_facts.full_context import context
    from bauvo_working_units.phases.project_facts.payload import reference_map
    from bauvo_working_units.shared.source_documents import full_source_documents
    from bauvo_working_units.phases.project_facts.provisions import provisions, model_provisions
    pages, entries, _, _ = context(full_source_documents(documents))
    mapping = reference_map(entries)
    units = model_provisions(provisions(pages, entries), mapping)
    data = {'evidence_protocol': 'source-backed-interpretations-v9', 'provisions': units,
            'target_provision_ids': [u['id'] for u in units]}
    return owned_answer(data, remap_references(answer, {identifier: label for label, identifier in mapping.items()}))


def answer_for(doc, rows=None):
    entries = entries_for(doc)
    if rows is None:
        return labeled_answer([doc], index_answer({'evidence_catalog': entries}))
    rows = [{**interpreted(row.get('zitate', []), row.get('interpretation', 'Synthetic interpretation'),
                          row.get('uncertainty')), **row} for row in rows]
    selected = {ref.get('evidence_id') for row in rows for ref in row.get('zitate', [])}
    return labeled_answer([doc], {'verpflichtungen': rows, 'nicht_als_fakt_erfasst': [
        {'evidence_id': e['evidence_id'], 'grund': 'Outside this synthetic fixture selection'}
        for e in entries if e['evidence_id'] not in selected]})


def historical_index(doc, schema=3):
    """An old-format synthetic artifact, using its original catalog and text assembly."""
    import copy
    from bauvo_working_units.citation_evidence import LEGACY_CATALOG, CATALOG_PROTOCOL
    from bauvo_working_units.core import digest
    from bauvo_working_units.phases.project_facts.index import index_response
    from bauvo_working_units.shared.source_documents import full_source_documents
    key=doc.source_key or doc.source or doc.id
    pages=[{**p,'source_key':key,'document_id':doc.id} for p in doc.pages]
    entries=catalog(pages,protocol=CATALOG_PROTOCOL if schema in (4,5) else LEGACY_CATALOG)
    raw=index_answer({'evidence_catalog':entries})
    for row in raw['verpflichtungen']:
        row.pop('claims')
    if schema != 5:
        for row in raw['verpflichtungen']:
            row.pop('interpretation');row.pop('uncertainty')
    values,excluded,coverage,issues=index_response(raw,entries,pages if schema in (4,5) else (),interpretations=schema==5)
    rid=f'{key}:PF0001'
    metadata={'source_key':key,'document_id':doc.id,'document_title':doc.title,
              'pages':[p['pdf_page'] for p in doc.pages],
              'primary_pages':[p['pdf_page'] for p in doc.pages]}
    facts=[{'id':f'{rid}:fact:{i:03d}','gemini':copy.deepcopy(raw['verpflichtungen'][i]),
            'review_findings':[], 'derived':{**metadata,'response_id':rid,'response_position':i,**v}}
           for i,v in enumerate(values)]
    if schema==2:
        for fact in facts:fact['gemini']['verpflichtung']='Historical synthetic summary'
    return {'schema_version':schema,'evidence_protocol':
                {2:'source-span-ids-v1',3:'source-clause-index-v1',4:'source-clause-index-v2',
                 5:'source-backed-interpretations-v1'}[schema],
            **({'catalog_protocol':CATALOG_PROTOCOL} if schema in (4,5) else {}),
            **({'source_documents': full_source_documents([doc])} if schema==5 else {}),
            'status':'recorded','facts':facts,'excluded_passages':[],
            'source_snapshots':[{'source_key':key,'document_id':doc.id,
                'document_sha256':digest(list(doc.pages)),'pages_sha256':doc.source_snapshot}],
            'excluded_sources':[], 'windows':[metadata], 'review_findings':[],
            'gemini_responses':[{'id':rid,'pass':'PAGE_PROJECT_FACTS',**metadata,
                'gemini':raw,'coverage':coverage}]}


def decode_prompt(prompt):
    import json
    from bauvo_working_units.phases.project_facts.payload import expand_payload
    value = json.loads(prompt.split('SOURCE DATA:\n', 1)[1])
    if prompt.startswith('PAGE_EXTRACTION.') and all('text' in p for p in value['source_pages']):
        # Historical provider doubles still return source-ID responses. Construct
        # their old catalog locally; it is never part of the actual Gemini input.
        from bauvo_working_units.phases.aufgaben.payload import legacy_input
        from bauvo_working_units.phases.aufgaben.passages import passage_catalog
        wire, _ = legacy_input(value)
        entries = passage_catalog([{**p, 'source_key': value.get('source_key', 'source'),
                                   'document_id': value.get('document', {}).get('id', 'D1')}
                                  for p in value['source_pages']])
        labels = [label for p in wire['source_pages'] for label, _ in p['segments'] if label]
        return {**value, 'evidence_catalog': [{**entry, 'evidence_id': label}
                                            for label, entry in zip(labels, entries)]}
    if prompt.startswith('PAGE_EXTRACTION.') and value.get('reference_protocol') in ('source-labels-v1', 'source-paragraph-labels-v1'):
        # Legacy semantic test doubles consume ordinary pages. Decode the new
        # transport without changing source wording or synthetic predictions.
        entries, pages = [], []
        for page in value['source_pages']:
            plain = {k: page[k] for k in ('pdf_page', 'primary', 'checkboxes') if k in page}
            plain['text'] = ''.join(text for _, text in page['segments'])
            pages.append(plain)
            source = [{**plain, 'source_key': page['source_key'], 'document_id': page['document_id']}]
            if value['reference_protocol'] == 'source-paragraph-labels-v1':
                from bauvo_working_units.phases.aufgaben.passages import passage_catalog
                expanded = passage_catalog(source)
            else:
                expanded = catalog(source)
            labels = [label for label, _ in page['segments'] if label is not None]
            entries.extend({**entry, 'evidence_id': label} for label, entry in zip(labels, expanded))
        return {**value, 'source_pages': pages, 'evidence_catalog': entries}
    if not prompt.startswith('PAGE_PROJECT_FACTS.'):
        return value
    expanded = expand_payload(value)
    if 'reference_map' in expanded:
        labels = {identifier: label for label, identifier in expanded['reference_map'].items()}
        expanded['evidence_catalog'] = [{**entry, 'evidence_id': labels[entry['evidence_id']]}
                                        for entry in expanded['evidence_catalog']]
        expanded['target_evidence_ids'] = value['target_evidence_ids']
        if 'provisions' in value:
            expanded['provisions'] = value['provisions']
    return expanded
