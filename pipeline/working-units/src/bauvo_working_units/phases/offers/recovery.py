"""Offline, explicitly audited source selections for a completed Offers run."""
import argparse
import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from ...citation_evidence import evidence_span
from ...core import digest
from ...project_flow import load_documents
from .context import PROTOCOL as CONTEXT_PROTOCOL, review_context
from .payload import LEGACY_PROTOCOL
from .response import resolve_response
from .stage import validate_input
from .validation import validate

RECOVERY_PROTOCOL = 'explicit-offer-source-selection-v1'
BINDING_CODES = {'invalid_citation', 'citation_page_outside_window', 'citation_quote_not_found'}


def recover_offers(baseline, documents, selections, chunk_pages=10, overlap_pages=1):
    """Bind explicit original spans; never fuzzy-match, change raw JSON or approve meaning."""
    if (baseline.get('completed_passes') != ['PAGE_EXTRACTION', 'PAGE_OFFERS']
            or baseline.get('offers_protocol') != LEGACY_PROTOCOL):
        raise ValueError('Explicit quotation recovery applies only to completed legacy quote-based Offers')
    if selections.get('input_sha256') != digest(baseline):
        raise ValueError('Source selections do not match this saved result')
    if not isinstance(selections.get('selections'), list):
        raise ValueError('Expected an explicit source-selection list')
    responses = {r['id']: r for r in baseline['gemini_responses'] if r['pass'] == 'PAGE_OFFERS'}
    # Reuse the source/window validation on the unchanged Aufgaben portion.
    prior = {**baseline, 'completed_passes': ['PAGE_EXTRACTION'], 'offers': [],
             'review_findings': [f for f in baseline['review_findings'] if f.get('response_id') not in responses]}
    validate_input(prior, documents, chunk_pages, overlap_pages)
    sources = {(d.source_key, d.id): d for d in documents}
    records = {o['id']: o for o in baseline['offers']}
    overrides = {}
    for selection in selections.get('selections', []):
        if not isinstance(selection, dict):
            raise ValueError('Invalid source selection')
        record = records.get(selection.get('offer_id'))
        if record is None or not isinstance(selection.get('reason'), str) or not selection['reason'].strip():
            raise ValueError('Each source selection needs a known offer and an inspection reason')
        meta = record['derived']; position = selection.get('citation_position')
        if type(position) is not int or position < 0:
            raise ValueError('Invalid citation position')
        raw = record['gemini']
        if not isinstance(raw, dict) or not isinstance(raw.get('citations'), list) or position >= len(raw['citations']):
            raise ValueError('Source selection does not identify an existing citation')
        original = raw['citations'][position]
        if not isinstance(original, dict):
            raise ValueError('Invalid original model citation')
        if selection.get('model_citation_sha256') != digest(original):
            raise ValueError('Source selection does not match the original model citation')
        response = responses[meta['response_id']]
        key = (response['source_key'], response['document_id'])
        document = sources[key]
        span = selection.get('source_span', {})
        if not isinstance(span, dict):
            raise ValueError('Invalid source span')
        page = next((p for p in document.pages if p['pdf_page'] == span.get('pdf_page')), None)
        if (page is None or span.get('pdf_page') != original.get('pdf_page')
                or span.get('pdf_page') not in response['primary_pages']):
            raise ValueError('Source selection is outside the original citation page/window')
        start, end = span.get('start'), span.get('end')
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(page['text']):
            raise ValueError('Invalid original source offsets')
        original_page = {**page, 'source_key': key[0], 'document_id': key[1]}
        if span != evidence_span(original_page, start, end):
            raise ValueError('Source selection differs from the original source text or provenance')
        row = meta['response_position']
        if raw != response['gemini']['offers'][row]:
            raise ValueError('Offer record differs from its preserved provider response')
        path = f'$.offers[{row}].citations[{position}]'
        target = (response['id'], path)
        if target in overrides:
            raise ValueError('Duplicate source selection')
        if not any(f.get('response_id') == response['id'] and f.get('path') == path
                   and f.get('code') == 'citation_quote_not_found' for f in baseline['review_findings']):
            raise ValueError('Explicit recovery targets only unresolved source quotations')
        overrides[target] = selection

    result = copy.deepcopy(baseline)
    active = [f for f in result['review_findings'] if not (
        f.get('response_id') in responses and f.get('code') in BINDING_CODES | {'offer_validation_failed'})]
    resolved = {}
    for response in result['gemini_responses']:
        if response['id'] not in responses or response.get('status') == 'request_failed':
            continue
        document = sources[(response['source_key'], response['document_id'])]
        pages = [{**p, 'source_key': response['source_key'], 'document_id': response['document_id']}
                 for p in document.pages if p['pdf_page'] in response['primary_pages']]
        value, audit, issues = resolve_response(response['gemini'], pages)
        for (rid, path), selection in overrides.items():
            if rid != response['id']:
                continue
            record = records[selection['offer_id']]
            position = selection['citation_position']; row = record['derived']['response_position']
            value['offers'][row]['citations'][position] = copy.deepcopy(selection['source_span'])
            audit['bindings'].append({'path': path, 'method': 'explicit_original_source_selection',
                'original_model_citation': copy.deepcopy(record['gemini']['citations'][position]),
                'reason': selection['reason'], **selection['source_span']})
            issues = [f for f in issues if f['path'] != path]
        audit['findings'] = copy.deepcopy(issues)
        if 'citation_evidence' in response:
            response.setdefault('citation_evidence_history', []).append(response['citation_evidence'])
        response['citation_evidence'] = audit
        resolved[response['id']] = value
        active.extend({'response_id': response['id'], **f} for f in issues)
    for record in result['offers']:
        meta = record['derived']; response = responses[meta['response_id']]
        value = resolved.get(meta['response_id'])
        if value is None:
            continue
        row = value['offers'][meta['response_position']]
        if isinstance(row, dict) and isinstance(row.get('citations'), list):
            meta['citations'] = copy.deepcopy(row['citations'])
            meta['review_context_protocol'] = CONTEXT_PROTOCOL
            meta['review_context'] = review_context(row['citations'], sources[(response['source_key'], response['document_id'])])
        meta.pop('duplicate_of', None)
    validate(SimpleNamespace(responses=result['gemini_responses'], offers=result['offers'], findings=active),
             protocol=LEGACY_PROTOCOL)
    result['review_findings'] = active
    result['offer_source_recovery'] = {'protocol': RECOVERY_PROTOCOL, 'input_sha256': digest(baseline),
        'selections': copy.deepcopy(selections['selections']),
        'original_review_findings': copy.deepcopy(baseline['review_findings']),
        'scope': 'source_binding_only_not_semantic_review_or_customer_approval', 'new_provider_calls': 0}
    result['status'] = ('request_failed' if any(f['code'] == 'model_request_failed' for f in active)
                        else 'incomplete' if any(f['code'] == 'offer_validation_failed' for f in active)
                        else 'needs_review' if active else 'recorded')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--working-units', required=True)
    parser.add_argument('--pages', required=True)
    parser.add_argument('--documents', required=True)
    parser.add_argument('--selections', required=True, help='Explicit source spans and input/citation fingerprints')
    parser.add_argument('--output', required=True)
    parser.add_argument('--chunk-pages', type=int, default=10)
    parser.add_argument('--overlap-pages', type=int, default=1)
    args = parser.parse_args()
    output = Path(args.output)
    raw = output.with_name(output.stem + '.gemini-raw.json')
    compact = output.with_name(output.stem + '.offers-only.json')
    if any(p.exists() for p in (output, raw, compact)):
        parser.error('Outputs already exist; use a new output directory')
    baseline = json.loads(Path(args.working_units).read_text())
    selections = json.loads(Path(args.selections).read_text())
    kinds = {(s['source_key'], s['document_id']): s['source_type'] for s in baseline['source_snapshots']}
    documents = [replace(d, source_type=kinds.get((d.source_key, d.id), 'project'))
                 for d in load_documents(args.pages, args.documents)]
    result = recover_offers(baseline, documents, selections, args.chunk_pages, args.overlap_pages)
    output.parent.mkdir(parents=True, exist_ok=True)
    for path, value in [(output, result), (raw, [r['gemini'] for r in result['gemini_responses'] if r['pass']=='PAGE_OFFERS']),
                        (compact, {'offers': [o['gemini'] for o in result['offers']]})]:
        with path.open('x') as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    print(f"Recovered {len(result['offers'])} offers; status={result['status']}; new provider calls=0; output={output}")
    if result['status'] in ('request_failed', 'incomplete'):
        raise SystemExit('Unresolved findings remain; downstream stages blocked')


if __name__ == '__main__':
    main()
