"""PDF -> rendered page images -> visible-text transcription -> pages.json."""
import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import pymupdf
from gemini_ocr import GeminiImageOCR, DEFAULT_MODEL, cached_transcription, save, normalize as normalize_gemini

RENDER_VERSION = 'cropbox-png-300dpi-ocr-v3'


def parse_pages(spec,count):
    if not spec: return list(range(1,count+1))
    result=set()
    for item in spec.split(','):
        bounds=item.split('-')
        if len(bounds)==1: result.add(int(bounds[0]))
        elif len(bounds)==2: result.update(range(int(bounds[0]),int(bounds[1])+1))
        else: raise ValueError('Bad page selection: '+item)
    return sorted(result)


def run():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--location', default='global', help='Vertex AI Gemini location')
    parser.add_argument('--ocr-provider', choices=['gemini'], default='gemini')
    parser.add_argument('--ocr-model', default=DEFAULT_MODEL, help='Image transcription model; always minimal reasoning')
    parser.add_argument('--transport', choices=['sdk'], default='sdk')
    parser.add_argument('--pages')
    parser.add_argument('--reuse-ocr-cache',action='append',default=[],help='Read-through cache from a prior extraction; exact pixel/model/prompt/settings matches only')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    source = Path(args.input).expanduser()
    if not source.is_file():
        parser.error('--input must be a local PDF file')
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    provider = GeminiImageOCR(args.project,args.location,args.ocr_model)
    ocr_binding = provider.identity
    binding = {'schema_version': 6, 'ocr':ocr_binding, 'render_version': RENDER_VERSION}
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    images = out/'page-images'
    images.mkdir(exist_ok=True)
    pages = []
    if (out/'pages.json').exists() and not args.resume:
        raise ValueError('Output already has extraction evidence; use a new output directory or --resume')
    checkpoint_path = out/'pages.json'
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text())
        if checkpoint.get('request_binding') != binding or checkpoint.get('sha256') != source_hash:
            raise ValueError('Checkpoint configuration/source changed; start a new run')
    if any((out/name).exists() for name in ('sections.json','blocks.json')):raise ValueError('Legacy grouped extraction: start a fresh run; old evidence is preserved')
    with ExitStack() as cleanup, pymupdf.open(source) as pdf:
        cleanup.callback(provider.close)
        count = len(pdf)
        selected = parse_pages(args.pages, count)
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
            target = out/rendered_page['image_file']
            image = target.read_bytes()
            image_hash = rendered_page['image_sha256']
            print(f'OCR visible pixels: PDF page {number}/{count}', flush=True)
            raw, raw_path = cached_transcription(provider,image,out/'ocr-cache',args.reuse_ocr_cache)
            row = normalize_gemini(raw,number,image_hash,str(raw_path.relative_to(out)))
            row['raw_ocr_sha256'] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
            row['image_file'] = str(target.relative_to(out))
            pages.append(row)
            save(checkpoint_path, {'schema_version': 6, 'source': str(source), 'sha256': source_hash,
                                  'page_count': count, 'selected_pages': selected, 'request_binding': binding, 'pages': pages})
        print(f'wrote {len(pages)} OCR pages; output={out}',flush=True)


if __name__ == '__main__':
    run()
