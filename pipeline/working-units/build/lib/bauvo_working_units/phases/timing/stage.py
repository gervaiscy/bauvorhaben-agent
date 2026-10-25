"""Assess timing only, extending a saved Aufgaben-and-offers artifact."""
from __future__ import annotations

import argparse
import copy
import json
from dataclasses import replace
from pathlib import Path

from ...offer_stage import validate_input
from ...page_flow import extract_page_units
from ...project_flow import load_documents
from ...date_calculation import validate_anchors
from ...fact_artifact import load_facts, validate_facts

PRIOR_PASSES = ('PAGE_EXTRACTION', 'PAGE_OFFERS')


def extract_timing(baseline, documents, model, chunk_pages=3, overlap_pages=1, anchors=None, project_facts=None, context_documents=()):
    validate_input(baseline, documents, chunk_pages, overlap_pages, expected_passes=PRIOR_PASSES)
    anchors = validate_anchors(baseline.get('supported_anchors', []) if anchors is None else anchors, documents)
    facts = baseline['project_facts'] if project_facts is None else validate_facts(project_facts, [*documents, *context_documents])
    result = extract_page_units(documents, model, chunk_pages=chunk_pages, overlap_pages=overlap_pages,
                                supported_anchors=anchors, project_facts=facts, context_documents=context_documents,
                                stop_after='PAGE_TIMING', aufgaben_result=baseline)
    if not context_documents:
        result['context_snapshots'] = copy.deepcopy(baseline.get('context_snapshots', []))
    if project_facts is not None:
        # Keep historical fact evidence separate from findings about the replacement.
        if facts != baseline['project_facts']:
            result['previous_project_facts'] = copy.deepcopy(baseline['project_facts'])
            result['previous_fact_review_findings'] = [copy.deepcopy(f) for f in baseline['review_findings']
                                                      if 'fact_id' in f or f.get('pass') == 'PAGE_PROJECT_FACTS']
        result['review_findings'] = [f for f in result['review_findings']
                                    if 'fact_id' not in f and f.get('pass') != 'PAGE_PROJECT_FACTS']
        result['review_findings'].extend(copy.deepcopy(facts['review_findings']))
        result['status'] = ('request_failed' if any(f.get('code') == 'model_request_failed'
                            for f in result['review_findings']) else
                            'needs_review' if result['review_findings'] else 'recorded')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--working-units', required=True, help='Full cumulative Aufgaben-and-offers working-units.json')
    parser.add_argument('--pages', required=True)
    parser.add_argument('--documents', required=True)
    parser.add_argument('--output', required=True, help='New cumulative working-units.json')
    parser.add_argument('--project', required=True)
    parser.add_argument('--model', default='gemini-3.5-flash')
    parser.add_argument('--location', default='global')
    parser.add_argument('--chunk-pages', type=int, default=3)
    parser.add_argument('--overlap-pages', type=int, default=1)
    parser.add_argument('--project-facts', help='Saved contract facts; provide original sources as pages/context-pages for verification')
    parser.add_argument('--context-pages', action='append', default=[], help='Original pages for contract facts; repeat with matching --context-documents')
    parser.add_argument('--context-documents', action='append', default=[], help='Document boundaries for each context source')
    parser.add_argument('--date-anchors', help='Optional source-cited event dates; otherwise retain saved anchors')
    args = parser.parse_args()
    if len(args.context_pages) != len(args.context_documents):
        parser.error('Each --context-pages requires matching --context-documents')
    context_documents = [d for pages, boundaries in zip(args.context_pages, args.context_documents)
                         for d in load_documents(pages, boundaries)]
    output = Path(args.output)
    raw = output.with_name(output.stem + '.gemini-raw.json')
    compact = output.with_name(output.stem + '.timing-only.json')
    trace = output.with_name(output.stem + '.trace')
    if any(p.exists() for p in (output, raw, compact, trace, raw.with_suffix('.attempts.json'))):
        parser.error('Outputs already exist; use a new output directory')
    baseline = json.loads(Path(args.working_units).read_text())
    kinds = {(s['source_key'], s['document_id']): s['source_type'] for s in baseline['source_snapshots']}
    documents = [replace(d, source_type=kinds.get((d.source_key, d.id), 'project'))
                 for d in load_documents(args.pages, args.documents)]
    validate_input(baseline, documents, args.chunk_pages, args.overlap_pages, expected_passes=PRIOR_PASSES)
    anchors = validate_anchors(json.loads(Path(args.date_anchors).read_text()) if args.date_anchors
                               else baseline.get('supported_anchors', []), documents)
    facts = load_facts(args.project_facts, [*documents, *context_documents]) if args.project_facts else None
    from ...gemini import GeminiModel
    from ...trace import RawRunRecorder, TracingModel, RetryingModel
    model = RetryingModel(TracingModel(RawRunRecorder(
        GeminiModel(args.project, args.location, args.model), raw), trace))
    result = extract_timing(baseline, documents, model, args.chunk_pages, args.overlap_pages, anchors, facts, context_documents)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    compact.write_text(json.dumps({'assessments': [review['gemini'] for unit in result['working_units']
                      for review in unit['reviews'] if review['kind'] == 'PAGE_TIMING']},
                      ensure_ascii=False, indent=2) + '\n')
    print(f"Wrote timing assessments for {len(result['working_units'])} tasks: {output}")
    if result['status'] == 'request_failed':
        raise SystemExit('Unresolved timing requests; partial results and raw attempts preserved')


if __name__ == '__main__':
    main()
