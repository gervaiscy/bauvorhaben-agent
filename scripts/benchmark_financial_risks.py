#!/usr/bin/env python3
"""Frozen source-only risk comparison; semantics scored only from explicit reviewer labels."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib,itertools,json,random,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
EVAL=ROOT/'evaluation/financial_risks'
VERSION='financial-risk-benchmark-v1'
def read(p):return json.loads(Path(p).read_text())
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n');tmp.replace(p)
def obj(props):return {'type':'object','properties':props,'required':list(props),'additionalProperties':False}
S={'type':'string'}
def arr(t):return {'type':'array','items':t}
RISK=obj({'title':S,'explanation':S,'evidence_refs':arr(S)})
RISKS=obj({'financial_risks':arr(RISK)})
STATEMENTS=obj({'statements':arr(obj({'quote':S,'evidence_ref':S}))})
REVIEWS=obj({'assessments':arr(obj({'candidate_id':S,'decision':{'type':'string','enum':['KEEP','REVISE','EXCLUDE']},'reason':S,'evidence_refs':arr(S),'risk':{'anyOf':[RISK,{'type':'null'}]}}))})

def load_dataset():
 data=read(EVAL/'dataset.json');lock=read(EVAL/'dataset.lock.json')
 if sha(EVAL/'dataset.json')!=lock['dataset_sha256']:raise ValueError('Frozen dataset changed; create a new version')
 ids=[c['id'] for c in data['cases']]
 if len(ids)!=len(set(ids)):raise ValueError('Duplicate cases')
 return data

def model_payload(case):return {'sources':case['sources']}
def windows(case):
 for source in case['sources']:
  lookup={p['pdf_page']:p for p in source['pages']}
  if [n for d in source['documents'] for n in d['pdf_pages']]!=list(lookup):raise ValueError('Invalid ordered document coverage')
  for doc in source['documents']:
   for start in range(0,len(doc['pdf_pages']),2):
    nums=doc['pdf_pages'][start:start+3]
    if start and len(nums)==1:break
    yield {'sources':[{'source_key':source['source_key'],'pages':[lookup[n] for n in nums],'documents':[{**doc,'pdf_pages':nums}]}]}
def refs(payload):return {p['id']:p['text'] for s in payload['sources'] for p in s['pages']}
def validate_risks(result,payload):
 if not isinstance(result,dict) or set(result)!={'financial_risks'} or not isinstance(result['financial_risks'],list):raise ValueError('Invalid risk schema')
 valid=refs(payload)
 for r in result['financial_risks']:
  if not isinstance(r,dict) or set(r)!={'title','explanation','evidence_refs'}:raise ValueError('Invalid risk fields')
  if not all(isinstance(r[k],str) and r[k].strip() for k in ['title','explanation']):raise ValueError('Empty risk text')
  if not isinstance(r['evidence_refs'],list) or not r['evidence_refs'] or any(not isinstance(x,str) or x not in valid for x in r['evidence_refs']):raise ValueError('Unsupported reference')
 return result['financial_risks']
def validate_statements(result,payload):
 if not isinstance(result,dict) or set(result)!={'statements'} or not isinstance(result['statements'],list):raise ValueError('Invalid statements')
 pages=refs(payload)
 for s in result['statements']:
  if not isinstance(s,dict) or set(s)!={'quote','evidence_ref'} or not isinstance(s['quote'],str) or not s['quote'] or s['quote'] not in pages.get(s['evidence_ref'],''):raise ValueError('Statement not exact source text')
 return result['statements']
def reviewed_risks(result,candidates,payload):
 expected={c['id'] for c in candidates};seen=[];output=[]
 for a in result.get('assessments',[]):
  if set(a)!={'candidate_id','decision','reason','evidence_refs','risk'}:raise ValueError('Invalid review')
  seen.append(a['candidate_id'])
  if a['decision'] not in ('KEEP','REVISE','EXCLUDE') or not a['reason'].strip():raise ValueError('Invalid decision')
  if not a['evidence_refs'] or any(r not in refs(payload) for r in a['evidence_refs']):raise ValueError('Invalid review evidence')
  if a['decision']=='EXCLUDE':
   if a['risk'] is not None:raise ValueError('Excluded risk retained')
  else:output.extend(validate_risks({'financial_risks':[a['risk']]},payload))
 if set(seen)!=expected or len(seen)!=len(set(seen)):raise ValueError('Missing or duplicate review coverage')
 return output

def execute_pipeline(case,approach,call):
 payload=model_payload(case)
 if approach=='discovery_review':
  candidates=[]
  for i,window in enumerate(windows(case),1):
   result=call(f'01-discovery-{i:02d}','discover.txt',window,RISKS)
   for risk in validate_risks(result,window):candidates.append({'id':f'C{len(candidates)+1:04d}','risk':risk})
  # Same second-stage opportunity even when local discovery finds no candidates.
  result=call('02-review','review.txt',{'candidates':candidates,**payload},REVIEWS)
  return reviewed_risks(result,candidates,payload)
 if approach=='extract_interpret':
  statements=[]
  for i,window in enumerate(windows(case),1):
   result=call(f'01-extract-{i:02d}','extract.txt',window,STATEMENTS)
   statements.extend(validate_statements(result,window))
  result=call('02-interpret','interpret.txt',{'statements':statements,**payload},RISKS)
  return validate_risks(result,payload)
 raise ValueError('Unknown approach')

class Provider:
 def __init__(self,output,project,location,max_calls):
  self.output=output;self.project=project;self.location=location;self.max_calls=max_calls;self.lock=threading.Lock()
  path=output/'request-ledger.json';self.ledger=read(path) if path.exists() else []
 def call(self,folder,model,stage,prompt_name,payload,schema):
  from google import genai
  from google.genai import types
  attempt=folder/stage;attempt.mkdir()
  request={'prompt':(EVAL/prompt_name).read_text(),'payload':payload,'schema':schema,'model':model,'thinking':'MEDIUM','temperature':1.0,'max_output_tokens':8192}
  write(attempt/'request.json',request)
  with self.lock:
   if len(self.ledger)>=self.max_calls:raise RuntimeError('Request cap reached')
   event={'request_sha256':digest(request),'path':str(attempt),'status':'reserved','started_at':time.time()};self.ledger.append(event);write(self.output/'request-ledger.json',self.ledger)
  client=None
  try:
   client=genai.Client(vertexai=True,project=self.project,location=self.location,http_options=types.HttpOptions(api_version='v1',timeout=180000,retry_options=types.HttpRetryOptions(attempts=1)))
   response=client.models.generate_content(model=model,contents=request['prompt']+'\n\nEingaben (JSON):\n'+json.dumps(payload,ensure_ascii=False),config=types.GenerateContentConfig(response_mime_type='application/json',response_json_schema=schema,thinking_config=types.ThinkingConfig(thinking_level='MEDIUM'),temperature=1.0,max_output_tokens=8192))
   (attempt/'raw-response.json').write_text(response.model_dump_json(indent=2));(attempt/'response.txt').write_text(response.text or '')
   finish=str(response.candidates[0].finish_reason).split('.')[-1]
   with self.lock:event.update(finish_reason=finish,response_sha256=sha(attempt/'raw-response.json'),usage=response.usage_metadata.model_dump(mode='json') if response.usage_metadata else None)
   if finish!='STOP':raise ValueError('Incomplete model response')
   result=json.loads(response.text);write(attempt/'result.json',result)
   with self.lock:event['status']='complete'
   return result
  except Exception as exc:
   with self.lock:event.update(status='failed',error_type=type(exc).__name__)
   raise
  finally:
   if client:client.close()
   with self.lock:write(self.output/'request-ledger.json',self.ledger)

def run(args):
 data=load_dataset();out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
 prompts={p.name:sha(p) for p in EVAL.glob('*.txt')}
 jobs=[]
 for model,approach,repeat,case in itertools.product(args.models,args.approaches,range(1,args.repeats+1),data['cases']):
  jobs.append({'id':f'J{len(jobs)+1:04d}','case_id':case['id'],'model':model,'approach':approach,'repeat':repeat,'status':'prepared'})
 binding={'protocol':VERSION,'dataset_sha256':sha(EVAL/'dataset.json'),'lock_sha256':sha(EVAL/'dataset.lock.json'),'prompts':prompts,'code_sha256':sha(__file__),'models':args.models,'approaches':args.approaches,'repeats':args.repeats,'temperature':1.0,'thinking':'MEDIUM','max_calls':args.max_calls,'project':args.project,'location':args.location}
 write(out/'manifest.json',{'status':'running','binding':binding,'reference_status':data['review_status'],'jobs':jobs});write(out/'frozen-dataset.json',data)
 provider=Provider(out,args.project,args.location,args.max_calls);lock=threading.Lock();cases={c['id']:c for c in data['cases']}
 def save():write(out/'manifest.json',{'status':'running','binding':binding,'reference_status':data['review_status'],'jobs':jobs})
 def execute(job):
  folder=out/'jobs'/job['id'];folder.mkdir(parents=True)
  with lock:job['status']='running';save()
  try:
   risks=execute_pipeline(cases[job['case_id']],job['approach'],lambda stage,prompt,payload,schema:provider.call(folder,job['model'],stage,prompt,payload,schema))
   write(folder/'final.json',{'financial_risks':risks})
   with lock:job.update(status='complete',risk_count=len(risks),final_sha256=sha(folder/'final.json'));save()
  except Exception as exc:
   with lock:job.update(status='failed',error_type=type(exc).__name__);save()
  print(job['id'],job['case_id'],job['model'],job['approach'],job['repeat'],job['status'],flush=True)
 with ThreadPoolExecutor(max_workers=args.workers) as pool:list(pool.map(execute,jobs))
 write(out/'manifest.json',{'status':'diagnostic_complete' if all(j['status']=='complete' for j in jobs) else 'diagnostic_incomplete','binding':binding,'reference_status':data['review_status'],'jobs':jobs})
 packet(out)
 print('Jobs complete',sum(j['status']=='complete' for j in jobs),'of',len(jobs),'provider requests',len(provider.ledger),flush=True)

def packet(out):
 m=read(out/'manifest.json');data=read(out/'frozen-dataset.json');cases={c['id']:c for c in data['cases']};jobs=list(m['jobs']);random.Random(17831).shuffle(jobs)
 records=[];mapping={}
 for i,j in enumerate(jobs,1):
  rid=f'R{i:04d}';mapping[rid]=j['id'];f=out/'jobs'/j['id']/'final.json'
  if j['status']=='complete' and sha(f)!=j['final_sha256']:raise ValueError('Saved output changed')
  records.append({'record_id':rid,'case_id':j['case_id'],'sources':cases[j['case_id']]['sources'],'reference':cases[j['case_id']]['reference'],'output':read(f) if j['status']=='complete' else None,'status':j['status']})
 write(out/'blind-review-packet.json',{'reference_status':data['review_status'],'instructions':'Review source evidence first. Reference answers are drafts; disagreement must be resolved in a new frozen version. No model/approach/repeat labels supplied. Mark every output risk, not just selected examples.','records':records})
 write(out/'private-unblinding-map.json',mapping)
 write(out/'output-grading-template.json',{'reviewer':None,'independent':False,'reference_approval':None,'grades':[{'record_id':r['record_id'],'risk_labels':[{'risk_index':i,'exposure_id':None,'unsupported':None,'missing_conditions':None} for i,_ in enumerate((r['output'] or {}).get('financial_risks',[]))]} for r in records]})
 write(out/'reference-review-template.json',{'reviewer':None,'independent':False,'dataset_sha256':m['binding']['dataset_sha256'],'status':'PENDING','case_decisions':[{'case_id':c['id'],'decision':None,'notes':None} for c in data['cases']]})
 technical=[]
 for model,approach in itertools.product(m['binding']['models'],m['binding']['approaches']):
  group=[j for j in m['jobs'] if j['model']==model and j['approach']==approach];complete=[j for j in group if j['status']=='complete']
  presence=[]
  for c in data['cases']:
   g=[j for j in group if j['case_id']==c['id']]
   if len(g)==m['binding']['repeats'] and all(j['status']=='complete' for j in g):presence.extend(int((a['risk_count']>0)==(b['risk_count']>0)) for a,b in itertools.combinations(g,2))
  technical.append({'model':model,'approach':approach,'completed_jobs':len(complete),'failed_jobs':len(group)-len(complete),'risk_presence_agreement':sum(presence)/len(presence) if presence else None,'note':'Presence agreement is not semantic consistency or correctness.'})
 write(out/'report.json',{'status':'AWAITING_INDEPENDENT_REVIEW','reference_status':data['review_status'],'semantic_metrics':None,'winner':None,'technical_metrics':technical,'limitation':'Synthetic development diagnostic set, not held-out contracts. Failure rates remain visible. No benchmark score or winner inferred from risk counts.'})

def score(out,grading,approval):
 m=read(out/'manifest.json');dataset=read(out/'frozen-dataset.json');cases={c['id']:c for c in dataset['cases']};g=read(grading);approved=read(approval)
 if approved.get('status')!='APPROVED' or not approved.get('reviewer') or not approved.get('independent') or approved.get('dataset_sha256')!=m['binding']['dataset_sha256']:raise ValueError('Independent frozen-reference approval required')
 if {c['case_id'] for c in approved.get('case_decisions',[]) if c.get('decision')=='APPROVE'}!=set(cases):raise ValueError('All references need approval; revisions require new freeze')
 if not g.get('reviewer') or not g.get('independent') or g.get('reference_approval')!=sha(approval):raise ValueError('Independent grading and exact reference-approval hash required')
 mapping=read(out/'private-unblinding-map.json');jobs={j['id']:j for j in m['jobs']};grades=g['grades']
 if len(grades)!=len(mapping) or {x['record_id'] for x in grades}!=set(mapping):raise ValueError('Missing/duplicate graded records')
 rows=[]
 for grade in grades:
  job=jobs[mapping[grade['record_id']]];case=cases[job['case_id']];exposures={e['id']:e for e in case['reference']['exposures']};labels=grade['risk_labels']
  expected_indices=set(range(job.get('risk_count',0))) if job['status']=='complete' else set()
  if len(labels)!=len(expected_indices) or {x['risk_index'] for x in labels}!=expected_indices:raise ValueError('Every output risk needs exactly one label')
  matched=set();unsupported=0;missing=set();relevant=set()
  for label in labels:
   eid=label['exposure_id'];bad=label['unsupported'];lost=label['missing_conditions']
   if type(bad) is not bool or not isinstance(lost,list):raise ValueError('Unfinished semantic grading')
   if bad:
    if eid is not None:raise ValueError('Unsupported warning cannot match exposure')
    unsupported+=1;continue
   if eid not in exposures:raise ValueError('Invalid expected exposure')
   valid={c['id'] for c in exposures[eid]['conditions']}
   if not set(lost)<=valid:raise ValueError('Unknown condition label')
   matched.add(eid);missing.update((eid,c) for c in lost);relevant.update((eid,c) for c in valid)
  rows.append({**{k:job[k] for k in ['case_id','model','approach','repeat','status']},'matched':sorted(matched),'critical_misses':sum(e['critical'] and eid not in matched for eid,e in exposures.items()),'critical_total':sum(e['critical'] for e in exposures.values()),'misses':len(set(exposures)-matched),'unsupported_warnings':unsupported,'output_warnings':len(labels),'conditions_lost':len(missing),'conditions_assessed':len(relevant)})
 metrics=[]
 for model,approach in itertools.product(m['binding']['models'],m['binding']['approaches']):
  rr=[r for r in rows if r['model']==model and r['approach']==approach];cons=[]
  for cid in cases:
   cc=[r for r in rr if r['case_id']==cid and r['status']=='complete']
   if len(cc)!=m['binding']['repeats']:continue
   for a,b in itertools.combinations(cc,2):
    aa=set(a['matched']);bb=set(b['matched']);cons.append(len(aa&bb)/len(aa|bb) if aa|bb else 1.0)
  sums={k:sum(r[k] for r in rr) for k in ['critical_misses','critical_total','misses','unsupported_warnings','output_warnings','conditions_lost','conditions_assessed']}
  metrics.append({'model':model,'approach':approach,**sums,'failed_jobs':sum(r['status']!='complete' for r in rr),'mean_matched_exposure_jaccard':sum(cons)/len(cons) if cons else None})
 write(out/'scored-report.json',{'protocol':VERSION,'reference_approval_sha256':sha(approval),'grading_sha256':sha(grading),'metrics':metrics,'rows':rows,'winner':None,'selection_policy':'Compare critical misses first, then unsupported warnings and conditions lost; do not rank by warning count. No automatic winner or publication.','limitations':'Human labels against this synthetic development set do not establish whole-contract production accuracy; failed jobs count as misses and are reported separately.'})
 return metrics

def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
 r=sub.add_parser('run');r.add_argument('--output',required=True);r.add_argument('--project',required=True);r.add_argument('--location',default='global');r.add_argument('--models',nargs='+',default=['gemini-3.5-flash','gemini-3.1-pro-preview']);r.add_argument('--approaches',nargs='+',choices=['discovery_review','extract_interpret'],default=['discovery_review','extract_interpret']);r.add_argument('--repeats',type=int,default=3);r.add_argument('--workers',type=int,default=8);r.add_argument('--max-calls',type=int,default=400)
 s=sub.add_parser('score');s.add_argument('--output',required=True);s.add_argument('--grading',required=True);s.add_argument('--approval',required=True)
 a=p.parse_args()
 if a.command=='run':
  if a.repeats<2 or a.workers<1 or a.max_calls<1:raise ValueError('Need repeats >=2, workers >=1 and a positive call cap')
  run(a)
 else:score(Path(a.output),a.grading,a.approval)
if __name__=='__main__':main()
