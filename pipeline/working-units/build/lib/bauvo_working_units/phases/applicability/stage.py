"""Assess applicability only, preserving a saved Aufgaben, offers and timing result."""
import argparse
import copy
import json
from dataclasses import replace
from pathlib import Path

from ...fact_artifact import validate_facts
from ...offer_stage import validate_input
from ...page_flow import extract_page_units
from ...project_flow import load_documents

PRIOR_PASSES = ('PAGE_EXTRACTION', 'PAGE_OFFERS', 'PAGE_TIMING')


def prior_passes(baseline):
    completed = baseline.get('completed_passes')
    with_consequences = (*PRIOR_PASSES, 'PAGE_CONSEQUENCES')
    return with_consequences if completed == list(with_consequences) else PRIOR_PASSES


def extract_applicability(baseline, documents, model, chunk_pages=3, overlap_pages=1,
                          context_documents=()):
    validate_input(baseline, documents, chunk_pages, overlap_pages, expected_passes=prior_passes(baseline))
    facts = validate_facts(baseline['project_facts'], [*documents, *context_documents])
    result = extract_page_units(documents, model, chunk_pages=chunk_pages, overlap_pages=overlap_pages,
                               supported_anchors=baseline.get('supported_anchors', []),
                               context_documents=context_documents, project_facts=facts,
                               stop_after='PAGE_APPLICABILITY', aufgaben_result=baseline)
    if not context_documents:
        result['context_snapshots'] = copy.deepcopy(baseline.get('context_snapshots', []))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--working-units', required=True, help='Full cumulative timing-stage working-units.json')
    parser.add_argument('--pages', required=True)
    parser.add_argument('--documents', required=True)
    parser.add_argument('--context-pages', action='append', default=[])
    parser.add_argument('--context-documents', action='append', default=[])
    parser.add_argument('--output', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--model', default='gemini-3.5-flash')
    parser.add_argument('--location', default='global')
    parser.add_argument('--chunk-pages', type=int, default=3)
    parser.add_argument('--overlap-pages', type=int, default=1)
    args = parser.parse_args()
    if len(args.context_pages) != len(args.context_documents):
        parser.error('Each --context-pages requires matching --context-documents')
    output = Path(args.output)
    raw = output.with_name(output.stem + '.gemini-raw.json')
    compact = output.with_name(output.stem + '.applicability-only.json')
    trace = output.with_name(output.stem + '.trace')
    if any(p.exists() for p in (output, raw, compact, trace, raw.with_suffix('.attempts.json'))):
        parser.error('Outputs already exist; use a new output directory')
    baseline = json.loads(Path(args.working_units).read_text())
    kinds = {(s['source_key'], s['document_id']): s['source_type'] for s in baseline['source_snapshots']}
    documents = [replace(d, source_type=kinds.get((d.source_key, d.id), 'project'))
                 for d in load_documents(args.pages, args.documents)]
    context = [d for pages, boundaries in zip(args.context_pages, args.context_documents)
               for d in load_documents(pages, boundaries)]
    validate_input(baseline, documents, args.chunk_pages, args.overlap_pages, expected_passes=prior_passes(baseline))
    validate_facts(baseline['project_facts'], [*documents, *context])
    from ...gemini import GeminiModel
    from ...trace import RawRunRecorder, TracingModel, RetryingModel
    model = RetryingModel(TracingModel(RawRunRecorder(
        GeminiModel(args.project, args.location, args.model), raw), trace))
    result = extract_applicability(baseline, documents, model, args.chunk_pages, args.overlap_pages, context)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    compact.write_text(json.dumps({'assessments': [review['gemini'] for unit in result['working_units']
                      for review in unit['reviews'] if review['kind'] == 'PAGE_APPLICABILITY']},
                      ensure_ascii=False, indent=2) + '\n')
    print(f"Wrote applicability assessments for {len(result['working_units'])} tasks: {output}")
    if result['status'] == 'request_failed':
        raise SystemExit('Unresolved applicability requests; partial results and raw attempts preserved')


if __name__ == '__main__':
    main()
