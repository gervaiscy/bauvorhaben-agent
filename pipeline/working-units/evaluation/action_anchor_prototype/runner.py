"""Prepare, budget, run and replay an isolated action-anchor comparison.

No production stage, cache protocol, prompt, schema or previous run is modified.
Default call limit is zero. Each selected request gets at most one provider attempt.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parents[1] / 'src'))
sys.path.insert(0, str(BASE.parent))

from action_anchor_prototype.core import PROTOCOL, SCHEMA, PROMPT_FILE, assess, digest, prompt_for, render_review, validate_expectations
from bauvo_working_units.call_budget import CallBudget, CallLimitExceeded
from bauvo_working_units.gemini import IncompleteResponse
from bauvo_working_units.phases.aufgaben.payload import legacy_input as model_input
from bauvo_working_units.schemas import generation_for


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fixture_jobs():
    cases = {c['id']: c for c in read(BASE.parent / 'cases.json')['cases']}
    for control in read(BASE / 'controls.json')['cases']:
        case = cases[control['case_id']]
        for doc in case['documents']:
            source = {'source_key': case['id'], 'document': {'id': doc['document_id'], 'title': doc['document_title']},
                      'source_pages': [{**p, 'primary': True} for p in case['pages'] if p['pdf_page'] in doc['pdf_pages']],
                      'known_items': [], 'prior_context': '', 'project_facts': {'facts': []}}
            # Other documents supply their own original text as context, never oracle interpretations.
            for other in case['documents']:
                if other['document_id'] == doc['document_id']:
                    continue
                for page in case['pages']:
                    if page['pdf_page'] in other['pdf_pages']:
                        source['project_facts']['facts'].append({'id': f'context-{other["document_id"]}-{page["pdf_page"]}',
                            'citations': [{'source_key': case['id'], 'document_id': other['document_id'],
                                           'pdf_page': page['pdf_page'], 'quote': page['text']}]})
            wire, _ = model_input(source)
            yield {'id': case['id'] + '--' + doc['document_id'], 'kind': 'fictional_held_out',
                   'source': wire, 'expectations': control['documents'][doc['document_id']]}


def prepare(output, saved_run=None, saved_expectations=None):
    jobs = list(fixture_jobs())
    inputs = [BASE / 'controls.json', BASE.parent / 'cases.json', PROMPT_FILE]
    if saved_run is not None:
        if saved_expectations is None:
            raise ValueError('Saved-source comparison requires separately authored expectations')
        saved_run = Path(saved_run)
        result_path = saved_run / 'result.json'
        result = read(result_path)
        spec = read(saved_expectations)
        inputs.extend([result_path, Path(saved_expectations)])
        for item in spec['cases']:
            number = item['response_number']
            trace = saved_run / f'trace/{number:04d}-prompt.txt'
            source = json.loads(trace.read_text().split('SOURCE DATA:\n', 1)[1])
            old_response = result['gemini_responses'][number - 1]
            if old_response['document_id'] != source['document']['id']:
                raise ValueError('Saved trace does not match result response')
            inputs.append(trace)
            jobs.append({'id': item['id'], 'kind': 'saved_source', 'source': source,
                         'expectations': item['expectations'],
                         'baseline': {'result_file': str(result_path.resolve()), 'response_number': number,
                                      'raw_response': old_response['gemini'],
                                      'review_notes': item.get('baseline_review_notes', []),
                                      'automatic_anchor_score': 'unavailable_in_original_representation'}})
    for job in jobs:
        validate_expectations(job['source'], job['expectations'])
    if len({j['id'] for j in jobs}) != len(jobs):
        raise ValueError('Duplicate comparison case ID')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    files, rows = {}, []
    for job in jobs:
        folder = output / job['id']; folder.mkdir()
        write(folder / 'input.json', job['source'])
        write(folder / 'expectations.json', job['expectations'])
        (folder / 'prompt.txt').write_text(prompt_for(job['source']))
        if 'baseline' in job:
            write(folder / 'baseline.json', job['baseline'])
        for path in folder.iterdir():
            files[str(path.relative_to(output))] = sha(path)
        rows.append({'id': job['id'], 'kind': job['kind']})
    manifest = {'protocol': PROTOCOL, 'cases': rows, 'frozen_files': files,
                'source_fingerprints': {str(p.resolve()): sha(p) for p in inputs},
                'expected_values_sent_to_provider': False,
                'automatic_score_limit': 'Authored literal anchors and role aliases; semantic review remains required.'}
    write(output / 'suite.json', manifest)
    return manifest


class PrototypeModel:
    """A separate provider adapter with its own schema; production registries stay untouched."""
    def __init__(self, project, location, model):
        from google import genai
        from google.genai import types
        self.client = genai.Client(vertexai=True, project=project, location=location,
            http_options=types.HttpOptions(api_version='v1', timeout=180_000,
                                          retry_options=types.HttpRetryOptions(attempts=1)))
        self.types, self.model = types, model
        self.last_raw_response = self.last_finish_reason = self.last_diagnostics = None

    def extract(self, prompt):
        self.last_raw_response = self.last_finish_reason = self.last_diagnostics = None
        config = self.types.GenerateContentConfig(**generation_for('PAGE_EXTRACTION'), response_json_schema=SCHEMA)
        response = self.client.models.generate_content(model=self.model, contents=prompt, config=config)
        self.last_raw_response = response.text
        candidates = getattr(response, 'candidates', None)
        reason = getattr(candidates[0], 'finish_reason', None) if candidates else None
        self.last_finish_reason = str(reason).split('.')[-1] if reason is not None else None
        usage = getattr(response, 'usage_metadata', None)
        self.last_diagnostics = {'model_version': getattr(response, 'model_version', None),
                                'response_id': getattr(response, 'response_id', None),
                                'usage_metadata': usage.model_dump(mode='json') if hasattr(usage, 'model_dump') else None}
        if self.last_finish_reason != 'STOP' or not response.text:
            raise IncompleteResponse('unfinished_or_empty_prototype_response', self.last_finish_reason)
        try:
            return json.loads(response.text)
        except ValueError as exc:
            raise IncompleteResponse('invalid_prototype_json', self.last_finish_reason) from exc


def store_once(path, value):
    """Atomic immutable publication; concurrent/different answers cannot replace evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.prototype-')
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush(); os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if read(path) != value:
                raise ValueError('Different completed response already exists; original preserved')
    finally:
        os.unlink(temporary)


def load_record(path, request):
    if not path.exists():
        return None
    record = read(path)
    payload = {k: record[k] for k in ('response', 'raw_text', 'finish_reason', 'diagnostics')}
    if (record['request'] != request or record['request_sha256'] != digest(request)
            or record['response_sha256'] != digest(payload) or record['finish_reason'] != 'STOP'
            or json.loads(record['raw_text']) != record['response']):
        raise ValueError('Prototype response store integrity failure')
    return record


def run(suite, output, project=None, location='global', model='gemini-3.5-flash',
        max_new_calls=0, cache_dir=None, provider_factory=None, plan_only=False):
    suite, output = Path(suite), Path(output)
    manifest = read(suite / 'suite.json')
    if manifest['protocol'] != PROTOCOL:
        raise ValueError('Unknown prototype protocol')
    if any(sha(suite / path) != checksum for path, checksum in manifest['frozen_files'].items()):
        raise ValueError('Prepared inputs or expectations changed; prepare a new suite')
    cache = Path(cache_dir) if cache_dir else suite / 'provider-responses'
    budget = CallBudget(max_new_calls)
    jobs = []
    for case in manifest['cases']:
        folder = suite / case['id']
        request = {'protocol': PROTOCOL, 'project': project, 'location': location, 'model': model,
                   'generation': generation_for('PAGE_EXTRACTION'), 'response_schema': SCHEMA,
                   'prompt': (folder / 'prompt.txt').read_text()}
        path = cache / (digest(request) + '.json')
        jobs.append({**case, 'folder': folder, 'request': request, 'path': path,
                     'record': load_record(path, request)})
    planned = sum(j['record'] is None for j in jobs)
    output.mkdir(parents=True, exist_ok=False)
    plan = {'requests': [{'case': j['id'], 'request_sha256': digest(j['request']),
                          'source': 'saved' if j['record'] else 'new', 'response_file': str(j['path'])} for j in jobs],
            'new_calls': planned, 'saved': len(jobs) - planned, 'limit': max_new_calls,
            'retry_policy': 'No retries; one attempt per new request, including failures.'}
    write(output / 'request-plan.json', plan)
    print(f'Prototype plan: {len(jobs)} requests; {planned} new; limit={max_new_calls}', flush=True)
    if plan_only:
        return {'status': 'plan_only', **plan}
    budget.check(planned)
    if planned and provider_factory is None and not project:
        raise ValueError('A project is required for new provider calls')
    backend, summary, provider_failed = None, [], False
    write(output / 'run-manifest.json', {'suite': str(suite.resolve()), 'suite_sha256': sha(suite/'suite.json'),
          'protocol': PROTOCOL, 'code_fingerprints': {p.name: sha(p) for p in BASE.glob('*.py')}, 'plan': plan})
    for job in jobs:
        if provider_failed and job['record'] is None:
            summary.append({'case': job['id'], 'kind': job['kind'], 'status': 'not_attempted',
                            'reason': 'New calls stopped after a provider failure; no automatic retry.'})
            write(output/'summary.json', {'protocol': PROTOCOL, 'calls_used': budget.used, 'cases': summary,
                  'semantic_review_required': True, 'production_pipeline_changed': False})
            continue
        dest = output / job['id']; dest.mkdir()
        source, expectations = read(job['folder']/'input.json'), read(job['folder']/'expectations.json')
        write(dest/'input.json', source); write(dest/'expectations.json', expectations)
        (dest/'prompt.txt').write_text(job['request']['prompt'])
        print('Prototype case:', job['id'], flush=True)
        record, error = job['record'], None
        if record is None:
            budget.consume()
            try:
                if backend is None:
                    backend = provider_factory() if provider_factory else PrototypeModel(project, location, model)
                response = backend.extract(job['request']['prompt'])
                raw = getattr(backend, 'last_raw_response', None) or json.dumps(response, ensure_ascii=False)
                finish = getattr(backend, 'last_finish_reason', None) or 'STOP'
                if finish != 'STOP' or json.loads(raw) != response:
                    raise ValueError('Completed response metadata does not match its original JSON')
                payload = {'response': response, 'raw_text': raw, 'finish_reason': finish,
                           'diagnostics': copy.deepcopy(getattr(backend, 'last_diagnostics', None))}
                record = {'request': job['request'], 'request_sha256': digest(job['request']),
                          **payload, 'response_sha256': digest(payload)}
                # Store completion BEFORE local binding or expectation checks.
                store_once(job['path'], record)
            except Exception as exc:
                error = {'error_type': type(exc).__name__, 'finish_reason': getattr(backend, 'last_finish_reason', None)}
                provider_failed = True
                (dest/'raw-response.txt').write_text(getattr(backend, 'last_raw_response', None) or '')
                write(dest/'failure.json', error)
        if record is not None:
            (dest/'raw-response.json').write_text(record['raw_text'])
            write(dest/'provider-record.json', record)
            report = assess(source, record['response'], expectations)
            write(dest/'assessment.json', report)
            (dest/'review.html').write_text(render_review(source, report))
            row = {'case': job['id'], 'kind': job['kind'], 'status': report['status'],
                   'counts': {key: len(report[key]) for key in ('binding_findings', 'missing_actions', 'grouped_actions',
                        'duplicate_actions', 'unexpected_actions_requiring_review', 'forbidden_action_matches', 'actor_findings')}}
        else:
            row = {'case': job['id'], 'kind': job['kind'], 'status': 'request_failed', 'error': error}
        summary.append(row)
        write(output/'summary.json', {'protocol': PROTOCOL, 'calls_used': budget.used, 'cases': summary,
              'semantic_review_required': True, 'production_pipeline_changed': False})
    return read(output/'summary.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare'); prep.add_argument('--output', required=True)
    prep.add_argument('--saved-run'); prep.add_argument('--saved-expectations')
    execute = commands.add_parser('run'); execute.add_argument('--suite', required=True)
    execute.add_argument('--output', required=True); execute.add_argument('--project')
    execute.add_argument('--location', default='global'); execute.add_argument('--model', default='gemini-3.5-flash')
    execute.add_argument('--max-new-calls', type=int, default=0); execute.add_argument('--cache-dir')
    execute.add_argument('--plan-only', action='store_true')
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.output, args.saved_run, args.saved_expectations)
        print(f'Prepared {len(result["cases"])} prototype requests; no model calls made')
    else:
        try:
            run(args.suite, args.output, args.project, args.location, args.model,
                args.max_new_calls, args.cache_dir, plan_only=args.plan_only)
        except CallLimitExceeded as exc:
            parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
