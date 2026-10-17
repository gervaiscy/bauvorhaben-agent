#!/usr/bin/env python3
"""Separate local Tesseract run: rendered pixels -> pages.json -> source review.

No cloud/model calls, document-boundary proposals, working units or approvals.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import benchmark_ocr as bench
from document_ocr import save,digest
from legacy_document_grouping import fallback
from legacy_ocr_quality import report,review_html
from tesseract_pages import VERSION,project_tsv


def write_local_review(source,out,pages,documents,quality):
    """Small lazy previews keep a full-document review from decoding gigabytes."""
    import pymupdf
    previews=out/'preview-images';previews.mkdir(exist_ok=True)
    with pymupdf.open(source) as pdf:
        for page in pages:
            n=page['pdf_page'];bench.render(pdf[n-1],200).save(previews/f'page-{n:04}.png')
    content=review_html(pages,documents,quality).replace('<img ','<img loading="lazy" ')
    for page in pages:
        filename=f'page-{page["pdf_page"]:04}.png'
        original=f'page-images/{filename}';preview=f'preview-images/{filename}'
        content=content.replace(f'<img loading="lazy" src="{original}" alt="Rendered source page">',
            f'<a href="{original}"><img loading="lazy" src="{preview}" alt="Rendered source page; click for full resolution"></a>')
    (out/'source-review.html').write_text(content)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--tessdata',required=True);parser.add_argument('--dpi',type=int,default=600)
    parser.add_argument('--reuse-cache',action='append',default=[])
    args=parser.parse_args()
    source=Path(args.input).resolve();out=Path(args.output).resolve();models=Path(args.tessdata).resolve()
    if args.dpi<300 or args.dpi>900:parser.error('DPI must be between 300 and 900')
    if not all((models/(lang+'.traineddata')).is_file() for lang in ('deu','eng')):parser.error('Supply German and English traineddata')
    version=subprocess.run(['tesseract','--version'],capture_output=True,text=True,check=True).stdout.splitlines()[0]
    binding={'protocol':VERSION,'source_sha256':bench.sha(source),'engine':version,'dpi':args.dpi,
        'languages':'deu+eng','psm':3,'oem':1,'models':{lang:bench.sha(models/(lang+'.traineddata')) for lang in ('deu','eng')}}
    out.mkdir(parents=True,exist_ok=True)
    if (out/'binding.json').exists() and json.loads((out/'binding.json').read_text())!=binding:raise ValueError('Run configuration changed; use a new output directory')
    save(out/'binding.json',binding)
    for folder in ('tesseract-cache','ocr-cache','page-images'):(out/folder).mkdir(exist_ok=True)
    for directory in args.reuse_cache:
        for path in Path(directory).glob('*.json'):
            stored=json.loads(path.read_text())
            if stored.get('request',{}).get('output_format')!='tsv-v1':continue
            if stored.get('response_sha256')!=digest(stored.get('response')):raise ValueError('Tesseract reuse cache modified')
            dest=out/'tesseract-cache'/path.name
            if not dest.exists():shutil.copyfile(path,dest)
    pages=[];started=time.monotonic()
    import pymupdf
    with pymupdf.open(source) as pdf:
        count=len(pdf)
        for n,p in enumerate(pdf,1):
            print(f'Tesseract {version}: PDF page {n}/{count} at {args.dpi} DPI',flush=True)
            pix=bench.render(p,args.dpi);image=out/'page-images'/f'page-{n:04}.png';pix.save(image)
            result=bench.tesseract(image,out,models,version,3)
            evidence=out/'ocr-cache'/(digest(result)+'.json');save(evidence,result)
            row=project_tsv(result['tsv'],n,pix.width,pix.height,bench.sha(image),str(evidence.relative_to(out)))
            row['raw_ocr_sha256']=bench.sha(evidence);row['ocr_seconds']=result['seconds'];pages.append(row)
            save(out/'pages.json',{'schema_version':5,'source':str(source),'sha256':binding['source_sha256'],
                'page_count':count,'selected_pages':list(range(1,count+1)),'request_binding':binding,'pages':pages})
    lines=[l for p in pages for l in p['lines']]
    documents=fallback({'schema_version':5,'pages_sha256':digest(pages)},lines,'disabled',[]) if lines else {'schema_version':5,'pages_sha256':digest(pages),'status':'disabled','documents':[],'review_flags':[]}
    quality=report(pages,documents,count)
    quality['findings'].append({'code':'checkbox_and_handwriting_detection_not_supported','pdf_page':None,
        'reason':'Tesseract text output does not establish checkbox states or handwriting. Review original pages explicitly; absence of detections is not absence of marks.'})
    quality['status']='needs_review';quality['experiment_only']=True
    save(out/'documents.json',documents);save(out/'quality-report.json',quality)
    write_local_review(source,out,pages,documents,quality)
    save(out/'run-summary.json',{'engine':version,'dpi':args.dpi,'pages':len(pages),'lines':len(lines),
        'elapsed_seconds_this_invocation':time.monotonic()-started,'ocr_seconds_including_reused_results':sum(p['ocr_seconds'] for p in pages),
        'api_calls':0,'source_sha256':binding['source_sha256'],'source_review_approved':False,
        'limitations':['No checkbox-state detector','No handwriting classification','No document-boundary inference','No working-unit extraction']})
    print(f'Finished: {len(pages)} pages, {len(lines)} lines; review required; {out}',flush=True)


if __name__=='__main__':main()
