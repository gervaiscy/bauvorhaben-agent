#!/usr/bin/env python3
"""Run the Gemini page transcription pipeline over each PDF under a local directory or GCS prefix."""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

def inputs(source):
    if source.startswith('gs://'):
        paths=subprocess.check_output(['gcloud','storage','ls','--recursive',source.rstrip('/')+'/'],text=True).splitlines()
        return [x for x in paths if x.lower().endswith('.pdf')]
    return [str(p) for p in sorted(Path(source).rglob('*.pdf'))]

def output_name(source):
    stem=Path(source).stem
    return re.sub('[^A-Za-z0-9_-]+','-',stem).strip('-')[:60]+'-'+hashlib.sha256(source.encode()).hexdigest()[:8]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-prefix',required=True,help='Local directory or private gs:// prefix')
    p.add_argument('--output-root',required=True,help='Local output directory')
    p.add_argument('--publish',help='Optional private gs:// prefix for results')
    p.add_argument('--project',required=True)
    p.add_argument('--staging',help='Legacy option; no staging upload is needed')
    p.add_argument('--location',default='global')
    p.add_argument('--document-boundary-model','--model',dest='model',default='gemini-3.5-flash',help='Model for optional document-boundary proposals')
    p.add_argument('--ocr-model',default='gemini-3.5-flash')
    p.add_argument('--resume',action='store_true',help='Skip contracts whose local quality-report.json already exists')
    args=p.parse_args()
    root=Path(args.output_root); root.mkdir(parents=True,exist_ok=True)
    found=inputs(args.input_prefix)
    if not found: raise SystemExit('No PDFs found')
    results=[]
    for source in found:
        name=output_name(source); out=root/name
        if args.resume and (out/'quality-report.json').exists():
            result={'source':source,'status':'already_done','output':str(out)}
        else:
            cmd=[sys.executable,str(Path(__file__).resolve().parents[1]/'pipeline/pdf-extractor/ocr_pipeline.py'),'--input',source,'--output',str(out),'--project',args.project,'--location',args.location]
            cmd+=['--ocr-model',args.ocr_model]
            with tempfile.TemporaryDirectory() as temporary:
                if source.startswith('gs://'):
                    local=Path(temporary)/'source.pdf'
                    subprocess.run(['gcloud','storage','cp',source,str(local)],check=True)
                    cmd[cmd.index('--input')+1]=str(local)
                completed=subprocess.run(cmd)
                if completed.returncode == 0:
                    completed=subprocess.run([sys.executable,
                        str(Path(__file__).resolve().parents[1]/'pipeline/extraction_support/document_pipeline.py'),
                        '--input',str(out/'pages.json'),'--output',str(out),'--project',args.project,
                        '--location',args.location,'--document-boundary-model',args.model])
            result={'source':source,'status':'completed' if completed.returncode==0 else 'failed','output':str(out),'exit_code':completed.returncode}
        if args.publish and result['status']!='failed':
            for file in out.glob('*.json'):
                subprocess.run(['gcloud','storage','cp',str(file),args.publish.rstrip('/')+'/'+name+'/'+file.name],check=True)
        results.append(result)
        (root/'batch-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+chr(10))
    print('contracts',len(results),'completed',sum(x['status'] in ('completed','already_done') for x in results),'failed',sum(x['status']=='failed' for x in results))
    if any(x['status']=='failed' for x in results): raise SystemExit(1)

if __name__=='__main__': main()
