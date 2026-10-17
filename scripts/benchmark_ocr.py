#!/usr/bin/env python3
"""Isolated, source-bound OCR experiment. Never changes pipeline runs or approvals."""
import argparse
import csv
import copy
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'scripts/ocr_experiments'), str(ROOT/'pipeline/extraction_support')]
from document_ocr import GoogleDocumentOCR, OPTIONS, cached_ocr, digest, normalize, save
from ocr_repairs import repair_order

VERSION = 'ocr-benchmark-v1'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def text_normalized(value, display=False):
    text = unicodedata.normalize('NFC', value)
    if display:
        # Only display-equivalent notation. Never map E, \\in or I to currency/litres.
        text = text.replace('^{\\circ}', '°').replace('\\circ', '°').replace('\\times','x')
        text = re.sub(r'\^\{([23])\}',r'\1',text).replace('~',' ')
        text = unicodedata.normalize('NFKC',text).translate(str.maketrans({'„':'"','“':'"','”':'"','×':'x'}))
        text = re.sub(r'\s*"\s*','"',text)
        text = re.sub(r'(?<=\d),\s+(?=\d)',',',text)
        text = re.sub(r'(?<=\d)\s*x\s*(?=\d)', ' x ', text)
    return ' '.join(text.split())


def distance(a, b):
    """Levenshtein distance, including insertion/deletion and word-order errors."""
    previous = list(range(len(b)+1))
    for i,x in enumerate(a,1):
        current = [i]
        for j,y in enumerate(b,1):
            current.append(min(previous[j]+1,current[-1]+1,previous[j-1]+(x!=y)))
        previous = current
    return previous[-1]


def score(expected, actual, critical):
    reference = text_normalized(expected); observed = text_normalized(actual)
    display_ref = text_normalized(expected,True); display_out = text_normalized(actual,True)
    critical_results = [{'expected':c,'correct':text_normalized(c,True).replace(' ','') in display_out.replace(' ','')} for c in critical]
    return {'characters':len(reference),'character_errors':distance(reference,observed),
        'display_characters':len(display_ref),'display_character_errors':distance(display_ref,display_out),
        'words':len(display_ref.split()),'word_errors':distance(display_ref.split(),display_out.split()),
        'exact_display_match':display_ref==display_out,'critical':critical_results}


def inside(box, region):
    return box is not None and region[0] <= (box[0]+box[2])/2 <= region[2] and region[1] <= (box[1]+box[3])/2 <= region[3]


def region_text(page, region, repaired=False):
    """Select by fixed geometry, never by matching the expected answer."""
    if repaired:
        subset = copy.deepcopy(page)
        subset['tokens'] = [w for w in subset['tokens'] if inside(w['bbox'],region)]
        ids = {w['id'] for w in subset['tokens']}
        lines = []
        for line in subset['lines']:
            words = [w for w in subset['tokens'] if w['id'] in line.get('token_ids',[])]
            if words:
                spans = sorted({tuple(s) for w in words for s in w['text_segments']})
                line['text'] = ''.join(subset['text'][a:b] for a,b in spans).rstrip('\r\n')
                line['text_segments'] = [list(s) for s in spans]
                line['token_ids'] = [w['id'] for w in words]
                line['bbox'] = [min(w['bbox'][0] for w in words),min(w['bbox'][1] for w in words),max(w['bbox'][2] for w in words),max(w['bbox'][3] for w in words)]
                lines.append(line)
            elif not line.get('token_ids') and inside(line.get('bbox'),region):lines.append(line)
        subset['lines'] = lines
        return repair_order(subset)['text']
    tokens = [w for w in page['tokens'] if inside(w['bbox'],region)]
    if not tokens:
        return '\n'.join(l['text'] for l in page['lines'] if inside(l.get('bbox'),region))
    spans = sorted({tuple(s) for w in tokens for s in w['text_segments']})
    pieces = []; last = None
    for a,b in spans:
        if last is not None and a>last and page['text'][last:a].strip():pieces.append('\n')
        elif last is not None and a>last:pieces.append(page['text'][last:a])
        pieces.append(page['text'][a:b]);last=b
    return ''.join(pieces)


def parse_tsv(value, width, height):
    lines=[]
    reader=csv.DictReader(io.StringIO(value),delimiter='\t',quoting=csv.QUOTE_NONE)
    required={'level','block_num','par_num','line_num','left','top','width','height','text'}
    if not required <= set(reader.fieldnames or []):raise ValueError('Tesseract did not return TSV; invalid output must not count as an empty OCR page')
    for word in reader:
        if word['level']!='5' or not word['text'].strip():continue
        x,y,w,h=(int(word[k]) for k in ('left','top','width','height'))
        lines.append({'text':word['text'],'bbox':[x/width*1000,y/height*1000,(x+w)/width*1000,(y+h)/height*1000],
            'line_key':[word[k] for k in ('block_num','par_num','line_num')]})
    return lines


def tess_text(words, region):
    rows=[];last=None
    for w in words:
        if not inside(w['bbox'],region):continue
        if rows and w['line_key']==last:rows[-1]+=' '+w['text']
        else:rows.append(w['text'])
        last=w['line_key']
    return '\n'.join(rows)


class BudgetProvider:
    def __init__(self, provider, output, limit):
        self.provider=provider;self.processor=provider.processor;self.path=output/'request-ledger.json';self.limit=limit
        self.entries=json.loads(self.path.read_text()) if self.path.exists() else []

    def process(self, pixels):
        if len(self.entries)>=self.limit:raise RuntimeError('Benchmark Google request limit reached')
        event={'image_sha256':hashlib.sha256(pixels).hexdigest(),'status':'reserved','started_at':time.time()}
        self.entries.append(event);save(self.path,self.entries)
        start=time.monotonic()
        try:
            result=self.provider.process(pixels);event['status']='succeeded';return result
        except Exception as exc:
            event['status']='failed';event['error_type']=type(exc).__name__;raise
        finally:
            event['seconds']=time.monotonic()-start;save(self.path,self.entries)


def render(pdf_page, dpi, box=None):
    import pymupdf
    clip=None
    if box:
        a,b,c,d=box;r=pdf_page.rect
        clip=pymupdf.Rect(a*r.width/1000,b*r.height/1000,c*r.width/1000,d*r.height/1000)
    return pdf_page.get_pixmap(dpi=dpi,clip=clip,alpha=False)


def tesseract(image, output, models, version, psm):
    import pymupdf
    binding={'image_sha256':sha(image),'models':{p.name:sha(p) for p in sorted(models.glob('*.traineddata'))},'engine':version,'psm':psm,'languages':'deu+eng','output_format':'tsv-v1'}
    path=output/'tesseract-cache'/(digest(binding)+'.json');path.parent.mkdir(exist_ok=True)
    if path.exists():
        result=json.loads(path.read_text())
        if result['request']!=binding or result['response_sha256']!=digest(result['response']):raise ValueError('Tesseract cache modified')
        return result['response']
    started=time.monotonic()
    command=['tesseract',str(image),'stdout','--tessdata-dir',str(models),'-l','deu+eng','--oem','1','--psm',str(psm),'-c','tessedit_create_tsv=1']
    env=dict(os.environ,OMP_THREAD_LIMIT='1')
    result=subprocess.run(command,check=True,capture_output=True,text=True,timeout=180,env=env)
    pix=pymupdf.Pixmap(str(image));response={'tsv':result.stdout,'stderr':result.stderr,'seconds':time.monotonic()-started,
        'words':parse_tsv(result.stdout,pix.width,pix.height)}
    save(path,{'request':binding,'response':response,'response_sha256':digest(response)})
    return response


def aggregate(records):
    summaries=[]
    for variant in dict.fromkeys(r['variant'] for r in records):
        rows=[r for r in records if r['variant']==variant]
        errors=sum(r['scores']['display_character_errors'] for r in rows);chars=sum(r['scores']['display_characters'] for r in rows)
        checks=[c for r in rows for c in r['scores']['critical']]
        unambiguous=[c for r in rows if not r.get('ambiguity') for c in r['scores']['critical']]
        summaries.append({'variant':variant,'regions':len(rows),'failed_regions':sum(bool(r.get('error')) for r in rows),
            'strict_cer':sum(r['scores']['character_errors'] for r in rows)/max(1,sum(r['scores']['characters'] for r in rows)),
            'display_cer':errors/max(1,chars),'display_wer':sum(r['scores']['word_errors'] for r in rows)/max(1,sum(r['scores']['words'] for r in rows)),
            'exact_regions':sum(r['scores']['exact_display_match'] for r in rows),'critical_correct':sum(c['correct'] for c in checks),'critical_total':len(checks),
            'unambiguous_critical_correct':sum(c['correct'] for c in unambiguous),'unambiguous_critical_total':len(unambiguous),
            'control_exact_regions':sum(r['scores']['exact_display_match'] for r in rows if r['id'].startswith('control_'))})
    return summaries


def write_report(output, manifest, records, ledger, metadata):
    summaries=aggregate(records)
    payload={'protocol':VERSION,'manifest_sha256':digest(manifest),'metadata':metadata,'summaries':summaries,'records':records,'request_ledger':ledger}
    save(output/'results.json',payload)
    e=html.escape
    body=['<!doctype html><html lang="en"><meta charset="utf-8"><title>OCR comparison</title><style>body{font:16px system-ui;max-width:1700px;margin:30px;color:#173249}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:9px;border:1px solid #ccd5db;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f5f7;padding:12px}img{max-width:100%}details{border:1px solid #ccd5db;margin:8px 0;padding:10px}.good{color:#167344}.bad{color:#b02525}nav{position:sticky;top:0;background:white;padding:10px}</style><h1>OCR comparison · 10 BLB regions</h1>',
    '<p>Fixed, manually transcribed regions from 10 physical PDF pages. Includes known failures and four control passages. This is a targeted diagnostic sample, not a representative accuracy estimate, statistical certification or source approval.</p>',
    '<p>Full-page OCR is scored only inside the fixed reference rectangles. Crop OCR uses the same rectangle rendered directly from the source PDF at 600 DPI, not enlarged 300-DPI pixels. Tesseract uses official German + English best models; page segmentation is automatic (PSM 3) on pages and uniform text (PSM 6) on crops. Crop comparisons therefore change both context and segmentation mode.</p>',
    '<p>Strict CER compares visible Unicode and normalized whitespace. Display CER additionally normalizes equivalent superscripts, multiplication signs, degree notation and typography; it does not turn E or \\in into €, or I/| into l. CER includes wrong reading order. Critical checks require the specified value or phrase, including units and negations, in the selected region. The narrow litre glyph is reported separately as ambiguous.</p>',
    '<nav><a href="results.json">Raw results and scores</a> · <a href="reference.json">Frozen references</a> · <a href="#evidence">Evidence</a></nav>',
    '<table><tr><th>Variant</th><th>Display CER ↓</th><th>Strict CER ↓</th><th>Word errors ↓</th><th>Exact regions</th><th>Critical checks</th><th>Excl. ambiguous litre</th><th>Exact controls / 4</th><th>Failures</th></tr>']
    for s in summaries:
        body.append('<tr>'+''.join('<td>'+e(str(v))+'</td>' for v in [s['variant'],f'{s["display_cer"]:.2%}',f'{s["strict_cer"]:.2%}',f'{s["display_wer"]:.2%}',f'{s["exact_regions"]}/{s["regions"]}',f'{s["critical_correct"]}/{s["critical_total"]}',f'{s["unambiguous_critical_correct"]}/{s["unambiguous_critical_total"]}',s['control_exact_regions'],s['failed_regions']])+'</tr>')
    body.append('</table><h2>Runtime and cost accounting</h2><pre>'+e(json.dumps(metadata,ensure_ascii=False,indent=2))+'</pre><p>API estimates are not billing measurements. Baseline latency is unavailable. Cached requests incur no new OCR call in this experiment; stored timings refer to original executions. Tesseract has no per-page API charge; local/server compute cost is not measured. Transport retries may add latency. Google add-on billing should be checked against the billing export.</p><h2 id="evidence">Fixed references and every result</h2>')
    for reference in manifest['references']:
        name=reference['id'];body.append(f'<h2>PDF page {reference["pdf_page"]}: {e(name)}</h2><img src="images/{name}-reference.png" alt="Visible reference region"><h3>Reference transcription</h3><pre>'+e(reference['expected'])+'</pre>')
        if reference.get('ambiguity'):body.append('<p>'+e(reference['ambiguity'])+'</p>')
        for r in [r for r in records if r['id']==name]:
            checks=r['scores']['critical'];ok=sum(c['correct'] for c in checks)
            body.append('<details><summary>'+e(r['variant'])+f' · critical {ok}/{len(checks)}</summary><pre>'+e(r.get('error') or r['text'])+'</pre><ul>')
            for c in checks:body.append('<li class="'+('good' if c['correct'] else 'bad')+'">'+('PASS ' if c['correct'] else 'FAIL ')+e(c['expected'])+'</li>')
            body.append('</ul></details>')
    (output/'report.html').write_text(''.join(body)+'</html>')
    return summaries


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--tessdata',required=True);parser.add_argument('--max-google-calls',type=int,default=30)
    parser.add_argument('--reuse-ocr-cache',action='append',default=[])
    args=parser.parse_args()
    from bauvo import load_environment
    config=load_environment();manifest=json.loads(Path(args.manifest).read_text());output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    if sha(manifest['source_pdf'])!=manifest['source_sha256'] or sha(manifest['baseline_pages'])!=manifest['baseline_sha256']:raise ValueError('Benchmark source/baseline fingerprint changed')
    models=Path(args.tessdata).resolve()
    if not all((models/(lang+'.traineddata')).is_file() for lang in ('deu','eng')):raise ValueError('German and English traineddata required')
    version=subprocess.run(['tesseract','--version'],capture_output=True,text=True,check=True).stdout.splitlines()[0]
    binding={'version':VERSION,'manifest':manifest,'options':OPTIONS,'processor':config['ocr_processor'],'tesseract':version,'models':{lang:sha(models/(lang+'.traineddata')) for lang in ('deu','eng')}}
    if (output/'binding.json').exists() and json.loads((output/'binding.json').read_text())!=binding:raise ValueError('Experiment configuration changed; choose a new output directory')
    save(output/'binding.json',binding);save(output/'reference.json',manifest);(output/'images').mkdir(exist_ok=True)
    provider=BudgetProvider(GoogleDocumentOCR(config['ocr_processor']),output,args.max_google_calls)
    baseline=json.loads(Path(manifest['baseline_pages']).read_text())
    records=[];timings=[]
    def record(ref,variant,text,error=None):
        records.append({'id':ref['id'],'pdf_page':ref['pdf_page'],'variant':variant,'text':text,'error':error,'ambiguity':ref.get('ambiguity'),'scores':score(ref['expected'],text,ref['critical'])})
        save(output/'records-checkpoint.json',records)
    import pymupdf
    with pymupdf.open(manifest['source_pdf']) as pdf:
        for ref in manifest['references']:
            n=ref['pdf_page'];p=pdf[n-1];box=ref['bbox'];name=ref['id']
            print(f'Benchmark PDF page {n}: {name}',flush=True)
            render(p,300,box).save(output/'images'/(name+'-reference.png'))
            original=next(p for p in baseline['pages'] if p['pdf_page']==n)
            for repaired in (False,True):record(ref,'baseline_300_'+('order' if repaired else 'raw'),region_text(original,box,repaired))
            for dpi in (300,600):
                pix=render(p,dpi);image=output/'images'/f'page-{n:04}-{dpi}.png';pix.save(image);pixels=image.read_bytes()
                try:
                    raw,path=cached_ocr(provider,pixels,output/'ocr-cache',args.reuse_ocr_cache)
                    normalized=normalize(raw,n,sha(image),str(path.relative_to(output)))
                    for repaired in (False,True):record(ref,f'google_{dpi}_'+('order' if repaired else 'raw'),region_text(normalized,box,repaired))
                except Exception as exc:
                    for mode in ('raw','order'):record(ref,f'google_{dpi}_{mode}','',type(exc).__name__+': '+str(exc))
                try:
                    tess=tesseract(image,output,models,version,3);timings.append({'variant':f'tesseract_{dpi}_page','page':n,'seconds':tess['seconds']})
                    record(ref,f'tesseract_{dpi}_page',tess_text(tess['words'],box))
                except Exception as exc:record(ref,f'tesseract_{dpi}_page','',type(exc).__name__+': '+str(exc))
            image=output/'images'/(name+'-direct-600.png');render(p,600,box).save(image)
            try:
                raw,path=cached_ocr(provider,image.read_bytes(),output/'ocr-cache',args.reuse_ocr_cache)
                record(ref,'google_direct_600_crop',raw['document']['text'])
            except Exception as exc:record(ref,'google_direct_600_crop','',type(exc).__name__+': '+str(exc))
            try:
                tess=tesseract(image,output,models,version,6);timings.append({'variant':'tesseract_direct_600_crop','page':n,'seconds':tess['seconds']})
                record(ref,'tesseract_direct_600_crop',tess_text(tess['words'],[0,0,1000,1000]))
            except Exception as exc:record(ref,'tesseract_direct_600_crop','',type(exc).__name__+': '+str(exc))
    google_timings=[]
    for image in sorted((output/'images').glob('*.png')):
        if image.name.endswith('-reference.png'):continue
        variant='google_direct_600_crop' if '-direct-' in image.name else ('google_600_page' if image.name.endswith('-600.png') else 'google_300_page')
        event=next((e for e in provider.entries if e['image_sha256']==sha(image)),None)
        google_timings.append({'variant':variant,'image':str(image.relative_to(output)),
            'seconds':event.get('seconds') if event else None,'cached_before_experiment':event is None})
    metadata={'benchmark_code_sha256':sha(__file__),'source_sha256':manifest['source_sha256'],'tesseract':version,'traineddata_sha256':binding['models'],
        'google_requests_reserved_this_experiment':len(provider.entries),'successful_requests':sum(e['status']=='succeeded' for e in provider.entries),
        'google_seconds':sum(e.get('seconds',0) for e in provider.entries),'google_timings':google_timings,'tesseract_timings':timings,
        'model_provenance':json.loads((models/'provenance.json').read_text()) if (models/'provenance.json').exists() else None,
        'google_estimate_usd_base_only':len(provider.entries)*.0015,'google_estimate_usd_conservative_with_two_addons':len(provider.entries)*.0135,
        'cost_assumptions':'USD 1.50/1000 base pages; conservative upper estimate adds USD 6/1000 for each of two configured premium features. Ignores free tier/discounts/taxes; not an invoice.',
        'pricing_source':'https://cloud.google.com/products/document-ai/pricing','google_options':OPTIONS,
        'reference_method':manifest['reference_method'],'selection_limit':'Only selected regions on one BLB; no held-out document, checkbox or handwritten-reference evaluation.'}
    summaries=write_report(output,manifest,records,provider.entries,metadata)
    print(json.dumps({'report':str(output/'report.html'),'summaries':summaries},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
