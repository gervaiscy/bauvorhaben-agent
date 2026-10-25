"""Offline end-to-end handoffs, phase isolation, prompt locks and safe pipeline resume."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bauvo_working_units import pipeline
from bauvo_working_units.core import digest
from bauvo_working_units.trace import ModelRequestFailed


class FakeModel:
    def __init__(self, fail_timing=False):
        self.calls=[];self.fail_timing=fail_timing

    def extract(self,prompt):
        kind=prompt.split('.',1)[0];data=json.loads(prompt.split('SOURCE DATA:\n')[1])
        self.calls.append((kind,data))
        page=data['source_pages'][0];citation={'pdf_page':page['pdf_page'],'quote':page['text']}
        if kind=='PAGE_PROJECT_FACTS':
            return {'verpflichtungen':[{'verpflichtung':page['text'],
                    'zitate':[{'pdf_page':page['pdf_page'],'text':page['text']}]}]}
        if kind=='PAGE_EXTRACTION':
            return {'working_units':[{'name':'Bauwasser','action':'Bauwasser bereitstellen',
                    'actor':'Bauherr','performed_by':None,'citations':[citation]}],'carry_forward':''}
        if kind=='PAGE_OFFERS':return {'offers':[]}
        if kind=='PAGE_TIMING' and self.fail_timing:
            raise ModelRequestFailed([{'finish_reason':'MAX_TOKENS'}])
        rows=[]
        for unit in data['working_units']:
            row={'unit_id':unit['id']}
            if kind=='PAGE_TIMING':row.update(status='NOT_STATED',timing_rules=[],missing_evidence=[])
            elif kind=='PAGE_CONSEQUENCES':row.update(status='NOT_STATED',consequences=[],citations=[])
            else:row.update(applicability='APPLIES',reason='Belegte Vereinbarung')
            rows.append(row)
        return {'assessments':rows}


class ModularPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.contract=self.root/'contract.pdf';self.contract.write_bytes(b'%PDF contract fixture')
        self.blb=self.root/'blb.pdf';self.blb.write_bytes(b'%PDF blb fixture')
        self.output=self.root/'run';self.commands=[]

    def command(self,argv):
        self.commands.append(argv)
        folder=Path(argv[argv.index('--output')+1]);folder.mkdir(parents=True,exist_ok=True)
        if Path(argv[1]).name=='ocr_pipeline.py':
            source=Path(argv[argv.index('--input')+1])
            text='Der Bauherr stellt Bauwasser bereit.'
            pages=[{'pdf_page':1,'text':text,'checkboxes':[],
                    'text_sha256':hashlib.sha256(text.encode()).hexdigest()}]
            pipeline.write(folder/'pages.json',{'schema_version':6,'source':str(source),
                           'sha256':pipeline.sha(source),'page_count':1,'pages':pages})
        else:
            pages=pipeline.read(argv[argv.index('--input')+1])['pages']
            pipeline.write(folder/'documents.json',{'schema_version':6,'pages_sha256':digest(pages),
                'status':'proposed','documents':[{'document_id':'D1','document_title':'Document',
                 'pdf_pages':[1],'uncertain':False}],
                'page_assignments':[{'pdf_page':1,'document_id':'D1'}]})

    def run_pipeline(self,model,resume=False):
        return pipeline.run([self.contract],[self.blb],self.output,'unused',resume=resume,
                            model=model,command_runner=self.command)

    def test_all_phases_run_in_order_with_separate_artifacts_and_no_blb_fact_calls(self):
        model=FakeModel();result=self.run_pipeline(model)
        self.assertEqual([k for k,d in model.calls],['PAGE_PROJECT_FACTS']+
            [kind for kind in ('PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY') for _ in range(2)])
        self.assertEqual(len(self.commands),4)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(len(result['steps']),15)
        for name,path in result['outputs'].items():
            final=pipeline.read(self.output/path)
            self.assertEqual(final['completed_passes'],['PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'])
            self.assertEqual(len(final['project_facts']['facts']),1)
            self.assertEqual(len(final['project_facts']['excluded_sources']),1)
            for number in ('04-aufgaben','05-offers','06-timing','07-consequences','08-applicability'):
                artifact=result['steps'][number+'/'+name]['artifact'];folder=(self.output/artifact).parent
                self.assertTrue((folder/'raw.json').is_file())
                self.assertTrue((folder/'trace').is_dir())
            previous=pipeline.read(self.output/result['steps']['07-consequences/'+name]['artifact'])
            self.assertEqual(final['working_units'][0]['reviews'][:-1],previous['working_units'][0]['reviews'])
        before=(self.output/'pipeline.json').read_bytes()
        with self.assertRaises(FileExistsError):self.run_pipeline(model)
        self.assertEqual((self.output/'pipeline.json').read_bytes(),before)
        model.calls.clear();self.commands.clear()
        self.run_pipeline(model,resume=True)
        self.assertEqual(model.calls,[]);self.assertEqual(self.commands,[])

    def test_failed_phase_stops_later_phases_and_resume_preserves_failed_attempt(self):
        model=FakeModel(fail_timing=True)
        with self.assertRaisesRegex(RuntimeError,'Unresolved model requests'):self.run_pipeline(model)
        manifest=pipeline.read(self.output/'pipeline.json')
        self.assertEqual(manifest['status'],'failed')
        self.assertFalse(any(k.startswith('07-') for k in manifest['steps']))
        failed=self.output/manifest['steps']['06-timing/contract-01']['artifact']
        original=failed.read_bytes()
        model.fail_timing=False;model.calls.clear()
        manifest=self.run_pipeline(model,resume=True)
        self.assertEqual(manifest['status'],'recorded')
        self.assertEqual(failed.read_bytes(),original)
        self.assertEqual(manifest['steps']['06-timing/contract-01']['attempt'],2)
        self.assertNotIn('PAGE_PROJECT_FACTS',[k for k,d in model.calls])

    def test_resume_rejects_changed_pdf_code_binding_or_completed_artifact(self):
        for change in ('pdf','code','artifact'):
            with self.subTest(change=change):
                # Separate run per tamper test.
                self.output=self.root/change
                model=FakeModel();manifest=self.run_pipeline(model);model.calls.clear()
                if change=='pdf':self.contract.write_bytes(b'changed')
                elif change=='artifact':
                    path=self.output/manifest['steps']['03-project-facts']['artifact'];path.write_text('{}')
                with patch.object(pipeline,'code_binding',return_value='changed') if change=='code' else patch.object(pipeline,'code_binding',wraps=pipeline.code_binding):
                    with self.assertRaises(ValueError):self.run_pipeline(model,resume=True)
                self.assertEqual(model.calls,[])

    def test_settled_prompt_lock_and_phase_resource_locations(self):
        lock=pipeline.read(pipeline.ROOT/'pipeline/prompts.lock.json')
        self.assertEqual(len(lock),8)
        for path,expected in lock.items():
            self.assertEqual(pipeline.sha(pipeline.ROOT/path),expected,path)
        package=Path(pipeline.__file__).parent
        for name in ('project_facts','aufgaben','offers','timing','consequences','applicability'):
            for filename in ('prompt.txt','schema.py','service.py'):
                self.assertTrue((package/'phases'/name/filename).is_file())

    def test_explicit_pre_analysis_resume_retains_pdf_outputs_and_old_binding(self):
        def uncertain_command(argv):
            self.command(argv)
            if Path(argv[1]).name=='document_pipeline.py':
                folder=Path(argv[argv.index('--output')+1]);path=folder/'documents.json'
                data=pipeline.read(path);data['status']='needs_review';data['documents'][0]['uncertain']=True
                pipeline.write(path,data)
        # Simulate an older run that stopped after its PDF phases, before any model request.
        with patch.object(pipeline,'collect_project_facts',side_effect=RuntimeError('stop before facts')):
            with self.assertRaises(RuntimeError):
                pipeline.run([self.contract],[self.blb],self.output,'unused',model=FakeModel(),
                             command_runner=uncertain_command)
        manifest=pipeline.read(self.output/'pipeline.json')
        manifest['steps'].pop('03-project-facts')
        (self.output/'03-project-facts').rename(self.root/'unused-facts-attempt')
        manifest['binding']['code']='previous-analysis-code'
        manifest['binding'].pop('phase_thinking_levels',None)
        pipeline.write(self.output/'pipeline.json',manifest)
        original_binding=json.loads(json.dumps(manifest['binding']))
        pages=self.output/manifest['steps']['01-ocr/blb-01']['artifact'];before=pages.read_bytes()
        def no_pdf_calls(argv):self.fail('Completed PDF phases must be reused')
        result=pipeline.run([self.contract],[self.blb],self.output,'unused',resume=True,model=FakeModel(),
                            command_runner=no_pdf_calls,accept_analysis_changes=True)
        self.assertEqual(result['binding_history'],[original_binding])
        self.assertEqual(pages.read_bytes(),before)
        final=pipeline.read(self.output/result['outputs']['blb-01'])
        self.assertTrue(final['working_units'][0]['derived']['boundary_uncertain'])
        # Once analysis has run, this override must not reuse results under changed settings.
        with patch.object(pipeline,'code_binding',return_value='different'),self.assertRaises(ValueError):
            pipeline.run([self.contract],[self.blb],self.output,'unused',resume=True,model=FakeModel(),
                         command_runner=no_pdf_calls,accept_analysis_changes=True)
