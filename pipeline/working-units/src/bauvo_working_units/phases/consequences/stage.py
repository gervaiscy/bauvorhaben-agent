"""Assess timed tasks once per batch of ten, using Sol or Gemini."""
import argparse
import copy
import json
from dataclasses import replace
from pathlib import Path

from ...fact_artifact import validate_facts
from ...offer_stage import validate_input
from ...page_flow import extract_page_units
from ...project_flow import load_documents
from ..timing.artifact import load_artifact
from .artifact import output_paths, write_outputs
from .payload import task_rows

PRIOR_PASSES = ('PAGE_EXTRACTION', 'PAGE_OFFERS', 'PAGE_TIMING')


def extract_consequences(baseline, documents, model, chunk_pages=10, overlap_pages=1,
                          context_documents=(), on_batch=None):
    validate_input(baseline, documents, chunk_pages, overlap_pages, expected_passes=PRIOR_PASSES)
    task_rows(baseline['working_units'], baseline['project_facts'])
    facts = validate_facts(baseline['project_facts'], [*documents, *context_documents])
    result = extract_page_units(documents, model, chunk_pages=chunk_pages, overlap_pages=overlap_pages,
                               supported_anchors=baseline.get('supported_anchors', []),
                               context_documents=context_documents, project_facts=facts,
                               stop_after='PAGE_CONSEQUENCES', aufgaben_result=baseline, on_batch=on_batch)
    if not context_documents:
        result['context_snapshots'] = copy.deepcopy(baseline.get('context_snapshots', []))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--working-units', required=True, help='Timing result.json with its audit sidecar, or a full cumulative artifact')
    parser.add_argument('--pages', required=True)
    parser.add_argument('--documents', required=True)
    parser.add_argument('--context-pages', action='append', default=[], help='Original contract sources for local fact validation; BLB sources must have --context-type blb')
    parser.add_argument('--context-documents', action='append', default=[])
    parser.add_argument('--context-type', action='append', choices=('project', 'blb'), default=[])
    parser.add_argument('--output', required=True, help='Readable tasks with consequences; lossless data in <stem>.audit.json')
    parser.add_argument('--provider', choices=('gemini', 'sol'), default='gemini')
    parser.add_argument('--project', help='Google Cloud project; used only by Gemini')
    parser.add_argument('--model', help='Defaults to gemini-3.5-flash or gpt-6.1-sol according to --provider')
    parser.add_argument('--reasoning-effort', choices=('low', 'medium', 'high'), default='medium', help='Sol reasoning; Gemini consequences use MEDIUM')
    parser.add_argument('--location', default='global')
    parser.add_argument('--chunk-pages', type=int, default=10, help='Must match the saved extraction windows; consequence batches always contain up to ten tasks')
    parser.add_argument('--overlap-pages', type=int, default=1)
    parser.add_argument('--max-cost-usd', default='30')
    parser.add_argument('--spend-ledger', help='Persistent USD ledger shared with retries; reuse the same limit')
    parser.add_argument('--plan-only', action='store_true', help='Plan/replay without new provider calls')
    from ...call_budget import add_call_arguments, CallBudget, CallLimitExceeded, PlanOnlyStop
    add_call_arguments(parser)
    args = parser.parse_args()
    if args.max_new_calls < 0:
        parser.error('--max-new-calls must be nonnegative')
    if len(args.context_pages) != len(args.context_documents):
        parser.error('Each --context-pages requires matching --context-documents')
    if args.context_type and len(args.context_type) != len(args.context_pages):
        parser.error('Provide one --context-type per context source')
    output = Path(args.output)
    raw = output.with_name(output.stem + ('.openai-raw.json' if args.provider == 'sol' else '.gemini-raw.json'))
    trace = output.with_name(output.stem + '.trace')
    partial = output.with_name(output.stem + '.partial.json')
    compact = output.with_name(output.stem + '.consequences-only.json')
    if any(p.exists() for p in (*output_paths(output), raw, raw.with_suffix('.attempts.json'), trace, partial, compact)):
        parser.error('Outputs already exist; use a new output directory and reuse --cache-dir')
    baseline = load_artifact(args.working_units)
    kinds = {(s['source_key'], s['document_id']): s['source_type'] for s in baseline['source_snapshots']}
    documents = [replace(d, source_type=kinds.get((d.source_key, d.id), 'project'))
                 for d in load_documents(args.pages, args.documents)]
    context = [replace(d, source_type=kind)
               for pages, boundaries, kind in zip(args.context_pages, args.context_documents,
                                                  args.context_type or ['project']*len(args.context_pages))
               for d in load_documents(pages, boundaries)]
    # Source and task validation must precede provider creation and paid work.
    validate_input(baseline, documents, args.chunk_pages, args.overlap_pages, expected_passes=PRIOR_PASSES)
    validate_facts(baseline['project_facts'], [*documents, *context])
    task_rows(baseline['working_units'], baseline['project_facts'])
    from .backend import backend
    cached = backend(output, args.cache_dir, CallBudget(0 if args.plan_only else args.max_new_calls),
        provider=args.provider, project=args.project, location=args.location, model=args.model,
        reasoning=args.reasoning_effort, max_cost_usd=args.max_cost_usd,
        spend_ledger=args.spend_ledger, plan_only=args.plan_only)
    from ...trace import RawRunRecorder, TracingModel, RetryingModel
    model = cached if args.plan_only else RetryingModel(TracingModel(RawRunRecorder(cached, raw), trace))
    def save_partial(result):
        partial.parent.mkdir(parents=True, exist_ok=True)
        temp = partial.with_suffix('.tmp')
        temp.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        temp.replace(partial)
    try:
        result = extract_consequences(baseline, documents, model, args.chunk_pages, args.overlap_pages,
                                      context, on_batch=None if args.plan_only else save_partial)
    except (CallLimitExceeded, PlanOnlyStop) as exc:
        if args.plan_only:
            print('Plan only: ' + str(exc))
            return
        raise SystemExit(str(exc)) from exc
    if args.plan_only:
        print('Plan only: all consequence requests replayed; 0 new calls')
        return
    write_outputs(output, result)
    compact.write_text(json.dumps({u['id']: next((r['gemini'] for r in u['reviews']
                       if r['kind'] == 'PAGE_CONSEQUENCES'), None) for u in result['working_units']},
                       ensure_ascii=False, indent=2)+'\n')
    assessed = sum(any(r['kind'] == 'PAGE_CONSEQUENCES' for r in u['reviews']) for u in result['working_units'])
    print(f"Wrote consequences assessments for {assessed}/{len(result['working_units'])} tasks: {output}")
    if result['status'] in ('request_failed', 'incomplete'):
        raise SystemExit('Consequences incomplete; inspect preserved raw responses and validation findings; no downstream call made')


if __name__ == '__main__':
    main()
