"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .project_flow import extract_project_units, load_documents
from .gemini import GeminiModel
from .trace import RawRunRecorder, TracingModel


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract document-wide, reviewable Working Units")
    parser.add_argument("--pages", required=True, help="Authoritative pages.json (schema 5)")
    parser.add_argument("--documents", help="Optional source-bound documents.json proposals")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--raw-output", help="One JSON array containing only Gemini results")
    parser.add_argument("--project", required=True, help="Google Cloud project ID")
    parser.add_argument("--location", default="global")
    parser.add_argument("--model", default="gemini-2.5-flash")
    parser.add_argument("--whole-document-max-chars", type=int, default=80000)
    parser.add_argument("--chunk-pages", type=int, default=3)
    parser.add_argument("--overlap-pages", type=int, default=1)
    parser.add_argument("--language", choices=("en", "de"), default="de",
                        help="Language of natural-language output values")
    parser.add_argument("--trace-dir", help="Save every Gemini prompt and raw JSON response for review")
    parser.add_argument("--cache-dir", help="Resume identical complete model calls without repeating paid requests")
    args = parser.parse_args()

    output = Path(args.output)
    raw_output = (Path(args.raw_output) if args.raw_output else
                  output.with_name(f"{output.stem}.gemini-raw.json"))
    if raw_output.resolve() == output.resolve():
        parser.error("--raw-output and --output must be different files")
    documents = load_documents(args.pages, args.documents)
    backend=GeminiModel(args.project,args.location,args.model)
    if args.cache_dir:
        from .cache import CachedModel
        backend=CachedModel(backend,args.cache_dir,{'project':args.project,'location':args.location,'model':args.model,
                            'generation':{'temperature':0,'response_mime_type':'application/json'}})
    model = RawRunRecorder(backend, raw_output)
    if args.trace_dir:
        model = TracingModel(model, args.trace_dir)
    result = extract_project_units(documents, model,
                                   args.whole_document_max_chars, args.chunk_pages,
                                   args.overlap_pages, args.language)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(result['offers'])} offers, {len(result['working_units'])} Working Units and "
          f"{len(result['gemini_responses'])} preserved Gemini responses to {output}")


if __name__ == "__main__":
    main()
