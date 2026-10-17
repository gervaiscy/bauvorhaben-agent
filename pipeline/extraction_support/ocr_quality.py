"""OCR completeness and source review, independently of semantic grouping."""
import hashlib
import html
import json
from common import digest


def report(pages,documents,page_count):
    findings=[]
    def flag(code,page=None,**detail):findings.append({'code':code,'pdf_page':page,**detail})
    if [p['pdf_page'] for p in pages]!=list(range(1,page_count+1)):flag('incomplete_page_coverage')
    numbers=[p['pdf_page'] for p in pages]
    covered=[n for d in documents['documents'] for n in d['pdf_pages']]
    if (documents['documents'] or any(p['text'].strip() for p in pages)) and covered!=numbers:
        flag('document_page_coverage')
    assignments=documents.get('page_assignments',[])
    expected={n:d['document_id'] for d in documents['documents'] for n in d['pdf_pages']}
    if assignments!=[{'pdf_page':n,'document_id':expected.get(n)} for n in numbers]:
        flag('document_page_assignments')
    for p in pages:
        n=p['pdf_page']
        if hashlib.sha256(p['text'].encode()).hexdigest()!=p['text_sha256']:flag('page_text_changed',n)
        if not p['text'].strip():flag('empty_ocr_page',n)
        for finding in p.get('transcription_findings',[]):flag(finding['code'],n,reason=finding['reason'])
        for c in p['checkboxes']:
            if c['state']=='uncertain':flag('checkbox_needs_review',n,checkbox_id=c['id'])
    return {'schema_version':6,'status':'needs_review' if findings else 'awaiting_source_review',
        'boundary_findings':documents.get('review_flags',[]),'page_count':page_count,'emitted_pages':len(pages),'findings':findings,
        'accuracy':{'target':.99,'measured':False,'reason':'Requires independently reviewed source transcription; OCR confidence is not an accuracy measurement.'},
        'snapshot_sha256':digest({'pages':pages,'documents':documents})}


def review_html(pages,documents,quality):
    e=html.escape
    output=['<!doctype html><meta charset="utf-8"><title>OCR page review</title><style>body{font:16px system-ui;margin:28px;line-height:1.5}article{display:grid;grid-template-columns:1fr 1fr;gap:24px}img{max-width:100%}pre{white-space:pre-wrap}details{border:1px solid #ddd;padding:10px}</style><h1>OCR source review</h1><p>Compare every rendered page with the complete page text and checkboxes. Document boundaries are proposals and never filter text. Uncertain boundaries keep the bundle together for analysis.</p>']
    for title,data in (('Quality findings',quality),('Document boundary proposals',documents)):
        output.append('<details><summary>'+title+'</summary><pre>'+e(json.dumps(data,ensure_ascii=False,indent=2))+'</pre></details>')
    for p in pages:
        output.append('<h2>PDF page '+str(p['pdf_page'])+'</h2><article><div><img src="page-images/page-'+str(p['pdf_page']).zfill(4)+'.png" alt="Rendered source page"></div><div><h3>Complete OCR text</h3><pre>'+e(p['text'])+'</pre>')
        if p.get('coordinates_available') is False:
            output.append('<p>Gemini visible-text transcription: coordinates and confidence scores are unavailable. Checkbox states are transcribed marks, not independently measured detections.</p>')
        output.append('<h3>Checkboxes</h3><pre>'+e(json.dumps(p['checkboxes'],ensure_ascii=False,indent=2))+'</pre>')
        for c in p['checkboxes']:
            if c.get('review_crop'):output.append('<img style="max-width:400px" src="'+e(c['review_crop'],quote=True)+'" alt="Enlarged checkbox crop">')
        output.append('</div></article>')
    return ''.join(output)
