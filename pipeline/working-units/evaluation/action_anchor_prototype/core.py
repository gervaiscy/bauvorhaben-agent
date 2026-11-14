"""Isolated source-action experiment; no production orchestration imports this module."""
import copy
import hashlib
import html
import json
from pathlib import Path
import re


PROTOCOL = 'action-anchor-prototype-v1'
PROMPT_FILE = Path(__file__).with_name('instructions.de.txt')
SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['actions'], 'properties': {
    'actions': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'required': ['source_segment', 'action_quote', 'actor'], 'properties': {
            'source_segment': {'type': 'string', 'minLength': 1},
            'action_quote': {'type': 'string', 'minLength': 1},
            'actor': {'anyOf': [{'type': 'string', 'minLength': 1}, {'type': 'null'}]}}}}}}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def normalized(value):
    return ' '.join(value.split())


def prompt_for(source):
    # No expectations, previous answers or evaluation annotations enter the request.
    fields = ('source_key', 'document', 'known_items', 'prior_context', 'source_pages', 'project_facts')
    wire = {k: copy.deepcopy(source[k]) for k in fields if k in source}
    return PROTOCOL + '.\n' + PROMPT_FILE.read_text().rstrip() + '\n\nSOURCE DATA:\n' + json.dumps(wire, ensure_ascii=False)


def segments(source):
    result = {}
    for page in source['source_pages']:
        offset = 0
        for label, text in page['segments']:
            if label is not None:
                if label in result:
                    raise ValueError('Duplicate source label')
                result[label] = {'source_key': page.get('source_key', source.get('source_key')),
                    'document_id': page.get('document_id', source['document']['id']),
                    'pdf_page': page['pdf_page'], 'primary': page.get('primary', True),
                    'start': offset, 'text': text}
            offset += len(text)
    return result


def occurrences(quote, text):
    """Exact content only; wrapped whitespace may differ. Never use fuzzy repair."""
    pattern = r'\s+'.join(re.escape(word) for word in quote.split())
    return list(re.finditer('(?=(' + pattern + '))', text)) if pattern else []


def bind(source, response):
    """Preserve every raw row; derive an anchor only for a unique source match."""
    lookup = segments(source)
    tasks, findings = [], []
    if not isinstance(response, dict) or set(response) != {'actions'} or not isinstance(response['actions'], list):
        return [], [{'code': 'invalid_response_shape'}]
    for n, raw in enumerate(response['actions']):
        task = {'id': f'A{n+1}', 'raw': copy.deepcopy(raw), 'derived': None}
        tasks.append(task)
        valid = (isinstance(raw, dict) and set(raw) == {'source_segment', 'action_quote', 'actor'}
                 and all(isinstance(raw[k], str) and raw[k].strip() for k in ('source_segment', 'action_quote'))
                 and (raw['actor'] is None or isinstance(raw['actor'], str) and bool(raw['actor'].strip())))
        if not valid:
            findings.append({'row': n, 'code': 'invalid_action_shape'})
            continue
        entry = lookup.get(raw['source_segment'])
        if entry is None:
            findings.append({'row': n, 'code': 'unknown_segment'})
            continue
        if not entry['primary']:
            findings.append({'row': n, 'code': 'action_on_overlap_only_page'})
            continue
        matches = occurrences(raw['action_quote'], entry['text'])
        if len(matches) != 1:
            findings.append({'row': n, 'code': 'missing_quote' if not matches else 'ambiguous_quote'})
            continue
        start, end = matches[0].span(1)
        task['derived'] = {**{k: entry[k] for k in ('source_key', 'document_id', 'pdf_page')},
            'source_segment': raw['source_segment'], 'actor': raw['actor'],
            'action_quote': entry['text'][start:end], 'paragraph_text': entry['text'],
            'paragraph_start': entry['start'], 'start': entry['start'] + start, 'end': entry['start'] + end,
            'quote_binding': 'exact' if entry['text'][start:end] == raw['action_quote'] else 'whitespace_equivalent',
            'context_ref': 'input.json', 'context_role': 'available_source_context_not_selected_proof'}
    return tasks, findings


def anchor_matches(derived, anchor):
    return (derived['source_segment'] == anchor['source_segment']
            and any(normalized(needle) in normalized(derived['action_quote']) for needle in anchor['any_of']))


def validate_expectations(source, expectations):
    """Freeze source-local authored controls before any provider call."""
    lookup = segments(source)
    ids = [e['id'] for e in expectations['expected_actions']]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate expected action ID')
    for item in [*expectations['expected_actions'], *expectations.get('forbidden_actions', [])]:
        if not item['anchors']:
            raise ValueError('Expectation needs a source anchor')
        for anchor in item['anchors']:
            if anchor['source_segment'] not in lookup or not anchor['any_of']:
                raise ValueError('Expectation uses an unavailable source segment')
            if any(normalized(q) not in normalized(lookup[anchor['source_segment']]['text']) for q in anchor['any_of']):
                raise ValueError('Expectation quote is absent from its source segment')
    for condition in expectations.get('context_checks', []):
        if any(label not in lookup for label in condition['segments']):
            raise ValueError('Context check uses an unavailable source segment')


def assess(source, response, expectations):
    """Joint literal controls; unmatched candidates require review, never automatic deletion.

    These are authored evaluation rules, not a production correctness verifier.
    Whole-paragraph context cannot count as matching an expected action.
    """
    validate_expectations(source, expectations)
    tasks, findings = bind(source, response)
    expected = expectations['expected_actions']
    matches = {e['id']: [] for e in expected}
    grouped, unexpected, forbidden, actors = [], [], [], []
    for task in tasks:
        derived = task['derived']
        if derived is None:
            continue
        hits = [e for e in expected if any(anchor_matches(derived, a) for a in e['anchors'])]
        for e in hits:
            matches[e['id']].append(task['id'])
            if normalized(derived['actor'] or '').casefold() not in {
                    normalized(alias or '').casefold() for alias in e['actors']}:
                actors.append({'task': task['id'], 'expected_action': e['id'], 'actual': derived['actor'],
                               'allowed': e['actors']})
        if len(hits) > 1:
            grouped.append({'task': task['id'], 'expected_actions': [e['id'] for e in hits]})
        forbidden_hits = [f['id'] for f in expectations.get('forbidden_actions', [])
                          if not hits and any(anchor_matches(derived, a) for a in f['anchors'])
                          and ('actors' not in f or normalized(derived['actor'] or '').casefold()
                               in {normalized(a or '').casefold() for a in f['actors']})]
        if forbidden_hits:
            forbidden.append({'task': task['id'], 'controls': forbidden_hits})
        if not hits and not forbidden_hits:
            unexpected.append(task['id'])
    missing = [key for key, ids in matches.items() if not ids]
    duplicate = [{'expected_action': k, 'tasks': ids} for k, ids in matches.items() if len(ids) > 1]
    context = [{'id': c['id'], 'available_segments': c['segments'],
                'matched_actions': {key: matches.get(key, []) for key in c['for_actions']},
                'note': 'Context availability is not proof of semantic use or applicability.'}
               for c in expectations.get('context_checks', [])]
    passed = not any((findings, missing, grouped, duplicate, unexpected, forbidden, actors))
    return {'protocol': PROTOCOL, 'status': 'passes_authored_controls' if passed else 'needs_review',
            'semantic_review_required': True, 'expectation_scope': expectations.get('scope', 'authored_inventory'),
            'tasks': tasks, 'binding_findings': findings, 'missing_actions': missing,
            'grouped_actions': grouped, 'duplicate_actions': duplicate,
            'unexpected_actions_requiring_review': unexpected, 'forbidden_action_matches': forbidden,
            'actor_findings': actors, 'matches': matches, 'context_checks': context,
            'raw_response': copy.deepcopy(response)}


def render_review(source, report):
    esc = lambda value: html.escape(str(value))
    parts = ['<!doctype html><html lang="de"><meta charset="utf-8"><title>Action anchor prototype</title>',
             '<style>body{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:1rem}'
             'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f4f6;padding:1rem}'
             'mark{background:#ffed91}article{border-top:1px solid #ccc;padding:1rem 0}</style>',
             '<h1>Prototype: original actions and retained context</h1>',
             '<p>Authored controls are not semantic approval. Context is retained source material, not selected proof.</p>',
             '<pre>' + esc(json.dumps({k: v for k, v in report.items() if k not in ('tasks', 'raw_response')},
                                     ensure_ascii=False, indent=2)) + '</pre>']
    for task in report['tasks']:
        d = task['derived']
        parts.append('<article><h2>' + esc(task['id']) + '</h2>')
        if d:
            start = d['start'] - d['paragraph_start']; end = d['end'] - d['paragraph_start']
            text = d['paragraph_text']
            highlighted = esc(text[:start]) + '<mark>' + esc(text[start:end]) + '</mark>' + esc(text[end:])
            parts.append('<p>actor: '+esc(d['actor'])+' · PDF page '+str(d['pdf_page'])+'</p><pre>'+highlighted+'</pre>')
        else:
            parts.append('<pre>' + esc(json.dumps(task['raw'], ensure_ascii=False)) + '</pre>')
        parts.append('</article>')
    parts.append('<details><summary>Complete source window</summary>')
    for page in source['source_pages']:
        parts.append('<h3>PDF page '+str(page['pdf_page'])+'</h3><pre>' + esc(''.join(t for _, t in page['segments'])) + '</pre>')
    parts.append('</details><details><summary>Contractual context</summary><pre>' +
                 esc(json.dumps(source.get('project_facts', {}), ensure_ascii=False, indent=2)) + '</pre></details></html>')
    return '\n'.join(parts)
