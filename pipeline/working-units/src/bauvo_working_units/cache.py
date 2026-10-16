"""Checksummed, prompt-bound cache of complete JSON model responses."""
import copy
import hashlib
import json
from pathlib import Path


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

class CachedModel:
    def __init__(self,model,directory,identity):
        self.model=model;self.directory=Path(directory);self.identity=identity
        self.directory.mkdir(parents=True,exist_ok=True);self.last_raw_response=None

    def extract(self,prompt):
        request={'identity':self.identity,'prompt':prompt};key=digest(request);path=self.directory/(key+'.json')
        self.last_raw_response=None
        if path.exists():
            saved=json.loads(path.read_text())
            if saved['request_sha256']!=key or saved['response_sha256']!=digest({'response':saved['response'],'raw_text':saved.get('raw_text')}):raise ValueError('Working-unit model cache modified')
            print('Working-unit request: cache reused',flush=True)
            self.last_raw_response=saved.get('raw_text')
            return copy.deepcopy(saved['response'])
        print('Working-unit request: Gemini call',flush=True)
        try:answer=self.model.extract(prompt)
        finally:self.last_raw_response=getattr(self.model,'last_raw_response',None)
        if isinstance(answer,(dict,list)):
            saved={'request_sha256':key,'request':request,'response':answer,
                   'response_sha256':digest({'response':answer,'raw_text':self.last_raw_response}),'raw_text':self.last_raw_response}
            temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(saved,ensure_ascii=False));temporary.replace(path)
        print('Working-unit response saved',flush=True)
        return answer
