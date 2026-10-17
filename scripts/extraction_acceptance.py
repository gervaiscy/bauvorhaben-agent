"""Explicit source review approval bound to the complete extraction snapshot."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

ARTIFACTS=('pages.json','documents.json','quality-report.json')


def hashes(folder):
    paths=[folder/name for name in ARTIFACTS]
    page_data=json.loads((folder/'pages.json').read_text());pages=page_data['pages']
    inventory=folder/'page-images.json'
    if any(p.get('ocr_provider')=='gemini' for p in pages) and not inventory.is_file():
        raise ValueError('Rendered page inventory is missing')
    if inventory.exists():
        rendered=json.loads(inventory.read_text())
        images=rendered.get('images',[])
        if not isinstance(images,list) or any(not isinstance(i,dict) for i in images):
            raise ValueError('Invalid rendered image inventory')
        if [i.get('pdf_page') for i in images]!=[p['pdf_page'] for p in pages]:
            raise ValueError('Rendered image inventory does not cover the stored pages in order')
        if page_data.get('sha256') and rendered.get('source_sha256')!=page_data['sha256']:
            raise ValueError('Rendered image inventory belongs to a different source')
        for image,page in zip(images,pages):
            expected=f'page-images/page-{page["pdf_page"]:04}.png'
            if image.get('image_file')!=expected or page.get('image_file',expected)!=expected:
                raise ValueError('Invalid rendered image path')
            actual=hashlib.sha256((folder/expected).read_bytes()).hexdigest()
            if image.get('image_sha256')!=actual or page.get('image_sha256',actual)!=actual:
                raise ValueError('Rendered image evidence changed')
        paths.append(inventory)
    if page_data.get('schema_version') not in (5,6):raise ValueError('Legacy extraction requires a fresh run')
    if page_data.get('schema_version') in (5,6):
        for page in pages:
            raw=folder/page['raw_ocr_file']
            if raw.parent!=folder/'ocr-cache' or hashlib.sha256(raw.read_bytes()).hexdigest()!=page['raw_ocr_sha256']:raise ValueError('Raw OCR evidence changed')
            paths.append(raw)
            for check in page.get('ocr_rechecks', []):
                for attempt in check['attempts']:
                    crop=folder/attempt['image'];evidence=folder/attempt['raw_ocr_file']
                    if crop.parent!=folder/'page-images' or evidence.parent!=folder/'ocr-cache':raise ValueError('Invalid crop evidence path')
                    if hashlib.sha256(crop.read_bytes()).hexdigest()!=attempt['image_sha256'] or hashlib.sha256(evidence.read_bytes()).hexdigest()!=attempt['raw_ocr_sha256']:raise ValueError('Crop OCR evidence changed')
                    paths.extend((crop,evidence))
            paths.extend(folder/c['review_crop'] for c in page.get('checkboxes',[]) if c.get('review_crop'))
    paths += [folder/'page-images'/f'page-{p["pdf_page"]:04}.png' for p in pages]
    if any(not p.is_file() for p in paths):raise ValueError('Extraction review artifacts are missing')
    return {str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def approve(run,source,reviewer,note,confirm):
    if not reviewer.strip() or not note.strip() or confirm!='reviewed':raise ValueError('REVIEWER, NOTE and CONFIRM=reviewed are required after comparing every rendered page with source-review.html')
    folder=run/'extraction'/source;quality=json.loads((folder/'quality-report.json').read_text())
    if quality.get('schema_version') not in (5,6) or quality.get('status')!='awaiting_source_review' or quality.get('findings'):raise ValueError('Unresolved extraction findings must be corrected before approval; see quality-report.json')
    record={'source':source,'reviewer':reviewer,'note':note,'confirmed':'reviewed','reviewed_at':datetime.now(timezone.utc).isoformat(),
            'files':hashes(folder),'scope':'Visible-source page transcription and document-boundary proposals reviewed; no legal or work-unit approval. Not a statistical 99% accuracy certification.'}
    dest=run/'extraction-approvals';dest.mkdir(exist_ok=True)
    target=dest/(source+'.json')
    if target.exists():raise ValueError('Approval already exists; do not overwrite review evidence')
    target.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    return target


def require_approved(run,sources):
    for source in sources:
        key=source['key'];folder=run/'extraction'/key;path=run/'extraction-approvals'/(key+'.json')
        if not path.exists():raise ValueError('Extraction is not source-reviewed: '+key+'. Inspect extraction/'+key+'/source-review.html and quality-report.json; after review use make approve-extraction CASE=... SOURCE='+key+' REVIEWER=... NOTE=... CONFIRM=reviewed')
        approval=json.loads(path.read_text());quality=json.loads((folder/'quality-report.json').read_text())
        if approval.get('source')!=key or approval.get('confirmed')!='reviewed' or not approval.get('reviewer') or not approval.get('note') or quality.get('schema_version') not in (5,6) or quality.get('status')!='awaiting_source_review' or quality.get('findings') or approval.get('files')!=hashes(folder):raise ValueError('Extraction changed or has unresolved findings after approval: '+key)


def resolve_finding(run,source,page,code,reviewer,note,confirm):
    """Record a human's narrow source review; never waive structural loss or rewrite text."""
    import uuid
    allowed={'low_ocr_confidence','empty_ocr_page','ocr_line_geometry_missing','uncertain_transcription','possible_nonliteral_transcription'}
    if code not in allowed:raise ValueError('This finding needs corrected extraction; it cannot be acknowledged away')
    if confirm!='reviewed' or not reviewer.strip() or not note.strip():raise ValueError('Require REVIEWER, NOTE and CONFIRM=reviewed after checking the rendered source')
    if (run/'extraction-approvals'/(source+'.json')).exists():raise ValueError('Preserve the existing approval; changed review needs a new run')
    folder=run/'extraction'/source;quality=json.loads((folder/'quality-report.json').read_text());pages=json.loads((folder/'pages.json').read_text())['pages']
    target=next((p for p in pages if p['pdf_page']==page),None)
    if target is None:raise ValueError('Unknown source page')
    found=[f for f in quality['findings'] if f['code']==code and f['pdf_page']==page]
    if not found:raise ValueError('No matching unresolved finding')
    record={'source':source,'pdf_page':page,'code':code,'reviewer':reviewer,'note':note,'confirmed':'reviewed',
            'reviewed_at':datetime.now(timezone.utc).isoformat(),'snapshot_before':hashes(folder),
            'scope':'Source-checked OCR disagreement/legibility/location only. Source text, unknown values and geometry remain unchanged. No statistical accuracy certification.'}
    audit=run/'extraction-reviews'/('finding-'+uuid.uuid4().hex[:12]);audit.mkdir(parents=True)
    (audit/'quality-report-before.json').write_bytes((folder/'quality-report.json').read_bytes())
    quality.setdefault('resolved_findings',[]).append(record)
    quality['findings']=[f for f in quality['findings'] if not(f['code']==code and f['pdf_page']==page)]
    quality['status']='needs_review' if quality['findings'] else 'awaiting_source_review'
    (folder/'quality-report.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2)+'\n')
    (audit/'resolution.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    # Update extraction checkpoints after this audited review-only transformation.
    marker=folder/'complete.json'
    if marker.exists():
        data=json.loads(marker.read_text());data['quality-report.json']=hashlib.sha256((folder/'quality-report.json').read_bytes()).hexdigest();marker.write_text(json.dumps(data,indent=2)+'\n')
    done=run/'extract.done.json'
    if done.exists():
        data=json.loads(done.read_text());rel=str((folder/'quality-report.json').relative_to(run));data['files'][rel]=hashlib.sha256((folder/'quality-report.json').read_bytes()).hexdigest();done.write_text(json.dumps(data,indent=2)+'\n')
    return audit,len(quality['findings'])
