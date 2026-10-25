"""Existing pages.json -> optional document boundaries -> quality and source review.

This stage never renders PDFs, transcribes images or rewrites pages.json.
"""
import argparse
import json
import os
from pathlib import Path
from common import save, digest
from document_grouping import group_documents
from ocr_quality import report, review_html
import grouping_client


def process(args):
    source = Path(args.input)
    data = json.loads(source.read_text())
    if data.get('schema_version') != 6:
        raise ValueError('Document processing requires schema-6 pages.json')
    pages = data['pages']
    count = data['page_count']
    out = Path(args.output) if args.output else source.parent
    out.mkdir(parents=True, exist_ok=True)
    def model(project, location, name, parts):
        return grouping_client.cached_group_call(args, parts, out/'grouping-cache',
            lambda: grouping_client.gemini_sdk(project, location, name, parts))
    try:
        documents = group_documents(args, pages, model)
        save(out/'documents.json', documents)
        quality=report(pages,documents,count)
        save(out/'quality-report.json',quality)
        duplicates = {}
        for page in pages:
            if page['text'].strip():
                duplicates.setdefault(digest(page['text']), []).append(page['pdf_page'])
        save(out/'duplicate-pages.json', {'groups': [v for v in duplicates.values() if len(v)>1], 'policy': 'Retain all physical pages and provenance'})
        (out/'source-review.html').write_text(review_html(pages,documents,quality,os.path.relpath(source.parent/'page-images',out)))
    except Exception as exc:
        save(out/'grouping-error.json', {'error': str(exc), 'page_count': len(pages)})
        raise
    if (out/'grouping-error.json').exists():
        import time
        (out/'grouping-error.json').rename(out/f'grouping-error-resolved-{time.time_ns()}.json')
    print(f'processed {len(pages)} existing pages and {len(documents["documents"])} document proposals; status={quality["status"]}; output={out}',flush=True)


def run():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--input', required=True, help='Existing schema-6 pages.json')
    parser.add_argument('--output', help='Extraction folder containing source images; defaults to input folder')
    parser.add_argument('--project', required=True)
    parser.add_argument('--location', default='global')
    parser.add_argument('--document-boundary-model', dest='model', default=grouping_client.DEFAULT_MODEL)
    parser.add_argument('--document-boundaries', choices=['auto','off'], default='auto')
    parser.add_argument('--reuse-grouping-cache', action='append', default=[])
    process(parser.parse_args())


if __name__ == '__main__':
    run()
