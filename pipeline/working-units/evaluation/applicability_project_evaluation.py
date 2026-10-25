"""Focused applicability evaluations through the production stage; no model corrections."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from bauvo_working_units.applicability_stage import extract_applicability
from bauvo_working_units.core import digest
from bauvo_working_units.gemini import GeminiModel
from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.project_facts import collect_project_facts
from bauvo_working_units.project_flow import Document
from bauvo_working_units.schemas import GENERATION, PROTOCOL, PHASE_THINKING_LEVELS
from bauvo_working_units.trace import RawRunRecorder, RetryingModel, TracingModel

BASE = Path(__file__).resolve().parent
CASES = BASE / 'applicability-project-context-cases.json'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def prepare(case):
    """Supply controlled tasks/facts; only applicability makes live requests."""
    def document(source, pages, kind):
        return Document('D1', 'Synthetic '+kind, source+'.pdf', schema_version=6,
                        source_key=source, source_snapshot=digest(pages), source_type=kind,
                        pages=tuple(pages))
    blb=document(case['id']+'-blb',[{'pdf_page':1,'text':case['source_text']}],'blb')
    fact_rows=case['contract_facts']
    contract=document(case['id']+'-contract',[
        {'pdf_page':i+1,'text':row['quote']} for i,row in enumerate(fact_rows)],'project')
    context=[contract] if fact_rows else []
    def facts_response(prompt):
        pages=json.loads(prompt.split('SOURCE DATA:\n')[1])['source_pages']
        return {'verpflichtungen':[{'verpflichtung':fact_rows[p['pdf_page']-1]['statement'],
                                   'zitate':[{'pdf_page':p['pdf_page'],'text':p['text']}]} for p in pages]}
    facts=collect_project_facts(context, SimpleNamespace(extract=facts_response))
    def earlier(prompt):
        data=json.loads(prompt.split('SOURCE DATA:\n')[1])
        if prompt.startswith('PAGE_EXTRACTION.'):
            return {'working_units':[{'name':t['action'],'action':t['action'],'actor':None,
                     'performed_by':None,'citations':[{'pdf_page':1,'quote':t.get('quote',case['source_text'])}]}
                     for t in case['tasks']],'carry_forward':''}
        if prompt.startswith('PAGE_OFFERS.'):return {'offers':[]}
        return {'assessments':[{'unit_id':u['id'],'status':'NOT_STATED','timing_rules':[],
                               'missing_evidence':[]} for u in data['working_units']]}
    baseline=extract_page_units([blb],SimpleNamespace(extract=earlier),project_facts=facts,
                                context_documents=context,stop_after='PAGE_TIMING')
    return baseline,[blb],context


def run(output, project, location='global', model=None):
    output=Path(output); output.mkdir(parents=True,exist_ok=False)
    files=[Path(__file__),CASES,*(BASE.parent/'src').rglob('*.py'),*(BASE.parent/'src').rglob('*.txt')]
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    write(output/'run-manifest.json',{'protocol':PROTOCOL,'model':'gemini-3.5-flash',
          'generation':GENERATION,'phase_thinking_levels':PHASE_THINKING_LEVELS,'chunk_pages':3,'overlap_pages':1,'inputs_and_code':hashes})
    backend=model if model is not None else GeminiModel(project,location,'gemini-3.5-flash')
    summary=[]
    for case in json.loads(CASES.read_text())['cases']:
        folder=output/case['id'];folder.mkdir()
        baseline,documents,context=prepare(case)
        write(folder/'expectations.json',case['expectations'])
        write(folder/'baseline.json',baseline)
        traced=RetryingModel(TracingModel(RawRunRecorder(backend,folder/'raw.json'),folder/'trace'))
        print('APPLICABILITY CASE',case['id'],flush=True)
        result=extract_applicability(baseline,documents,traced,context_documents=context)
        write(folder/'result.json',result)
        assessments=[r['gemini'] for u in result['working_units'] for r in u['reviews']
                     if r['kind']=='PAGE_APPLICABILITY']
        comparison=[{'task':task['action'],'expected':{k:v for k,v in expected.items() if k!='modality'},'actual':actual,
                     'status_fields_match':actual.get('applicability')==expected['applicability']}
                    for task,expected,actual in zip(case['tasks'],case['expectations']['units'],assessments)]
        summary.append({'case':case['id'],'status':result['status'],'comparison':comparison,
                        'assessment_count':len(assessments),'expected_count':len(case['tasks']),
                        'review_findings':result['review_findings'],'semantic_review_required':True})
        write(output/'execution-summary.json',summary)
    write(output/'integrity.json',{'inputs_and_code_unchanged':all(
        hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in hashes.items())})
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--project',required=True)
    parser.add_argument('--location',default='global')
    run(**vars(parser.parse_args()))
