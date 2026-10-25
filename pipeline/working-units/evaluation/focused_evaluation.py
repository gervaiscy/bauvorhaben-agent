"""Record focused model evaluations for manual review; never repair predictions."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from bauvo_working_units.gemini import GeminiModel
from bauvo_working_units.project_flow import Document, load_documents, make_page_chunks, extract_project_units
from bauvo_working_units.page_flow import _payload, _prompt, _view, unit_batches, assessment_findings, extract_page_units, select_assessment_chunk
from bauvo_working_units.schemas import PROTOCOL, GENERATION, PHASE_THINKING_LEVELS
from bauvo_working_units.citation_checks import citation_findings
from bauvo_working_units.trace import RawRunRecorder, TracingModel, RetryingModel, ModelRequestFailed
from prepare import prepare

BASE = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text())


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def source_jobs(pages, documents, baseline, spec, pass_name):
    docs = load_documents(pages, documents)
    records = read(baseline)['working_units']
    snapshots = {(d.source_key, d.id): d.source_snapshot for d in docs}
    if any(snapshots.get((u['derived']['source_key'], u['derived']['document_id'])) != u['derived']['source_snapshot'] for u in records):
        raise ValueError('Evaluation baseline does not match source snapshot')
    jobs = []
    windows = read(spec).get('windows') if pass_name == 'PAGE_APPLICABILITY' else read(spec)['cases']
    for i, case in enumerate(windows):
        numbers = case.get('pages', case.get('window_pages'))
        matches = [(d, c) for d in docs for c in make_page_chunks(d, chunk_pages=5, overlap_pages=1) if list(c.pages) == numbers]
        if len(matches) != 1:
            raise ValueError('Expected one document-local source window')
        doc, chunk = matches[0]
        units = [_view(u) for u in records if u['derived']['source_key'] == doc.source_key
                 and u['derived']['document_id'] == doc.id and u['derived']['primary_pages'] == list(chunk.primary_pages)]
        checks = case.get('checks', [case])
        if any('focus_suffix' in check and not any(u['id'].endswith(check['focus_suffix']) for u in units) for check in checks):
            raise ValueError('Evaluation focus unit is missing from baseline')
        name = case.get('id', f'blb-applicability-window-{i+1}')
        jobs.append((name, doc, chunk, units, case, case.get('mode') == 'EXTRACTION_AND_TIMING'))
    return jobs


def run(output, project, location='global', pages=None, documents=None,
        applicability_baseline=None, timing_baseline=None, model=None, case_ids=None):
    output = Path(output)
    if output.exists():
        raise ValueError('Evaluation output exists; use a new directory')
    if any((applicability_baseline, timing_baseline)) and not (pages and documents):
        raise ValueError('Source evaluations require pages and documents')
    selected = set(case_ids or [])
    available = {c['id'] for filename in ('cases.json', 'applicability-cases.json', 'timing-cases.json')
                 for c in read(BASE/filename)['cases']}
    if applicability_baseline:
        available |= {c['id'] for c in read(BASE/'blb-applicability-cases.json')['windows']}
    if timing_baseline:
        available |= {c['id'] for c in read(BASE/'blb-timing-cases.json')['cases']}
    if selected - available:
        raise ValueError('Unknown or unavailable evaluation cases: ' + ', '.join(sorted(selected - available)))
    backend = model or GeminiModel(project, location, 'gemini-3.5-flash')
    inputs = [*BASE.glob('*.json'), *BASE.glob('*.py'), *(BASE.parent/'src/bauvo_working_units').rglob('*.py')]
    inputs += list((BASE.parent/'src/bauvo_working_units/phases').rglob('prompt.txt'))
    inputs += [Path(p) for p in (pages, documents, applicability_baseline, timing_baseline) if p]
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    output.mkdir(parents=True)
    write(output/'run-manifest.json', {'protocol': PROTOCOL, 'model': 'gemini-3.5-flash', 'generation': GENERATION, 'phase_thinking_levels': PHASE_THINKING_LEVELS,
          'chunk_pages': 3, 'overlap_pages': 1, 'legacy_fixture_pages': 5, 'citation_validation': 'page_and_quote_checks', 'inputs_and_code': hashes})
    prepare(output/'actions')
    summary = []

    def recorder(folder):
        return RetryingModel(TracingModel(RawRunRecorder(backend, folder/'raw.json'), folder/'trace'))

    for case in read(BASE/'cases.json')['cases']:
        if selected and case['id'] not in selected:
            continue
        folder = output/'actions'/case['id']
        print('FOCUSED ACTION CASE', case['id'], flush=True)
        try:
            result = extract_project_units(load_documents(folder/'pages.json', folder/'documents.json'), recorder(folder))
            write(folder/'working-units.json', result)
            summary.append({'case': case['id'], 'status': result['status'], 'review_findings': result['review_findings']})
        except Exception as exc:
            summary.append({'case': case['id'], 'status': 'request_failed', 'error_type': type(exc).__name__})
        write(output/'execution-summary.json', summary)

    jobs = []
    for pass_name, filename in [('PAGE_APPLICABILITY', 'applicability-cases.json'), ('PAGE_TIMING', 'timing-cases.json')]:
        for case in read(BASE/filename)['cases']:
            source = case.get('pages', [{'pdf_page': 1, 'text': case.get('text', '')}])
            doc = Document('D1', 'Synthetic contract', 'synthetic.pdf', pages=tuple(source), schema_version=6, source_key=case['id'])
            chunks = make_page_chunks(doc, chunk_pages=5, overlap_pages=1)
            if len(chunks) != 1:
                raise ValueError('Pass-only fixture must fit one window')
            units = case.get('working_units', [{'id': 'U1', 'action': case.get('action'), 'actor': case.get('actor'),
                      'performed_by': case.get('performed_by'), 'citations': [{'pdf_page': 1, 'quote': case.get('text', '')}]}])
            jobs.append((pass_name, (case['id'], doc, chunks[0], units, case, False)))
    if applicability_baseline:
        jobs += [('PAGE_APPLICABILITY', job) for job in source_jobs(pages, documents, applicability_baseline, BASE/'blb-applicability-cases.json', 'PAGE_APPLICABILITY')]
    if timing_baseline:
        jobs += [('PAGE_TIMING', job) for job in source_jobs(pages, documents, timing_baseline, BASE/'blb-timing-cases.json', 'PAGE_TIMING')]

    for pass_name, (name, doc, chunk, units, expectations, extract_first) in jobs:
        if selected and name not in selected:
            continue
        folder = output/name
        folder.mkdir()
        payload = _payload(doc, chunk)
        write(folder/'inputs.json', {**payload, 'working_units': units})
        write(folder/'expectations.json', expectations)
        traced = recorder(folder)
        responses, findings = [], []
        print('FOCUSED CASE', name, pass_name, flush=True)
        try:
            if extract_first:
                response = traced.extract(_prompt('PAGE_EXTRACTION', {**payload, 'prior_context': '', 'known_items': []}, 'de'))
                responses.append({'pass': 'PAGE_EXTRACTION', 'gemini': copy.deepcopy(response)})
                findings += citation_findings(response, payload['source_pages'])
                if not isinstance(response, dict) or not isinstance(response.get('working_units'), list):
                    raise ValueError('Missing extracted units')
                units = [{**copy.deepcopy(u), 'id': f'E:{i:03d}'} for i, u in enumerate(response['working_units']) if isinstance(u, dict)]
            chunks = make_page_chunks(doc, chunk_pages=3, overlap_pages=1)
            grouped = {}
            for unit in units:
                chosen, reason = select_assessment_chunk(unit.get('citations'), chunk, chunks)
                grouped.setdefault(chosen.pages, (chosen, []))[1].append(unit)
                if reason:
                    findings.append({'unit_id': unit['id'], 'code': 'assessment_context_unresolved', 'reason': reason})
            for chosen, candidates in grouped.values():
                assessment_payload = _payload(doc, chosen)
                for batch in unit_batches(candidates):
                    try:
                        response = traced.extract(_prompt(pass_name, {**assessment_payload, 'working_units': batch}, 'de'))
                    except ModelRequestFailed as exc:
                        findings.append({'code': 'model_request_failed', 'pass': pass_name,
                                         'pages': list(chosen.pages), 'unit_ids': [u['id'] for u in batch], 'attempts': exc.attempts})
                        continue
                    responses.append({'pass': pass_name, 'pages': list(chosen.pages), 'gemini': copy.deepcopy(response)})
                    findings += assessment_findings(response, [u['id'] for u in batch], pass_name)
                    findings += citation_findings(response, assessment_payload['source_pages'])
            status = 'request_failed' if any(f['code'] == 'model_request_failed' for f in findings) else 'needs_review' if findings else 'recorded'
        except Exception as exc:
            status = 'request_failed'
            findings.append({'code': 'model_request_failed', 'error_type': type(exc).__name__,
                             **({'attempts': exc.attempts} if isinstance(exc, ModelRequestFailed) else {})})
        write(folder/'result.json', {'responses': responses, 'review_findings': findings,
              'status': status, 'citation_validation': 'page_and_quote_checks', 'semantic_review_required': True})
        summary.append({'case': name, 'status': status, 'review_findings': findings})
        write(output/'execution-summary.json', summary)
    if selected - {row['case'] for row in summary}:
        raise ValueError('Unknown or unavailable evaluation cases: ' + ', '.join(sorted(selected - {row['case'] for row in summary})))
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in hashes.items())
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('output', 'project'):
        parser.add_argument('--'+key, required=True)
    parser.add_argument('--location', default='global')
    for key in ('pages', 'documents', 'applicability-baseline', 'timing-baseline'):
        parser.add_argument('--'+key)
    parser.add_argument('--case', dest='case_ids', action='append', help='Run only the named case; repeat for several cases')
    run(**vars(parser.parse_args()))


if __name__ == '__main__':
    main()
