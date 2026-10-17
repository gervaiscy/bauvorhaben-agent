#!/usr/bin/env python3
"""Cached transport for document-boundary proposals ."""
import hashlib
import json
import time
from pathlib import Path


def save_json(path,data):
    from common import save
    save(path,data)

def gemini_sdk(project, location, model, parts):
    """Use ADC and schema-constrained text requests; no storage bucket is needed."""
    from google import genai
    from google.genai import types
    from document_grouping import RESPONSE_SCHEMA
    client=genai.Client(vertexai=True,project=project,location=location,
                       http_options=types.HttpOptions(timeout=600_000))
    converted=[types.Part.from_text(text=part['text']) for part in parts]
    for attempt in range(4):
        try:
            response=client.models.generate_content(model=model,contents=converted,
                config=types.GenerateContentConfig(temperature=0,max_output_tokens=32768,
                    response_mime_type='application/json',response_json_schema=RESPONSE_SCHEMA,thinking_config=types.ThinkingConfig(thinking_budget=1024)))
            if response.candidates and str(response.candidates[0].finish_reason).split('.')[-1]!='STOP':
                raise RuntimeError('Incomplete PDF model response: '+str(response.candidates[0].finish_reason))
            return json.loads(response.text)
        except Exception as exc:
            if getattr(exc,'code',None) not in (429,500,502,503,504) or attempt==3:raise
            time.sleep(2**attempt)
    raise AssertionError('unreachable')

def cached_group_call(args, parts, output, invoke):
    """Cache complete grouping answers by request; retain invalid boundary answers for repair."""
    from document_grouping import VERSION, RESPONSE_SCHEMA
    request={'project':args.project,'location':args.location,'model':args.model,
             'protocol':VERSION,'response_schema':RESPONSE_SCHEMA,'parts':parts}
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
