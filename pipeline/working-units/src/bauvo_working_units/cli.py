"""Command-line entry point."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

from .project_flow import extract_project_units, load_documents
from .gemini import GeminiModel
from .trace import RawRunRecorder, TracingModel, RetryingModel


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract document-wide, reviewable Working Units")
    parser.add_argument("--pages", required=True, help="Authoritative pages.json (schema 6; legacy schema 5 supported)")
    parser.add_argument("--documents", help="Source-bound documents.json proposals (required for schema 6)")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--raw-output", help="One JSON array containing only Gemini results")
    parser.add_argument("--project", help="Google Cloud project ID")
    parser.add_argument("--location", default="global")
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument("--whole-document-max-chars", type=int, default=80000)
    parser.add_argument("--chunk-pages", type=int, default=10)
    parser.add_argument("--overlap-pages", type=int, default=1)
    parser.add_argument("--language", choices=("en", "de"), default="de",
                        help="Language of natural-language output values")
    parser.add_argument("--trace-dir", help="Save every Gemini prompt and raw JSON response for review")
    parser.add_argument("--cache-dir", help="Replay identical completed provider responses, even when local validation fails")
    parser.add_argument("--date-anchors", help="JSON array of cited document_id/id/event/date anchors; dates must occur in source quotes")
    parser.add_argument('--context-pages', action='append', default=[], help='Additional project sources for fact collection; repeat with matching --context-documents')
    parser.add_argument('--context-documents', action='append', default=[], help='Boundaries for each additional project source')
    parser.add_argument('--source-type', choices=('project','blb'), default='project', help='BLB sources provide tasks, never project facts')
    parser.add_argument('--context-type', action='append', choices=('project','blb'), default=[], help='Type of each context source; default project, or repeat one per context source')
    parser.add_argument('--project-facts', help='Reuse a saved working-units.project-facts.json; original sources must be supplied as pages/context-pages')
    parser.add_argument('--max-new-calls', type=int, default=0,
                        help='Maximum actual Gemini attempts, including retries; default 0 (offline)')
    parser.add_argument('--replay-project-facts', help='Replay saved facts locally against original sources; no Gemini calls')
    parser.add_argument('--refresh-request', action='append', default=[], help='Explicitly regenerate only this saved request SHA; preserve the previous response')
    parser.add_argument('--plan-only', action='store_true', help='Write the known request plan without any provider calls')
    stages = {'project-facts':'PAGE_PROJECT_FACTS', 'aufgaben':'PAGE_EXTRACTION', 'offers':'PAGE_OFFERS',
              'timing':'PAGE_TIMING', 'consequences':'PAGE_CONSEQUENCES', 'applicability':'PAGE_APPLICABILITY'}
    parser.add_argument('--stop-after', choices=tuple(stages), default='applicability', help='Stop after this pass; later passes make no requests')
    parser.add_argument('--consequence-provider', choices=('gemini', 'sol'), default='gemini')
    parser.add_argument('--consequence-model')
    parser.add_argument('--consequence-reasoning', choices=('low', 'medium', 'high'), default='medium')
    parser.add_argument('--consequence-budget-usd', default='30')
    args = parser.parse_args()
    if args.max_new_calls < 0:
        parser.error('--max-new-calls must be nonnegative')
    if args.max_new_calls and not args.project:
        parser.error('--project is required for paid calls')
    if args.replay_project_facts and (args.refresh_request or args.max_new_calls or args.project_facts or args.stop_after != 'project-facts'):
        parser.error('--replay-project-facts requires --stop-after project-facts, no --project-facts and no paid calls')
    if len(args.context_pages) != len(args.context_documents):
        parser.error('Each --context-pages requires matching --context-documents')
    if args.context_type and len(args.context_type)!=len(args.context_pages):
        parser.error('Provide one --context-type per --context-pages, or omit all for project sources')

    output = Path(args.output)
    raw_output = (Path(args.raw_output) if args.raw_output else
                  output.with_name(f"{output.stem}.gemini-raw.json"))
    facts_output = output.with_name(f'{output.stem}.project-facts.json')
    units_output = output.with_name(f'{output.stem}.units-only.json') if args.stop_after != 'project-facts' else None
    snapshots_dir = output.with_name(f'{output.stem}.passes')
    if raw_output.resolve() in (output.resolve(), facts_output.resolve(), units_output.resolve() if units_output else None):
        parser.error("--raw-output, --output and derived project-facts output must be different files")
    if output.exists() or raw_output.exists() or facts_output.exists() or snapshots_dir.exists() or (units_output and units_output.exists()):
        parser.error('Output/raw-output already exists; use new output paths and reuse --cache-dir for resume')
    if args.stop_after in ('timing', 'consequences'):
        from .phases.timing.artifact import output_paths
        if any(path.exists() for path in output_paths(output)):
            parser.error('Timing output/audit/validation already exists; use new output paths')
        if raw_output.resolve() in {path.resolve() for path in output_paths(output)}:
            parser.error('--raw-output must differ from timing output/audit/validation paths')
    documents = [replace(doc, source_type=args.source_type) for doc in load_documents(args.pages, args.documents)]
    context_types = args.context_type or ['project'] * len(args.context_pages)
    context_documents = [replace(doc, source_type=kind)
                         for pages, boundaries, kind in zip(args.context_pages, args.context_documents, context_types)
                         for doc in load_documents(pages, boundaries)]
    if any(doc.schema_version != 6 for doc in context_documents):
        parser.error('Project context requires schema-6 pages')
    facts = None
    if args.project_facts:
        from .fact_artifact import load_facts
        from .project_facts import require_current_catalog
        facts = load_facts(args.project_facts, [*documents, *context_documents])
        require_current_catalog(facts)
        if Path(args.project_facts).resolve() in (output.resolve(), raw_output.resolve(), facts_output.resolve(), units_output.resolve() if units_output else None):
            parser.error('Saved facts input must differ from every output')
    if (args.project_facts or args.stop_after != 'applicability') and any(d.schema_version != 6 for d in documents):
        parser.error('Saved facts and separate passes require schema-6 pages')
    anchors = json.loads(Path(args.date_anchors).read_text()) if args.date_anchors else []
    from .date_calculation import validate_anchors
    anchors = validate_anchors(anchors, documents)
    from .pass_snapshots import PassSnapshots
    snapshots = PassSnapshots(snapshots_dir, facts_output) if not args.plan_only and not args.replay_project_facts and all(d.schema_version==6 for d in documents) else None
    from .call_budget import CallBudget, BudgetedModel, CallLimitExceeded, PlanOnlyStop
    from .cache import CachedModel
    budget = CallBudget(0 if args.plan_only else args.max_new_calls)
    backend = BudgetedModel(lambda: GeminiModel(args.project, args.location, args.model), budget)
    backend = CachedModel(backend, args.cache_dir or output.parent / 'provider-responses',
                          {'project':args.project, 'location':args.location, 'model':args.model},
                          budget=budget, plan_path=output.with_name(output.stem+'.request-plan.json'),
                          refresh_requests=args.refresh_request, plan_only=args.plan_only)
    if args.stop_after in ('consequences', 'applicability'):
        from .phases.consequences.backend import backend as consequence_backend, PhaseRouter
        consequence_model = args.consequence_model or (args.model if args.consequence_provider == 'gemini' else None)
        backend = PhaseRouter(backend, consequence_backend(output, args.cache_dir, budget,
            provider=args.consequence_provider, project=args.project, location=args.location,
            model=consequence_model, reasoning=args.consequence_reasoning,
            max_cost_usd=args.consequence_budget_usd, plan_only=args.plan_only,
            factory=(lambda: GeminiModel(args.project, args.location, consequence_model))
                    if args.consequence_provider == 'gemini' else None))
    if args.replay_project_facts:
        from .phases.project_facts.revalidation import replay_project_facts
        source = Path(args.replay_project_facts)
        result = replay_project_facts(json.loads(source.read_text()), [*documents, *context_documents])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        print(f"Offline replay: {len(result['gemini_responses'])} saved responses; 0 new calls; "
              f"{len(result['facts'])} facts; status={result['status']}; output={output}")
        if result['status'] in ('incomplete', 'request_failed'):
            raise SystemExit('Replay remains blocked; missing/invalid evidence preserved in output')
        return
    model = backend
    if not args.plan_only:
        model = RawRunRecorder(model, raw_output)
        if args.trace_dir:
            model = TracingModel(model, args.trace_dir)
        model = RetryingModel(model)
    try:
        result = extract_project_units(documents, model,
                                       args.whole_document_max_chars, args.chunk_pages,
                                       args.overlap_pages, args.language, anchors, context_documents, on_pass=snapshots,
                                       project_facts=facts, stop_after=stages[args.stop_after])
    except (CallLimitExceeded, PlanOnlyStop) as exc:
        if args.plan_only:
            print(f'Plan only: {exc}')
            return
        raise SystemExit(str(exc)) from exc
    if args.plan_only:
        print("Plan only: reached requests replayed offline; status=" + result.get("status", "unknown") + "; 0 new calls")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.stop_after in ('timing', 'consequences'):
        if args.stop_after == 'timing':
            from .phases.timing.artifact import write_outputs
        else:
            from .phases.consequences.artifact import write_outputs
        write_outputs(output, result)
    else:
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.stop_after == 'project-facts':
        print(f"Wrote {len(result['facts'])} project facts to {output}")
    else:
        from .unit_export import write_units
        write_units(units_output, [record['gemini'] for record in result['working_units']])
        print(f"Wrote {len(result['offers'])} offers, {len(result['working_units'])} Working Units and "
          f"{len(result['gemini_responses'])} preserved Gemini responses to {output}")

    if result.get('status') == 'incomplete':
        phase = 'offers' if any(f.get('code') == 'offer_validation_failed'
                               for f in result.get('review_findings', [])) else (
            'consequences' if any(f.get('code', '').startswith('consequence_')
                                  for f in result.get('review_findings', [])) else 'project facts')
        raise SystemExit(f'Incomplete {phase}; output, raw answer and validation findings preserved')
    if result.get("status") == "request_failed":
        raise SystemExit("Unresolved model requests; partial output and raw attempts preserved")


if __name__ == "__main__":
    main()
