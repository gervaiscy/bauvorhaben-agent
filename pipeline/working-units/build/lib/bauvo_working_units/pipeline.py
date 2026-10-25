"""Run PDF phases and analysis phases sequentially, preserving each handoff and attempt."""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from .core import digest
from .gemini import GeminiModel
from .page_flow import extract_page_units
from .project_flow import load_documents
from .phases.project_facts.service import collect_project_facts
from .phases.offers.stage import extract_offers
from .phases.timing.stage import extract_timing
from .phases.consequences.stage import extract_consequences
from .phases.applicability.stage import extract_applicability
from .trace import RawRunRecorder, RetryingModel, TracingModel

ROOT = next((path for path in (Path.cwd(), *Path(__file__).resolve().parents)
             if (path/'pipeline/pdf-extractor/ocr_pipeline.py').is_file()),
            Path(__file__).resolve().parents[4])


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_binding():
    paths = [p for folder in (Path(__file__).parent, ROOT/'pipeline/pdf-extractor',
                             ROOT/'pipeline/document-boundaries')
             for p in folder.rglob('*') if p.suffix in ('.py', '.txt')]
    return digest({str(p.relative_to(ROOT)): sha(p) for p in sorted(paths)})


def run(contracts, blbs, output, project, location='global', model_name='gemini-3.5-flash',
        resume=False, model=None, command_runner=None):
    """One phase's saved result is the next phase's input; never resume changed inputs."""
    sources = [{'id':f'{kind}-{i:02d}', 'type':source_type, 'pdf':str(Path(path).expanduser().resolve())}
               for kind, source_type, paths in (('contract','project',contracts),('blb','blb',blbs))
               for i, path in enumerate(paths, 1)]
    if not sources or not contracts:
        raise ValueError('Provide at least one contract PDF; BLB PDFs are optional')
    if any(not Path(s['pdf']).is_file() for s in sources):
        raise ValueError('Every source PDF must exist')
    if len({s['pdf'] for s in sources}) != len(sources):
        raise ValueError('Each PDF must be supplied once, with one source role')
    binding = {'sources':[{**s,'sha256':sha(s['pdf'])} for s in sources],
               'project':project,'location':location,'model':model_name,
               'chunk_pages':3,'overlap_pages':1,'code':code_binding()}
    output = Path(output).resolve()
    manifest_path = output/'pipeline.json'
    if resume:
        manifest = read(manifest_path)
        if manifest.get('binding') != binding:
            raise ValueError('Pipeline inputs/configuration/code changed; start a new output directory')
    else:
        output.mkdir(parents=True, exist_ok=False)
        manifest = {'binding':binding,'status':'running','steps':{}}
        write(manifest_path, manifest)
    command_runner = command_runner or (lambda argv: subprocess.run(argv, check=True))
    backend = model

    def step(key, operation):
        prior = manifest['steps'].get(key)
        if prior and prior['status'] == 'complete':
            path = output/prior['artifact']
            if sha(path) != prior['sha256']:
                raise ValueError('Saved phase output changed: '+key)
            print(key+': reused', flush=True)
            return path
        attempt = 1 if prior is None else prior['attempt']+1
        folder = output/key/f'attempt-{attempt:02d}'
        folder.mkdir(parents=True, exist_ok=False)
        entry = {'status':'running','attempt':attempt}
        manifest['steps'][key] = entry
        manifest['status'] = 'running'
        write(manifest_path, manifest)
        print(key+': starting', flush=True)
        try:
            path = operation(folder)
            result = read(path)
            entry.update(artifact=str(path.relative_to(output)),sha256=sha(path),
                         result_status=result.get('status','recorded'))
            if result.get('status') == 'request_failed':
                raise RuntimeError('Unresolved model requests; phase output and raw attempts preserved')
            entry['status'] = 'complete'
            write(manifest_path, manifest)
            return path
        except Exception as exc:
            entry.update(status='failed',error_type=type(exc).__name__)
            manifest['status'] = 'failed'
            write(manifest_path, manifest)
            raise

    def recorded(folder, operation):
        nonlocal backend
        if backend is None:
            backend = GeminiModel(project, location, model_name)
        recorder = RetryingModel(TracingModel(RawRunRecorder(backend, folder/'raw.json'), folder/'trace'))
        result = operation(recorder)
        path = folder/'result.json'
        write(path, result)
        return path

    pages, boundaries, documents = {}, {}, {}
    for source in sources:
        name = source['id']
        def ocr(folder, source=source):
            command_runner([sys.executable,str(ROOT/'pipeline/pdf-extractor/ocr_pipeline.py'),
                '--input',source['pdf'],'--output',str(folder),'--project',project,
                '--location',location,'--ocr-model',model_name])
            return folder/'pages.json'
        pages[name] = step('01-ocr/'+name, ocr)
    for source in sources:
        name = source['id']
        def boundary(folder, name=name):
            command_runner([sys.executable,str(ROOT/'pipeline/document-boundaries/document_pipeline.py'),
                '--input',str(pages[name]),'--output',str(folder),'--project',project,
                '--location',location,'--document-boundary-model',model_name])
            return folder/'documents.json'
        boundaries[name] = step('02-documents/'+name, boundary)
        documents[name] = [replace(d,source_type=source['type'])
                           for d in load_documents(pages[name],boundaries[name])]
    all_docs = [d for group in documents.values() for d in group]
    facts_path = step('03-project-facts',lambda folder: recorded(folder,lambda recorder:
        collect_project_facts(all_docs,recorder,chunk_pages=3,overlap_pages=1)))
    facts = read(facts_path)

    def context_for(name):
        return [d for source in sources if source['id']!=name and source['type']=='project'
                for d in documents[source['id']]]

    results = {}
    for source in sources:
        name=source['id']
        results[name] = step('04-aufgaben/'+name,lambda folder,name=name:recorded(folder,lambda recorder:
            extract_page_units(documents[name],recorder,chunk_pages=3,overlap_pages=1,
                context_documents=context_for(name),project_facts=facts,stop_after='PAGE_EXTRACTION')))
    phases = (
        ('05-offers',lambda baseline,docs,recorder,context:extract_offers(baseline,docs,recorder)),
        ('06-timing',lambda baseline,docs,recorder,context:extract_timing(
            baseline,docs,recorder,context_documents=context)),
        ('07-consequences',lambda baseline,docs,recorder,context:extract_consequences(
            baseline,docs,recorder,context_documents=context)),
        ('08-applicability',lambda baseline,docs,recorder,context:extract_applicability(
            baseline,docs,recorder,context_documents=context)),
    )
    for phase, operation in phases:
        for source in sources:
            name=source['id'];baseline=read(results[name])
            results[name]=step(phase+'/'+name,lambda folder,name=name,baseline=baseline,operation=operation:
                recorded(folder,lambda recorder:operation(baseline,documents[name],recorder,context_for(name))))
    manifest['outputs'] = {name:str(path.relative_to(output)) for name,path in results.items()}
    manifest['status'] = 'needs_review' if any(s.get('result_status')=='needs_review'
                            for s in manifest['steps'].values()) else 'recorded'
    write(manifest_path, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contract', action='append', required=True, help='Project PDF; repeat for additional signed project documents')
    parser.add_argument('--blb', action='append', default=[], help='General BLB PDF; repeat if needed; excluded from fact collection')
    parser.add_argument('--output', required=True, help='New pipeline directory, or an existing one with --resume')
    parser.add_argument('--project', required=True)
    parser.add_argument('--location', default='global')
    parser.add_argument('--model', default='gemini-3.5-flash')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    result = run(args.contract,args.blb,args.output,args.project,args.location,args.model,args.resume)
    print('Pipeline '+result['status']+': '+str(Path(args.output)/'pipeline.json'))


if __name__ == '__main__':
    main()
