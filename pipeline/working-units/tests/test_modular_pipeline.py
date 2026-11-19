from consequence_fixtures import assessment_answer
from clause_fixtures import decode_prompt
from clause_fixtures import index_answer
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
        kind=prompt.split('.',1)[0];data=decode_prompt(prompt)
        self.calls.append((kind,data))
        if prompt.startswith('PAGE_CONSEQUENCES.'):
            out = assessment_answer(json.loads(prompt.split('SOURCE DATA:\n', 1)[1]))
            return out
        page=data['source_pages'][0];citation={'pdf_page':page['pdf_page'],'quote':page['text']}
        if kind=='PAGE_PROJECT_FACTS':
            return index_answer(data)
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
        self.page_count=1

    def command(self,argv):
        self.commands.append(argv)
        folder=Path(argv[argv.index('--output')+1]);folder.mkdir(parents=True,exist_ok=True)
        if Path(argv[1]).name=='ocr_pipeline.py':
            source=Path(argv[argv.index('--input')+1])
            text='Der Bauherr stellt Bauwasser bereit.'
            pages=[{'pdf_page':n,'text':text,'checkboxes':[],
                    'text_sha256':hashlib.sha256(text.encode()).hexdigest()}
                   for n in range(1,self.page_count+1)]
            pipeline.write(folder/'pages.json',{'schema_version':6,'source':str(source),
                           'sha256':pipeline.sha(source),'page_count':self.page_count,'pages':pages})
        else:
            pages=pipeline.read(argv[argv.index('--input')+1])['pages']
            pipeline.write(folder/'documents.json',{'schema_version':6,'pages_sha256':digest(pages),
                'status':'proposed','documents':[{'document_id':'D1','document_title':'Document',
                 'pdf_pages':[p['pdf_page'] for p in pages],'uncertain':False}],
                'page_assignments':[{'pdf_page':p['pdf_page'],'document_id':'D1'} for p in pages]})

    def run_pipeline(self,model,resume=False):
        return pipeline.run([self.contract],[self.blb],self.output,'unused',resume=resume,
                            model=model,command_runner=self.command,max_new_calls=1000)

    def test_facts_use_full_contract_while_task_phases_keep_ten_page_windows(self):
        self.page_count=20
        model=FakeModel();manifest=self.run_pipeline(model)
        expected=[list(range(1,11)),list(range(10,20)),[19,20]]
        self.assertEqual(manifest['binding']['chunk_pages'],10)
        self.assertEqual(manifest['binding']['overlap_pages'],1)
        for kind in ('PAGE_PROJECT_FACTS','PAGE_EXTRACTION','PAGE_OFFERS'):
            windows=[[p['pdf_page'] for p in data['source_pages']]
                     for phase,data in model.calls if phase==kind]
            if kind=='PAGE_PROJECT_FACTS':
                self.assertEqual(windows,[list(range(1,21))])
            elif kind=='PAGE_OFFERS':
                # Identical source text/facts share actual requests; provenance is local.
                self.assertEqual(windows,[list(range(1,11)),list(range(11,20)),[20]])
            else:
                self.assertEqual(windows,expected*2)
        for kind,data in model.calls:
            if kind not in ('PAGE_PROJECT_FACTS','PAGE_CONSEQUENCES'):self.assertLessEqual(len(data['source_pages']),10)
        for step in manifest['steps']:
            if not step.startswith(('04-','05-','06-','07-','08-')):
                continue
            artifact=pipeline.read(self.output/manifest['steps'][step]['artifact'])
            self.assertEqual([w['pages'] for w in artifact['windows']],expected)
            self.assertEqual([n for w in artifact['windows'] for n in w['primary_pages']],
                             list(range(1,21)))
            if step.startswith('06-timing/'):
                from bauvo_working_units.phases.timing.artifact import review_view, validation_view
                folder = (self.output/manifest['steps'][step]['artifact']).parent
                self.assertEqual(pipeline.read(folder/'result.json'), review_view(artifact))
                self.assertEqual(pipeline.read(folder/'result.validation.json'), validation_view(artifact))

    def test_consequences_for_every_source_receive_contracts_and_blb_without_reextracting_context(self):
        model=FakeModel();manifest=self.run_pipeline(model)
        expected={digest({'source':str(path.resolve()),'pdf_sha256':pipeline.sha(path)})[:16]
                  for path in (self.contract,self.blb)}
        calls=[data for phase,data in model.calls if phase=='PAGE_CONSEQUENCES']
        self.assertEqual(len(calls),1)  # Identical task/timing/context input replays across PDFs.
        for data in calls:
            self.assertEqual(sum(len(d['pages']) for d in data['source_documents']),1)
            self.assertEqual(data['source_documents'][0]['document'],'B1')
            self.assertTrue(data['project_facts'])
            self.assertTrue(all(set(f)=={'id','text'} for f in data['project_facts']))
            self.assertLessEqual(len(data['working_units']),10)
        for name in ('contract-01','blb-01'):
            before=pipeline.read(self.output/manifest['steps']['06-timing/'+name]['artifact'])
            after=pipeline.read(self.output/manifest['steps']['07-consequences/'+name]['artifact'])
            self.assertEqual(len(after['working_units']),len(before['working_units']))
            self.assertEqual(len(after['context_snapshots']),1)
        self.assertTrue(all('source_documents' not in data for phase,data in model.calls
                            if phase not in ('PAGE_OFFERS','PAGE_CONSEQUENCES')))

    def test_offer_sources_match_extraction_type_and_contract_context_reaches_blb_through_facts(self):
        extra_contract=self.root/'amendment.pdf';extra_contract.write_bytes(b'%PDF amendment')
        extra_blb=self.root/'specification.pdf';extra_blb.write_bytes(b'%PDF specification')
        model=FakeModel()
        manifest=pipeline.run([self.contract,extra_contract],[self.blb,extra_blb],self.output,
                              'unused',model=model,command_runner=self.command,max_new_calls=1000)
        calls=[data for phase,data in model.calls if phase=='PAGE_OFFERS']
        # All four identical page/fact inputs share one actual request. Canonical
        # source identity is applied locally, without copying sources into the wire.
        self.assertEqual(len(calls),1)
        for data in calls:
            self.assertEqual(set(data),{'source_pages','project_facts'})
            self.assertTrue(data['project_facts']['facts'])
            self.assertTrue(data['project_facts']['facts'][0]['evidence'])
            self.assertTrue(all('pages' not in s for s in data['project_facts']['documents']))
        for name in ('contract-01','contract-02','blb-01','blb-02'):
            before=pipeline.read(self.output/manifest['steps']['04-aufgaben/'+name]['artifact'])
            after=pipeline.read(self.output/manifest['steps']['05-offers/'+name]['artifact'])
            self.assertEqual(after['working_units'],before['working_units'])
            self.assertEqual(len(after['context_snapshots']),3)

    def test_resume_rejects_previous_window_size_before_model_calls(self):
        manifest=self.run_pipeline(FakeModel())
        manifest['binding']['chunk_pages']=3
        pipeline.write(self.output/'pipeline.json',manifest)
        model=FakeModel()
        with self.assertRaisesRegex(ValueError,'inputs/configuration/code changed'):
            self.run_pipeline(model,resume=True)
        self.assertEqual(model.calls,[])

    def test_pipeline_sol_switch_routes_only_consequences_to_sol(self):
        from unittest.mock import patch
        gemini = FakeModel(); sol = FakeModel()
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic-not-real'}), \
             patch('bauvo_working_units.pipeline.GeminiModel', return_value=gemini), \
             patch('bauvo_working_units.sol.SolModel', return_value=sol) as constructor:
            result = pipeline.run([self.contract], [self.blb], self.output, 'unused',
                command_runner=self.command, max_new_calls=1000, consequence_provider='sol')
        self.assertTrue(sol.calls)
        self.assertEqual({phase for phase, _ in sol.calls}, {'PAGE_CONSEQUENCES'})
        self.assertNotIn('PAGE_CONSEQUENCES', {phase for phase, _ in gemini.calls})
        constructor.assert_called_once_with('gpt-6.1-sol', 'medium')
        self.assertEqual(result['binding']['consequences']['identity'],
                         {'provider': 'sol', 'model': 'gpt-6.1-sol', 'reasoning': 'medium'})

    def test_all_phases_run_in_order_with_separate_artifacts_and_no_blb_fact_calls(self):
        model=FakeModel();result=self.run_pipeline(model)
        self.assertEqual([k for k,d in model.calls],['PAGE_PROJECT_FACTS']+
            [kind for kind in ('PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY')
             for _ in range(1 if kind in ('PAGE_OFFERS','PAGE_CONSEQUENCES') else 2)])
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
                             command_runner=uncertain_command,max_new_calls=1000)
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
                            command_runner=no_pdf_calls,accept_analysis_changes=True,max_new_calls=1000)
        self.assertEqual(result['binding_history'],[original_binding])
        self.assertEqual(pages.read_bytes(),before)
        final=pipeline.read(self.output/result['outputs']['blb-01'])
        self.assertTrue(final['working_units'][0]['derived']['boundary_uncertain'])
        # Once analysis has run, this override must not reuse results under changed settings.
        with patch.object(pipeline,'code_binding',return_value='different'),self.assertRaises(ValueError):
            pipeline.run([self.contract],[self.blb],self.output,'unused',resume=True,model=FakeModel(),
                         command_runner=no_pdf_calls,accept_analysis_changes=True,max_new_calls=1000)
