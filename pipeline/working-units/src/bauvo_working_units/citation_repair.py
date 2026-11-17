"""Create a separate citation audit for saved pipeline results; originals stay intact."""
import argparse
import copy
import hashlib
from dataclasses import replace
import json
from pathlib import Path

from .citation_evidence import resolve, LEGACY_CATALOG
from .core import digest
from .project_flow import load_documents
from .shared.source_documents import citation_pages, full_source_documents

CITATION_CODES = {'citation_quote_not_found', 'citation_page_outside_window',
                  'invalid_citation', 'invalid_citations', 'missing_citations',
                  'missing_citation_source', 'unknown_evidence_id', 'unknown_passage_id',
                  'unknown_fact_id', 'invalid_fact_evidence',
                  'invalid_task_quote', 'task_quote_not_found', 'task_quote_ambiguous'}


def repair_saved_result(baseline, documents, model=None):
    sources = full_source_documents(documents)
    pages = citation_pages(sources)
    document_hashes = {(d.source_key or d.source or d.id, d.id): digest(list(d.pages)) for d in documents}
    for snapshot in [*baseline.get('source_snapshots', []), *baseline.get('context_snapshots', [])]:
        if document_hashes.get((snapshot['source_key'], snapshot['document_id'])) != snapshot['document_sha256']:
            raise ValueError('Saved result does not match the supplied original source documents')
    result = copy.deepcopy(baseline)
    original_findings = copy.deepcopy(baseline['review_findings'])
    target_ids = {f.get('response_id') for f in original_findings if f['code'] in CITATION_CODES}
    active = [f for f in result['review_findings']
              if f.get('response_id') not in target_ids or f['code'] not in CITATION_CODES]
    resolved_responses = {}
    for response in result['gemini_responses']:
        if response['id'] not in target_ids or not isinstance(response.get('gemini'), dict):
            continue
        local = [p for p in pages if (p['source_key'], p['document_id']) ==
                 (response['source_key'], response['document_id'])]
        if response['pass'] == 'PAGE_TIMING' and response.get('timing_protocol') == 'timing-page-quotes-v1':
            from .phases.timing.response import resolve_response
            from .project_facts import facts_view, evidence_pages
            window = [p for p in local if p['pdf_page'] in response['pages']]
            resolved, audit, issues = resolve_response(response['gemini'], window,
                facts_view(result['project_facts']), evidence_pages(result['project_facts']))
        elif response['pass'] == 'PAGE_EXTRACTION' and response.get('quote_protocol') == 'task-original-quote-v1':
            from .phases.aufgaben.quotations import resolve_quotations
            from .citation_checks import citation_findings
            window = [p for p in local if p['pdf_page'] in response['pages']]
            resolved, audit, issues = resolve_quotations(response['gemini'], window)
            issues.extend(citation_findings(resolved, window, source_key=response['source_key'],
                                             document_id=response['document_id']))
            audit['findings'] = copy.deepcopy(issues)
        elif response['pass'] == 'PAGE_OFFERS' and response.get('reference_protocol') == 'offer-passage-labels-v1':
            from .phases.offers.passages import resolve_response
            primary = [p for p in local if p['pdf_page'] in response['primary_pages']]
            resolved, audit, issues = resolve_response(response['gemini'], primary,
                                                      saved_map=response['reference_map'])
        elif response['pass'] == 'PAGE_OFFERS' and response.get('quote_protocol') == 'offer-original-quote-v1':
            from .phases.offers.response import resolve_response
            primary = [p for p in local if p['pdf_page'] in response['primary_pages']]
            resolved, audit, issues = resolve_response(response['gemini'], primary)
        else:
            resolved, audit, issues = resolve(response['gemini'], local, pages,
                response['source_key'], response['document_id'], model=model, allow_exact_rebinding=True,
                catalog_protocol=baseline.get('catalog_protocol', LEGACY_CATALOG),
                reference_map=response.get('reference_map') if response['pass']=='PAGE_OFFERS' else None)
        if 'citation_evidence' in response:
            response.setdefault('citation_evidence_history', []).append(response['citation_evidence'])
        response['citation_evidence'] = audit
        resolved_responses[response['id']] = resolved
        active.extend({'response_id': response['id'], **issue} for issue in issues)
    responses_by_id = {r['id']: r for r in result['gemini_responses']}
    documents_by_id = {(d.source_key or d.source or d.id, d.id): d for d in documents}
    for collection, key in (('working_units', 'working_units'), ('offers', 'offers')):
        for record in result.get(collection, []):
            meta = record['derived']
            response = resolved_responses.get(meta['response_id'])
            if response is not None:
                row = response[key][meta['response_position']]
                if isinstance(row, dict) and 'citations' in row:
                    meta['citations'] = copy.deepcopy(row['citations'])
                    if collection == 'working_units' and meta.get('task_protocol') == 'task-original-quote-v1':
                        from .phases.aufgaben.quotations import derived_data
                        meta.pop('source_text', None)
                        meta.update(derived_data(record['gemini'], row))
            original_response = responses_by_id.get(meta['response_id'], {})
            if collection == 'offers' and (original_response.get('quote_protocol') == 'offer-original-quote-v1'
                    or original_response.get('reference_protocol') == 'offer-passage-labels-v1'):
                from .phases.offers.context import PROTOCOL as CONTEXT_PROTOCOL, review_context
                document = documents_by_id[(original_response['source_key'], original_response['document_id'])]
                meta['review_context_protocol'] = CONTEXT_PROTOCOL
                meta['review_context'] = review_context(meta.get('citations', []), document)
            for review in record.get('reviews', []):
                response = resolved_responses.get(review['response_id'])
                if response is not None:
                    review['derived']['resolved_evidence'] = copy.deepcopy(
                        response['assessments'][review['response_position']])
    result['review_findings'] = active
    result['citation_repair'] = {'protocol': 'evidence-references-v1', 'input_sha256': digest(baseline),
        'source_documents_sha256': digest(sources), 'original_review_findings': original_findings,
        'mode': 'bounded_model_repair' if model is not None else 'local_source_binding',
        'scope': 'evidence_binding_only_not_interpretation_approval'}
    result['status'] = ('request_failed' if any(f['code'] == 'model_request_failed' for f in active)
                        else 'incomplete' if any(f['code'] == 'offer_validation_failed' for f in active)
                        else 'needs_review' if active else 'recorded')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, help='Existing modular pipeline run directory')
    parser.add_argument('--output', required=True, help='New audit directory, never an existing run')
    parser.add_argument('--repair-with-model', action='store_true', help='Enable one repair request per failed response')
    parser.add_argument('--project')
    parser.add_argument('--location', default='global')
    parser.add_argument('--model', default='gemini-3.5-flash')
    args = parser.parse_args()
    if args.repair_with_model and not args.project:
        parser.error('--repair-with-model requires --project')
    root = Path(args.run).resolve()
    output = Path(args.output).resolve()
    if output.exists() or output.is_relative_to(root):
        parser.error('Use a new output directory outside the original run')
    manifest = json.loads((root/'pipeline.json').read_text())
    for step in manifest['steps'].values():
        if step.get('artifact') and hashlib.sha256((root/step['artifact']).read_bytes()).hexdigest() != step['sha256']:
            raise ValueError('Original run artifact checksum does not match its manifest')
    sources = manifest['binding']['sources']
    documents = [replace(d, source_type=source['type']) for source in sources
                 for d in load_documents(root/manifest['steps']['01-ocr/'+source['id']]['artifact'],
                                         root/manifest['steps']['02-documents/'+source['id']]['artifact'])]
    inputs = {name: json.loads((root/path).read_text()) for name, path in manifest['outputs'].items()}
    # Validate every input/source binding before creating files or making requests.
    for baseline in inputs.values():
        repair_saved_result(baseline, documents)
    output.mkdir(parents=True, exist_ok=False)
    model = None
    if args.repair_with_model:
        from .gemini import GeminiModel
        from .trace import RetryingModel, TracingModel, RawRunRecorder
        model = RetryingModel(TracingModel(RawRunRecorder(
            GeminiModel(args.project,args.location,args.model),output/'raw.json'),output/'trace'))
    summary = {}
    for name, baseline in inputs.items():
        result = repair_saved_result(baseline, documents, model)
        (output/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        summary[name] = {'input': str(root/manifest['outputs'][name]), 'input_sha256': digest(baseline),
                         'original_findings': len(baseline['review_findings']),
                         'remaining_findings': len(result['review_findings']), 'status': result['status']}
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    if any(row['status'] == 'request_failed' for row in summary.values()):
        raise SystemExit('Unresolved citation-repair requests; audit and raw attempts preserved')


if __name__ == '__main__':
    main()
