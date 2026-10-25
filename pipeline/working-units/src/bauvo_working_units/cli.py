"""Command-line entry point."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

from .project_flow import extract_project_units, load_documents
from .gemini import GeminiModel
from .schemas import GENERATION, RESPONSE_SCHEMAS, PROTOCOL, PHASE_THINKING_LEVELS
from .trace import RawRunRecorder, TracingModel, RetryingModel


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract document-wide, reviewable Working Units")
    parser.add_argument("--pages", required=True, help="Authoritative pages.json (schema 6; legacy schema 5 supported)")
    parser.add_argument("--documents", help="Source-bound documents.json proposals (required for schema 6)")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--raw-output", help="One JSON array containing only Gemini results")
    parser.add_argument("--project", required=True, help="Google Cloud project ID")
    parser.add_argument("--location", default="global")
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument("--whole-document-max-chars", type=int, default=80000)
    parser.add_argument("--chunk-pages", type=int, default=3)
    parser.add_argument("--overlap-pages", type=int, default=1)
    parser.add_argument("--language", choices=("en", "de"), default="de",
                        help="Language of natural-language output values")
    parser.add_argument("--trace-dir", help="Save every Gemini prompt and raw JSON response for review")
    parser.add_argument("--cache-dir", help="Resume identical complete model calls without repeating paid requests")
    parser.add_argument("--date-anchors", help="JSON array of cited document_id/id/event/date anchors; dates must occur in source quotes")
    parser.add_argument('--context-pages', action='append', default=[], help='Additional project sources for fact collection; repeat with matching --context-documents')
    parser.add_argument('--context-documents', action='append', default=[], help='Boundaries for each additional project source')
    parser.add_argument('--source-type', choices=('project','blb'), default='project', help='BLB sources provide tasks, never project facts')
    parser.add_argument('--context-type', action='append', choices=('project','blb'), default=[], help='Type of each context source; default project, or repeat one per context source')
    parser.add_argument('--project-facts', help='Reuse a saved working-units.project-facts.json; original sources must be supplied as pages/context-pages')
    stages = {'project-facts':'PAGE_PROJECT_FACTS', 'aufgaben':'PAGE_EXTRACTION', 'offers':'PAGE_OFFERS',
              'timing':'PAGE_TIMING', 'consequences':'PAGE_CONSEQUENCES', 'applicability':'PAGE_APPLICABILITY'}
    parser.add_argument('--stop-after', choices=tuple(stages), default='applicability', help='Stop after this pass; later passes make no requests')
    args = parser.parse_args()
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
        facts = load_facts(args.project_facts, [*documents, *context_documents])
        if Path(args.project_facts).resolve() in (output.resolve(), raw_output.resolve(), facts_output.resolve(), units_output.resolve() if units_output else None):
            parser.error('Saved facts input must differ from every output')
    if (args.project_facts or args.stop_after != 'applicability') and any(d.schema_version != 6 for d in documents):
        parser.error('Saved facts and separate passes require schema-6 pages')
    anchors = json.loads(Path(args.date_anchors).read_text()) if args.date_anchors else []
    from .date_calculation import validate_anchors
    anchors = validate_anchors(anchors, documents)
    from .pass_snapshots import PassSnapshots
    snapshots = PassSnapshots(snapshots_dir, facts_output) if all(d.schema_version==6 for d in documents) else None
    backend=GeminiModel(args.project,args.location,args.model)
    if args.cache_dir:
        from .cache import CachedModel
        backend=CachedModel(backend,args.cache_dir,{'project':args.project,'location':args.location,'model':args.model,
                            'generation':GENERATION, 'phase_thinking_levels':PHASE_THINKING_LEVELS,
                            'response_schemas':RESPONSE_SCHEMAS,'protocol':PROTOCOL})
    model = RawRunRecorder(backend, raw_output)
    if args.trace_dir:
        model = TracingModel(model, args.trace_dir)
    model = RetryingModel(model)
    result = extract_project_units(documents, model,
                                   args.whole_document_max_chars, args.chunk_pages,
                                   args.overlap_pages, args.language, anchors, context_documents, on_pass=snapshots,
                                   project_facts=facts, stop_after=stages[args.stop_after])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.stop_after == 'project-facts':
        print(f"Wrote {len(result['facts'])} project facts to {output}")
    else:
        from .unit_export import write_units
        write_units(units_output, [record['gemini'] for record in result['working_units']])
        print(f"Wrote {len(result['offers'])} offers, {len(result['working_units'])} Working Units and "
          f"{len(result['gemini_responses'])} preserved Gemini responses to {output}")

    if result.get("status") == "request_failed":
        raise SystemExit("Unresolved model requests; partial output and raw attempts preserved")


if __name__ == "__main__":
    main()
