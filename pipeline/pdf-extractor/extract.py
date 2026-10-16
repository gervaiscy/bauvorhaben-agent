#!/usr/bin/env python3
"""Visible-page OCR entry point and cached document-boundary transport."""
import hashlib
import json
import time
from pathlib import Path


def save_json(path,data):
    from document_ocr import save
    save(path,data)

def gemini_sdk(project, location, model, parts):
    """Use ADC with inline bytes; local runs need neither gcloud nor a storage bucket."""
    from google import genai
    from google.genai import types
    client=genai.Client(vertexai=True,project=project,location=location,
                       http_options=types.HttpOptions(timeout=600_000))
    converted=[types.Part.from_text(text=part['text']) for part in parts]
    for attempt in range(4):
        try:
            response=client.models.generate_content(model=model,contents=converted,
                config=types.GenerateContentConfig(temperature=0,max_output_tokens=32768,
                    response_mime_type='application/json',thinking_config=types.ThinkingConfig(thinking_budget=1024)))
            if response.candidates and str(response.candidates[0].finish_reason).split('.')[-1]!='STOP':
                raise RuntimeError('Incomplete PDF model response: '+str(response.candidates[0].finish_reason))
            return json.loads(response.text)
        except Exception as exc:
            if getattr(exc,'code',None) not in (429,500,502,503,504) or attempt==3:raise
            time.sleep(2**attempt)
    raise AssertionError('unreachable')

def cached_group_call(args, parts, output, invoke):
    """Cache complete grouping answers by request; retain invalid span answers for repair."""
    request={'project':args.project,'location':args.location,'model':args.model,
             'protocol':'document-boundaries-v5','parts':parts}
    digest=hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    output.mkdir(parents=True,exist_ok=True);path=output/(digest+'.json')
    def response_digest(response):return hashlib.sha256(json.dumps(response,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    candidate=next((p/(digest+'.json') for p in [output,*map(Path,getattr(args,'reuse_grouping_cache',[]))] if (p/(digest+'.json')).is_file()),None)
    if candidate is not None:
        saved=json.loads(candidate.read_text())
        if saved.get('request')!=request or saved.get('request_sha256')!=digest or saved.get('response_sha256')!=response_digest(saved.get('response')):
            raise ValueError('Grouping checkpoint modified')
        if candidate!=path:path.write_bytes(candidate.read_bytes())
        return saved['response']
    answer=invoke()
    save_json(path,{'request_sha256':digest,'request':request,'response':answer,'response_sha256':response_digest(answer)})
    return answer

def parse_pages(spec,count):
    if not spec: return list(range(1,count+1))
    result=set()
    for item in spec.split(','):
        bounds=item.split('-')
        if len(bounds)==1: result.add(int(bounds[0]))
        elif len(bounds)==2: result.update(range(int(bounds[0]),int(bounds[1])+1))
        else: raise ValueError('Bad page selection: '+item)
    return sorted(result)

def main():
    from ocr_pipeline import run
    run()

if __name__=='__main__':main()
