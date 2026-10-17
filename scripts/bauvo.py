#!/usr/bin/env python3
"""Local Make entry point. Paths/configuration are data, never shell commands."""
from __future__ import annotations
import argparse
import contextlib
from datetime import date,datetime,timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'pipeline'),str(ROOT/'pipeline/working-units/src')]
sys.path.insert(0,str(ROOT/'scripts'))
from cache_binding import stage_binding
from customer_report import fingerprint,build_input,compile_report
from enrich_working_units import GeminiModel,load_manifest,write_json

STAGES=('extract','units','reconcile','review','timing','report')


def read(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def slug(value):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}',value):raise ValueError('CASE must use letters, digits, - or _ (max 80 characters)')
    return value

def load_environment():
    file=ROOT/'.env'
    allowed={'GOOGLE_APPLICATION_CREDENTIALS','GOOGLE_CLOUD_PROJECT','BAUVO_LOCATION','BAUVO_MODEL','BAUVO_WORKERS','BAUVO_BATCH_SIZE','BAUVO_THINKING_BUDGET','OPENAI_API_KEY','BAUVO_OCR_PROVIDER','BAUVO_OCR_MODEL','BAUVO_OCR_PROCESSOR','BAUVO_OCR_MIN_CONFIDENCE','BAUVO_OCR_RECHECK_PER_PAGE','BAUVO_OCR_RECHECK_LIMIT','BAUVO_DOCUMENT_BOUNDARIES','BAUVO_DOCUMENT_BOUNDARY_MODEL'}
    if file.exists():
        for line in file.read_text().splitlines():
            line=line.strip()
            if not line or line.startswith('#'):continue
            key,sep,value=line.partition('=')
            if not sep or key not in allowed:raise ValueError('Unsupported .env setting: '+key)
            os.environ.setdefault(key,value.strip().strip('"').strip("'"))
    credential=os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    if credential:
        path=Path(credential).expanduser()
        if not path.is_absolute():path=ROOT/path
        if not path.is_file():raise ValueError('Configured credential file does not exist')
        os.environ['GOOGLE_APPLICATION_CREDENTIALS']=str(path)
    return {'project':os.environ.get('GOOGLE_CLOUD_PROJECT',''),
            'location':os.environ.get('BAUVO_LOCATION','global'),
            'model':os.environ.get('BAUVO_MODEL','gemini-2.5-flash'),
            'ocr_provider':os.environ.get('BAUVO_OCR_PROVIDER','gemini'),
            'ocr_model':os.environ.get('BAUVO_OCR_MODEL','gemini-3.5-flash'),
            'document_boundary_model':os.environ.get('BAUVO_DOCUMENT_BOUNDARY_MODEL','gemini-3.5-flash'),
            'document_boundaries':os.environ.get('BAUVO_DOCUMENT_BOUNDARIES','auto'),
            'workers':int(os.environ.get('BAUVO_WORKERS','2')),
            'batch_size':int(os.environ.get('BAUVO_BATCH_SIZE','6')),
            'thinking_budget':int(os.environ.get('BAUVO_THINKING_BUDGET','2048'))}

def factory_for(config):
    def factory():return GeminiModel(config['project'],config['location'],config['model'],32768,config['thinking_budget'])
    factory.identity={k:config[k] for k in ('project','location','model','thinking_budget')}
    return factory

def case_path(name):return ROOT/'cases'/f'{slug(name)}.json'
def load_case(name):
    path=case_path(name)
    if not path.exists():raise ValueError('Unknown CASE. Use make new CASE=... CONTRACT=... BLB=... SIGNED=YYYY-MM-DD')
    case=read(path);date.fromisoformat(case['contract_signed'])
    if case.get('contract_signed_verified') is not True:raise ValueError('Case must explicitly confirm signing date as its planning baseline')
    keys=[s['key'] for s in case['sources']]
    if len(keys)!=len(set(keys)) or not {'contract','blb'}<=set(keys):raise ValueError('Unique sources contract and blb required; append annex sources if needed')
    for s in case['sources']:
        slug(s['key']);pdf=Path(s['pdf']).expanduser()
        if not pdf.is_absolute():pdf=ROOT/pdf
        if not pdf.is_file():raise ValueError('Missing PDF: '+str(pdf))
        s['pdf']=str(pdf.resolve())
    return case

def new_case(name):
    target=case_path(name)
    if target.exists():raise ValueError('Case already exists; use another name or edit its JSON')
    signed=os.environ.get('SIGNED','');date.fromisoformat(signed)
    sources=[]
    for key in ('contract','blb'):
        value=os.environ.get(key.upper(),'')
        if not value:raise ValueError(key.upper()+' path is required')
        path=Path(value).expanduser().resolve()
        if not path.is_file():raise ValueError('Missing PDF: '+str(path))
        sources.append({'key':key,'pdf':str(path)})
    write_json(target,{'contract_signed':signed,'contract_signed_verified':True,'sources':sources})
    print('Created',target,'; SIGNED is your confirmed planning baseline, not an inferred legal conclusion.')

def get_run(name,case,config,fresh=False):
    folder=ROOT/'runs'/name;pointer=folder/'latest.json'
    binding={'case':case,'source_sha256':{s['key']:sha(s['pdf']) for s in case['sources']},'model':config}
    if pointer.exists() and not fresh:
        run=folder/read(pointer)['directory']
        if read(run/'run.json')['binding']!=binding:raise ValueError('Inputs/configuration changed. Use make fresh CASE='+name+' to retain old results.')
        return run
    run=folder/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6])
    run.mkdir(parents=True)
    write_json(run/'run.json',{'binding':binding,'created_at':datetime.now(timezone.utc).isoformat()})
    write_json(pointer,{'directory':run.name});return run

@contextlib.contextmanager
def run_lock(run):
    with (run/'.lock').open('a') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Another command is using this run')
        yield

def code_binding():
    return fingerprint({str(p.relative_to(ROOT)):sha(p) for folder in ('scripts','pipeline') for p in (ROOT/folder).rglob('*.py')})

def stage_done(run,stage):
    checkpoint=run/f'{stage}.done.json'
    if not checkpoint.exists():return False
    data=read(checkpoint)
    if data.get('code_scope')!='stage-v1' or data['code']!=stage_binding(ROOT,stage):raise ValueError('Code changed for stage '+stage+'. Use make fresh to preserve reproducibility.')
    if any(not (run/p).exists() or sha(run/p)!=digest for p,digest in data['files'].items()):raise ValueError('Stage outputs changed: '+stage)
    return True

def complete(run,stage,files):
    write_json(run/f'{stage}.done.json',{'code_scope':'stage-v1','code':stage_binding(ROOT,stage),'files':{str(Path(p).relative_to(run)):sha(p) for p in files},'finished_at':datetime.now(timezone.utc).isoformat()})

def command(args,log):
    env=dict(os.environ);env['PYTHONPATH']=str(ROOT/'pipeline/working-units/src')+os.pathsep+str(ROOT/'pipeline')
    with log.open('a') as output:
        process=subprocess.Popen([sys.executable,*map(str,args)],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        for line in process.stdout:print(line,end='',flush=True);output.write(line);output.flush()
        if process.wait():raise RuntimeError('Stage command failed; see '+str(log))

def execute_stage(stage,run,case,config):
    factory=factory_for(config);files=[]
    if stage=='extract':
        for source in case['sources']:
            out=run/'extraction'/source['key'];marker=out/'complete.json'
            if marker.exists():
                saved=read(marker)
                if all(sha(out/name)==digest for name,digest in saved.items()):
                    files.extend(out/name for name in saved);continue
                raise ValueError('Extraction checkpoint changed')
            if config.get('ocr_provider','gemini') != 'gemini':
                raise ValueError('Production extraction uses Gemini; set BAUVO_OCR_PROVIDER=gemini. Other OCR providers are isolated experiments.')
            args=[ROOT/'pipeline/pdf-extractor/ocr_pipeline.py','--input',source['pdf'],'--output',out,
                  '--project',config['project'],'--location',config['location'],'--resume',
                  '--ocr-provider','gemini','--ocr-model',config.get('ocr_model','gemini-3.5-flash')]
            document_args=[ROOT/'pipeline/extraction_support/document_pipeline.py',
                  '--input',out/'pages.json','--output',out,'--project',config['project'],
                  '--location',config['location'],'--document-boundary-model',config.get('document_boundary_model','gemini-3.5-flash'),
                  '--document-boundaries',config.get('document_boundaries','auto')]
            for previous in sorted(run.parent.glob('*/extraction/'+source['key']+'/ocr-cache'),reverse=True):
                if previous!=out/'ocr-cache':
                    args.extend(['--reuse-ocr-cache',previous])
                    if (previous.parent/'grouping-cache').is_dir():document_args.extend(['--reuse-grouping-cache',previous.parent/'grouping-cache'])
            command(args,run/f'extract-{source["key"]}.log')
            command(document_args,run/f'extract-{source["key"]}.log')
            artifacts=[out/name for name in ('page-images.json','pages.json','documents.json','duplicate-pages.json','quality-report.json','source-review.html')]
            write_json(marker,{p.name:sha(p) for p in artifacts});files+=artifacts
    elif stage=='units':
        from extraction_acceptance import require_approved
        require_approved(run,case['sources'])
        for source in case['sources']:
            extracted=run/'extraction'/source['key'];output=run/'working-units'/source['key']
            output.mkdir(parents=True,exist_ok=True);target=output/'working-units.json'
            # Do not overwrite incomplete evidence; attempts have distinct trace directories.
            if not target.exists():
                attempt=output/('attempt-'+uuid.uuid4().hex[:8]);attempt.mkdir()
                command(['-m','bauvo_working_units.cli','--pages',extracted/'pages.json','--documents',extracted/'documents.json',
                    '--output',target,'--raw-output',attempt/'raw.json','--trace-dir',attempt/'trace','--cache-dir',output/'model-cache',
                    '--project',config['project'],'--location',config['location'],'--model',config['model']],run/f'units-{source["key"]}.log')
            files.append(target)
        manifest={'contract_signed':case['contract_signed'],'report_settings':{'contract_signed_verified':True,'events':{},'selections':[]},
          'sources':[{'key':s['key'],'working_units':str(run/'working-units'/s['key']/'working-units.json'),
                      'pages':str(run/'extraction'/s['key']/'pages.json'),'documents':str(run/'extraction'/s['key']/'documents.json')} for s in case['sources']]}
        write_json(run/'input-manifest.json',manifest)
        signed,sources=load_manifest(run/'input-manifest.json');payload=build_input(sources,signed,manifest['report_settings'])
        write_json(run/'input-snapshot.json',payload);files.extend([run/'input-manifest.json',run/'input-snapshot.json'])
    elif stage=='reconcile':
        from batched_report import run_batched
        p=read(run/'input-snapshot.json')
        q=run_batched(p,factory,run/'reconciliation',batch_size=config['batch_size'],workers=config['workers'],model_config=factory.identity)
        validation=compile_report(p,q);write_json(run/'reconciliation-validation.json',validation)
        if validation['status']=='BLOCKED':raise ValueError('Reconciliation validation blocked; inspect reconciliation-validation.json')
        write_json(run/'reconciliation.json',q);files=[run/'reconciliation.json',run/'reconciliation-validation.json']
    elif stage=='review':
        from document_review import run_review
        review=run_review(read(run/'input-snapshot.json'),read(run/'reconciliation.json'),factory,run/'document-review',config['batch_size'])
        write_json(run/'document-review.json',review);files=[run/'document-review.json']
    elif stage=='timing':
        from timing_assessment import run_batches,calculate
        p=read(run/'input-snapshot.json');q=read(run/'reconciliation.json');review=read(run/'document-review.json')
        records=run_batches(p,q,review,factory,run/'timing',config['batch_size'],config['workers'])
        # Price rule identity must be explicitly confirmed from the model's source-backed rule set.
        from price_anchor import select_price_rule
        price_id=select_price_rule(p,q,factory,run/'timing/price-anchor')
        result=calculate(p,q,records,price_rule_id=price_id)
        write_json(run/'timing/timing-result.json',result);files=[run/'timing/assessments.json',run/'timing/timing-result.json']
    elif stage=='report':
        from document_report import build_document_report,render_document_report,publication_check
        report=build_document_report(read(run/'input-snapshot.json'),read(run/'reconciliation.json'),read(run/'document-review.json'),read(run/'timing/timing-result.json'))
        html=render_document_report(report).encode();out=run/'report';out.mkdir(exist_ok=True)
        write_json(out/'document-report.json',report);(out/'customer-report.html').write_bytes(html)
        write_json(out/'publication-status.json',publication_check(report,html));files=list(out.iterdir())
    complete(run,stage,files)

def doctor(config,live=False):
    import importlib.metadata
    if not config['project']:raise ValueError('Set GOOGLE_CLOUD_PROJECT in .env')
    print('Python:',sys.version.split()[0])
    for package in ('google-genai','PyMuPDF'):print(package+':',importlib.metadata.version(package))
    print('Project:',config['project'],'| Location:',config['location'],'| Model:',config['model'])
    if config.get('ocr_provider','gemini') != 'gemini':
        raise ValueError('Production extraction uses Gemini; set BAUVO_OCR_PROVIDER=gemini.')
    print('PDF transcription: Gemini | Model:',config.get('ocr_model','gemini-3.5-flash'),'| Reasoning: minimal')
    print('Document boundaries: Model:',config.get('document_boundary_model','gemini-3.5-flash'),'| Reasoning: minimal | Window: 5 pages')
    import google.auth
    credentials,_=google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
    print('Google credentials: loadable (contents never logged)')
    if live:
        from google import genai
        client=genai.Client(vertexai=True,project=config['project'],location=config['location'])
        response=client.models.generate_content(model=config.get('ocr_model','gemini-3.5-flash'),
            contents='Return only OK.',config={'max_output_tokens':64,'thinking_config':{'thinking_level':'MINIMAL'}})
        if not response.text:raise ValueError('Empty model probe')
        print('Live Vertex request: successful')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['new','doctor','auth-check','run','fresh','fresh-extract','status','open','open-extraction','resolve-extraction','approve-extraction','regroup',*STAGES])
    args=parser.parse_args();config=load_environment();name=slug(os.environ.get('CASE','sample'))
    if args.command=='new':new_case(name);return
    if args.command in ('doctor','auth-check'):doctor(config,args.command=='auth-check');return
    if args.command=='approve-extraction':
        from extraction_acceptance import approve
        pointer=ROOT/'runs'/name/'latest.json'
        if not pointer.exists():raise ValueError('No extraction run exists')
        run=pointer.parent/read(pointer)['directory']
        source=slug(os.environ.get('SOURCE',''))
        if source not in {s['key'] for s in load_case(name)['sources']}:raise ValueError('Unknown source')
        with run_lock(run):
            result=approve(run,source,os.environ.get('REVIEWER',''),os.environ.get('NOTE',''),os.environ.get('CONFIRM',''))
        print('Source review recorded:',result)
        return
    if args.command=='regroup':
        from extraction_review import prepare_regroup
        pointer=ROOT/'runs'/name/'latest.json'
        if not pointer.exists():raise ValueError('No run exists')
        run=pointer.parent/read(pointer)['directory']
        with run_lock(run):audit=prepare_regroup(run,slug(os.environ.get('SOURCE','')),read,write_json,sha)
        print('Prior grouping archived:',audit,'; now run make run CASE='+name)
        return
    if args.command=='resolve-extraction':
        pointer=ROOT/'runs'/name/'latest.json'
        if not pointer.exists():raise ValueError('No extraction run exists')
        run=pointer.parent/read(pointer)['directory']
        with run_lock(run):
            from extraction_acceptance import resolve_finding
            audit,remaining=resolve_finding(run,slug(os.environ.get('SOURCE','')),int(os.environ.get('PAGE','0')),os.environ.get('FINDING',''),os.environ.get('REVIEWER',''),os.environ.get('NOTE',''),os.environ.get('CONFIRM',''))
        print('Scoped OCR finding review:',audit,'; remaining:',remaining)
        return
    if args.command=='open-extraction':
        source=slug(os.environ.get('SOURCE','blb'))
        pointer=ROOT/'runs'/name/'latest.json'
        if not pointer.exists():raise ValueError('No extraction run exists')
        run=pointer.parent/read(pointer)['directory'];target=run/'extraction'/source/'source-review.html'
        if not target.is_file():raise ValueError('No visible-source review report yet; run make fresh-extract CASE='+name)
        subprocess.run(['open',str(target)],check=True)
        return
    if args.command in ('status','open'):
        pointer=ROOT/'runs'/name/'latest.json'
        if not pointer.exists():print('No run yet. Use make run CASE='+name);return
        run=pointer.parent/read(pointer)['directory'];print('Run:',run)
        for stage in STAGES:print(stage+':','complete' if (run/f'{stage}.done.json').exists() else 'pending')
        if (run/'failure.json').exists():print('Last failure:',read(run/'failure.json')['error'])
        if args.command=='open':
            html=run/'report/customer-report.html'
            if not html.exists():raise ValueError('No report yet')
            subprocess.run(['open',str(html)],check=True)
        return
    case=load_case(name)
    if not config['project']:raise ValueError('GOOGLE_CLOUD_PROJECT required in .env')
    run=get_run(name,case,config,args.command in ('fresh','fresh-extract'));print('Run:',run,flush=True)
    stop='extract' if args.command=='fresh-extract' else args.command if args.command in STAGES else 'report'
    with run_lock(run):
        for stage in STAGES:
            try:
                if stage_done(run,stage):print(stage+': checkpoint reused',flush=True)
                else:
                    print(stage+': starting',flush=True);execute_stage(stage,run,case,config)
                    print(stage+': complete',flush=True)
            except Exception as exc:
                write_json(run/'failure.json',{'stage':stage,'error':str(exc),'type':type(exc).__name__})
                raise
            if stage==stop:break
        if (run/'failure.json').exists():(run/'failure.json').unlink()
    print('Finished requested stages. Customer output remains a review draft.',flush=True)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(type(exc).__name__+': '+str(exc),file=sys.stderr)
        raise SystemExit(1)
