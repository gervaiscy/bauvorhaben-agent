"""Separate human timing review from the lossless internal pipeline artifact."""
import copy
import json
from pathlib import Path
import re
from .review_citations import compact_citations


def output_paths(output):
    output = Path(output)
    return (output, output.with_name(output.stem + '.audit.json'),
            output.with_name(output.stem + '.validation.json'))


def review_view(result, *, compact=True):
    pages = {(d['source_key'], d['document_id'], p['pdf_page']): p['text']
             for d in result.get('project_facts', {}).get('source_documents', [])
             for p in d.get('pages', [])}
    titles = {(d['source_key'], d['document_id']): d['title']
              for d in result.get('project_facts', {}).get('source_documents', [])}
    for unit in result['working_units']:
        source = unit['derived']
        titles[(source.get('source_key'), source.get('document_id'))] = source.get('document_title')

    units = []
    for unit in result['working_units']:
        raw, source = unit['gemini'], unit['derived']
        raw = raw if isinstance(raw, dict) else {}
        row = {'id': unit['id'], 'title': raw.get('title', raw.get('name')),
               'actor': raw.get('actor'),
               'quote': source.get('source_text', raw.get('quote', raw.get('action')))}
        if 'quote' in raw and not source.get('source_text'):
            row['quote_verified'] = False
        timings = []
        for review in unit['reviews']:
            if review['kind'] != 'PAGE_TIMING':
                continue
            timing = copy.deepcopy(review['gemini'])
            timing.pop('unit_id', None)
            resolved = review['derived'].get('resolved_evidence', review['gemini'])
            if isinstance(resolved.get('citations'), list):
                citations = []
                selected = (compact_citations(resolved['citations'], pages) if compact
                            else resolved['citations'])
                for citation in selected:
                    if isinstance(citation, dict) and 'quote' in citation and 'pdf_page' in citation:
                        key = (citation.get('source_key', source.get('source_key')),
                               citation.get('document_id', source.get('document_id')))
                        citations.append({'document': titles.get(key) or key[1],
                                          'pdf_page': citation['pdf_page'], 'quote': citation['quote']})
                    else:
                        # Unknown references remain visible; validation is separate.
                        citations.append(copy.deepcopy(citation))
                timing['citations'] = citations
            timings.append(timing)
        row['timing'] = timings[0] if timings else None
        if len(timings) > 1:
            row['additional_timing_assessments'] = timings[1:]
        units.append(row)
    return {'status': result['status'], 'working_units': units}


def validation_view(result):
    responses = {r['id']: r for r in result.get('gemini_responses', [])}
    positions = {(u['derived'].get('response_id'), u['derived'].get('response_position')): u['id']
                 for u in result['working_units']}
    findings = copy.deepcopy(result['review_findings'])
    for finding in findings:
        if 'unit_id' in finding:
            continue
        response_id = finding.get('response_id')
        raw = responses.get(response_id, {}).get('gemini', {})
        raw = raw if isinstance(raw, dict) else {}
        match = re.match(r'^\$\.(assessments|working_units)\[(\d+)\]', finding.get('path', ''))
        collection, position = (match[1], int(match[2])) if match else ('assessments', finding.get('row'))
        rows = raw.get(collection, [])
        if isinstance(position, int) and isinstance(rows, list) and 0 <= position < len(rows):
            row = rows[position]
            identifier = (row.get('unit_id') if collection == 'assessments' and isinstance(row, dict)
                          else positions.get((response_id, position)))
            if identifier:
                finding['unit_id'] = identifier
    return {'status': result['status'], 'findings': findings}


def write_outputs(output, result):
    """Write a readable result and lossless sidecars without replacing evidence."""
    review, audit, validation = output_paths(output)
    for path in (review, audit, validation):
        if path.exists():
            raise FileExistsError(path)
    payloads = ((audit, result), (validation, validation_view(result)), (review, review_view(result)))
    review.parent.mkdir(parents=True, exist_ok=True)
    for path, payload in payloads:
        with path.open('x', encoding='utf-8') as file:
            file.write(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    return audit


def load_artifact(path):
    """Accept legacy full artifacts or a readable file with its matching audit."""
    path = Path(path)
    result = json.loads(path.read_text(encoding='utf-8'))
    if set(result) != {'status', 'working_units'}:
        return result
    audit = output_paths(path)[1]
    if not audit.is_file():
        raise ValueError(f'Timing review requires its audit file: {audit}')
    full = json.loads(audit.read_text(encoding='utf-8'))
    if review_view(full) != result and review_view(full, compact=False) != result:
        raise ValueError(f'Timing review does not match its audit file: {audit}')
    return full
