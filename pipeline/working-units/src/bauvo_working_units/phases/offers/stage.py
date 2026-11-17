"""Extract offers from original pages while preserving a saved Aufgaben result."""
from __future__ import annotations

import argparse
import copy
import json
from dataclasses import replace
from pathlib import Path

from ...core import digest
from ...page_flow import extract_page_units
from ...project_flow import load_documents, make_page_chunks
from ...schemas import PROTOCOL
from .payload import PROTOCOL as OFFERS_PROTOCOL, LEGACY_PROTOCOL


def validate_input(baseline, documents, chunk_pages=10, overlap_pages=1, expected_passes=('PAGE_EXTRACTION',)):
    if baseline.get('schema_version') != 18 or baseline.get('protocol') != PROTOCOL:
        raise ValueError('Expected a current schema-18 working-units.json')
    if baseline.get('completed_passes') != list(expected_passes):
        raise ValueError('Expected completed passes: ' + ', '.join(expected_passes))
    if any(f.get('code') == 'offer_validation_failed' for f in baseline.get('review_findings', [])):
        raise ValueError('Resolve the preserved offer validation findings before continuing')
    if 'PAGE_OFFERS' in expected_passes and baseline.get('offers_protocol') not in (OFFERS_PROTOCOL, LEGACY_PROTOCOL):
        raise ValueError('Saved offers use an older extraction contract; rerun Offers from accepted Aufgaben')
    if expected_passes == ('PAGE_EXTRACTION',) and baseline.get('offers'):
        raise ValueError('Expected an Aufgaben-only result without existing offers')
    if any(f.get('code') == 'model_request_failed' and f.get('pass') in expected_passes
           for f in baseline.get('review_findings', [])):
        raise ValueError('Complete the failed earlier windows before continuing')
    expected = [{'source_key': d.source_key, 'document_id': d.id, 'source_type': d.source_type,
                 'pages': [p['pdf_page'] for p in d.pages], 'pages_sha256': d.source_snapshot,
                 'document_sha256': digest(list(d.pages))} for d in documents]
    if baseline.get('source_snapshots') != expected:
        raise ValueError('Saved Aufgaben does not match these source pages/document boundaries')
    windows = [{'source_key': d.source_key, 'document_id': d.id, 'pages': list(c.pages),
                'primary_pages': list(c.primary_pages)} for d in documents
               for c in make_page_chunks(d, chunk_pages=chunk_pages, overlap_pages=overlap_pages)]
    if baseline.get('windows') != windows:
        raise ValueError('Keep the saved Aufgaben window size and overlap')


def extract_offers(baseline, documents, model, chunk_pages=10, overlap_pages=1,
                   context_documents=()):
    validate_input(baseline, documents, chunk_pages, overlap_pages)
    result = extract_page_units(documents, model, chunk_pages=chunk_pages,
                                overlap_pages=overlap_pages,
                                supported_anchors=baseline.get('supported_anchors', []),
                                project_facts=baseline['project_facts'],
                                context_documents=context_documents,
                                stop_after='PAGE_OFFERS', aufgaben_result=baseline)
    if not context_documents:
        result['context_snapshots'] = copy.deepcopy(baseline.get('context_snapshots', []))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--working-units', required=True, help='Saved full Aufgaben-only working-units.json')
    parser.add_argument('--pages', required=True)
    parser.add_argument('--documents', required=True)
    parser.add_argument('--context-pages', action='append', default=[])
    parser.add_argument('--context-documents', action='append', default=[])
    parser.add_argument('--context-type', action='append', choices=('project', 'blb'), default=[])
    parser.add_argument('--output', required=True, help='New cumulative working-units.json path')
    parser.add_argument('--project', required=True)
    parser.add_argument('--model', default='gemini-3.5-flash')
    parser.add_argument('--location', default='global')
    parser.add_argument('--chunk-pages', type=int, default=10)
    parser.add_argument('--overlap-pages', type=int, default=1)
    from ...call_budget import add_call_arguments, cached_backend
    add_call_arguments(parser)
    args = parser.parse_args()
    if len(args.context_pages) != len(args.context_documents):
        parser.error('Each --context-pages requires matching --context-documents')
    if args.context_type and len(args.context_type) != len(args.context_pages):
        parser.error('Provide one --context-type per context source')
    output = Path(args.output)
    raw = output.with_name(output.stem + '.gemini-raw.json')
    compact = output.with_name(output.stem + '.offers-only.json')
    trace = output.with_name(output.stem + '.trace')
    attempts = raw.with_suffix('.attempts.json')
    if any(p.exists() for p in (output, raw, compact, trace, attempts)):
        parser.error('Outputs already exist; use a new output directory')
    baseline = json.loads(Path(args.working_units).read_text())
    kinds = {(s['source_key'], s['document_id']): s['source_type'] for s in baseline['source_snapshots']}
    documents = [replace(d, source_type=kinds.get((d.source_key, d.id), 'project'))
                 for d in load_documents(args.pages, args.documents)]
    context_kinds = {(s['source_key'], s['document_id']): s['source_type']
                     for s in baseline.get('context_snapshots', [])}
    context = [replace(d, source_type=(args.context_type[i] if args.context_type else
                       context_kinds.get((d.source_key, d.id), 'project')))
               for i, (pages, boundaries) in enumerate(zip(args.context_pages, args.context_documents))
               for d in load_documents(pages, boundaries)]
    validate_input(baseline, documents, args.chunk_pages, args.overlap_pages)
    from ...gemini import GeminiModel
    from ...trace import RawRunRecorder, TracingModel, RetryingModel
    model = RetryingModel(TracingModel(RawRunRecorder(
        cached_backend(args, output, lambda: GeminiModel(args.project, args.location, args.model)), raw), trace))
    result = extract_offers(baseline, documents, model, args.chunk_pages, args.overlap_pages, context)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    compact.write_text(json.dumps({'offers': [o['gemini'] for o in result['offers']]},
                                  ensure_ascii=False, indent=2) + '\n')
    print(f"Wrote {len(result['offers'])} offers; preserved {len(result['working_units'])} tasks: {output}")
    if result['status'] == 'request_failed':
        raise SystemExit('Unresolved offer requests; partial results and raw attempts preserved')
    if result['status'] == 'incomplete':
        raise SystemExit('Invalid offer proposals; raw answers and validation findings preserved; downstream stages blocked')


if __name__ == '__main__':
    main()
