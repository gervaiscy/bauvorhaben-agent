"""Rendered pixels -> authoritative OCR pages -> optional document boundaries -> review."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'pipeline/document-boundaries'), str(ROOT/'pipeline/pdf-extractor')]

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import pymupdf
from document_ocr import DEFAULT_PROCESSOR, GoogleDocumentOCR, cached_ocr, normalize, save, digest, endpoint
from legacy_document_grouping import group_documents, VERSION as BOUNDARY_VERSION
from legacy_ocr_quality import report, review_html
from ocr_repairs import VERSION as REPAIR_VERSION, repair_order, recheck
from gemini_ocr import GeminiImageOCR, DEFAULT_MODEL, cached_transcription, normalize as normalize_gemini

RENDER_VERSION = 'cropbox-png-300dpi-ocr-v3'


def run():
    # Transcription and document-boundary interpretation are separate requests.
    import experimental_extract as extract
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--location', default='global', help='Gemini location; independent of OCR region')
    parser.add_argument('--model', default='gemini-2.5-flash')
    parser.add_argument('--ocr-provider', choices=['google-document-ai'], default='google-document-ai')
    parser.add_argument('--ocr-model', default=DEFAULT_MODEL, help='Image transcription model; always minimal reasoning')
    parser.add_argument('--transport', choices=['sdk'], default='sdk')
    parser.add_argument('--ocr-processor', default=DEFAULT_PROCESSOR, help='Pinned Document AI processorVersions resource')
    parser.add_argument('--ocr-min-confidence', type=float, default=.98)
    parser.add_argument('--pages')
    parser.add_argument('--ocr-recheck-per-page', type=int, default=3, help='Maximum suspicious regions rechecked per page (two OCR calls each)')
    parser.add_argument('--ocr-recheck-limit', type=int, default=24, help='Maximum regions rechecked per input file; 0 flags without paid crop calls')
    parser.add_argument('--document-boundaries',choices=['auto','off'],default='auto',help='Optional Gemini document proposals; off keeps the complete file together')
    parser.add_argument('--reuse-grouping-cache',action='append',default=[],help='Read-through cache for identical source-bound grouping requests')
    parser.add_argument('--reuse-ocr-cache',action='append',default=[],help='Read-through cache from a prior extraction; exact pixel/processor/options matches only')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.ocr_provider == 'google-document-ai':
        endpoint(args.ocr_processor)
    if not 0 <= args.ocr_min_confidence <= 1:
        parser.error('--ocr-min-confidence must be between 0 and 1')
    if args.ocr_recheck_per_page < 0 or args.ocr_recheck_limit < 0:
        parser.error('OCR recheck limits must be nonnegative')
    source = Path(args.input).expanduser()
    if not source.is_file():
        parser.error('--input must be a local PDF file')
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    provider = (GeminiImageOCR(args.project,args.location,args.ocr_model) if args.ocr_provider == 'gemini'
                else GoogleDocumentOCR(args.ocr_processor))
    ocr_binding = (provider.identity if args.ocr_provider == 'gemini' else
        {'provider':'google-document-ai','processor':args.ocr_processor,'min_confidence':args.ocr_min_confidence,
         'repairs':{'version':REPAIR_VERSION,'per_page':args.ocr_recheck_per_page,'limit':args.ocr_recheck_limit}})
    binding = {'schema_version': 5, 'ocr':ocr_binding,
               'boundaries': {'protocol':BOUNDARY_VERSION,'mode':args.document_boundaries,'project': args.project, 'location': args.location, 'model': args.model}, 'render_version': RENDER_VERSION,
               }
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    images = out/'page-images'
    images.mkdir(exist_ok=True)
    pages = []
    recheck_budget = [args.ocr_recheck_limit]
    if (out/'pages.json').exists() and not args.resume:
        raise ValueError('Output already has extraction evidence; use a new output directory or --resume')
    checkpoint_path = out/'pages.json'
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text())
        if checkpoint.get('request_binding') != binding or checkpoint.get('sha256') != source_hash:
            raise ValueError('Checkpoint configuration/source changed; start a new run')
    if any((out/name).exists() for name in ('sections.json','blocks.json')):raise ValueError('Legacy grouped extraction: start a fresh run; old evidence is preserved')
    with ExitStack() as cleanup, pymupdf.open(source) as pdf:
        if args.ocr_provider == 'gemini':
            cleanup.callback(provider.close)
        count = len(pdf)
        selected = extract.parse_pages(args.pages, count)
        if not selected or any(n < 1 or n > count for n in selected):
            raise ValueError('Selected PDF pages outside input')
        if checkpoint_path.exists() and checkpoint.get('selected_pages') != selected:
            raise ValueError('Checkpoint page selection changed; start a new run')
        rendered = []
        for number in selected:
            page = pdf[number-1]
            image = page.get_pixmap(dpi=300, alpha=False).tobytes('png')
            image_hash = hashlib.sha256(image).hexdigest()
            target = images/f'page-{number:04}.png'
            target.write_bytes(image)
            rendered.append({'pdf_page':number,'image_file':str(target.relative_to(out)),
                             'image_sha256':image_hash})
        save(out/'page-images.json',{'source_sha256':source_hash,'render_version':RENDER_VERSION,
                                    'dpi':300,'page_count':count,'selected_pages':selected,'images':rendered})
        for rendered_page in rendered:
            number = rendered_page['pdf_page']
            page = pdf[number-1]
            target = out/rendered_page['image_file']
            image = target.read_bytes()
            image_hash = rendered_page['image_sha256']
            print(f'OCR visible pixels: PDF page {number}/{count}', flush=True)
            if args.ocr_provider == 'gemini':
                raw, raw_path = cached_transcription(provider,image,out/'ocr-cache',args.reuse_ocr_cache)
                row = normalize_gemini(raw,number,image_hash,str(raw_path.relative_to(out)))
            else:
                raw, raw_path = cached_ocr(provider, image, out/'ocr-cache', args.reuse_ocr_cache)
                row = normalize(raw, number, image_hash, str(raw_path.relative_to(out)), args.ocr_min_confidence)
                row = repair_order(row)
                with pymupdf.open() as raster:
                    p = raster.new_page(width=page.rect.width, height=page.rect.height)
                    p.insert_image(p.rect, stream=image)
                    recheck(row, p, provider, out, args.reuse_ocr_cache, args.ocr_recheck_per_page, recheck_budget)
                    for mark in row['checkboxes']:
                        x0,y0,x1,y1=mark['bbox'];pad=10
                        clip=pymupdf.Rect(max(0,x0-pad)*p.rect.width/1000, max(0,y0-pad)*p.rect.height/1000,
                                          min(1000,x1+pad)*p.rect.width/1000, min(1000,y1+pad)*p.rect.height/1000)
                        crop=images/(mark['id']+'.png')
                        p.get_pixmap(clip=clip,dpi=600,alpha=False).save(crop)
                        mark['review_crop']=str(crop.relative_to(out))
            row['raw_ocr_sha256'] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
            row['image_file'] = str(target.relative_to(out))
            pages.append(row)
            save(checkpoint_path, {'schema_version': 5, 'source': str(source), 'sha256': source_hash,
                                  'page_count': count, 'selected_pages': selected, 'request_binding': binding, 'pages': pages})
        lines = [line for page in pages for line in page['lines']]
        def model(project, location, name, parts):
            return extract.cached_group_call(args, parts, out/'grouping-cache',
                lambda: extract.gemini_sdk(project, location, name, parts))
        try:
            documents = group_documents(args, pages, lines, model)
            save(out/'documents.json', documents)
            quality=report(pages,documents,count)
            save(out/'quality-report.json',quality)
            duplicates = {}
            for page in pages:
                if page['text'].strip():
                    duplicates.setdefault(digest(page['text']), []).append(page['pdf_page'])
            save(out/'duplicate-pages.json', {'groups': [v for v in duplicates.values() if len(v)>1], 'policy': 'Retain all physical pages and provenance'})
            (out/'source-review.html').write_text(review_html(pages,documents,quality))
        except Exception as exc:
            save(out/'grouping-error.json', {'error': str(exc), 'line_count': len(lines)})
            raise
        if (out/'grouping-error.json').exists():
            import time
            (out/'grouping-error.json').rename(out/f'grouping-error-resolved-{time.time_ns()}.json')
        print(f'wrote {len(pages)} OCR pages and {len(documents["documents"])} document proposals; status={quality["status"]}; output={out}',flush=True)
