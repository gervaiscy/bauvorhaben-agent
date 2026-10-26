import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bauvo_working_units.trace import RequestCheckpoints, RawRunRecorder, TracingModel

IDENTITY={'project':'test','location':'global','model':'test-model'}
PROMPT='PAGE_TIMING. original'
RAW=' {"assessments": []} '

class TimingCheckpointTests(unittest.TestCase):
    def legacy(self, folder):
        trace=folder/'working-units.trace';trace.mkdir(parents=True)
        for n, status, raw in [(1,'recorded',RAW),(2,'request_failed','{"unfinished"'),(3,'recorded','null')]:
            prompt=PROMPT if n==1 else 'bad'+str(n)
            (trace/f'{n:04d}-prompt.txt').write_text(prompt)
            (trace/f'{n:04d}-response.json').write_text(raw)
            (trace/f'{n:04d}-attempt.json').write_text(json.dumps({'status':status,
                'finish_reason':'STOP' if n==1 else None,
                'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
                'response_sha256':hashlib.sha256(raw.encode()).hexdigest()}))

    def test_recovery_reuses_only_exact_successful_requests_and_preserves_originals(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';self.legacy(old)
            originals={p:p.read_bytes() for p in old.rglob('*') if p.is_file()}
            calls=[]
            backend=SimpleNamespace(last_raw_response=RAW,last_finish_reason='STOP',
                extract=lambda p:calls.append(p) or {'assessments':[]})
            cache=RequestCheckpoints(backend,root/'new'/'request-checkpoints',IDENTITY,old)
            recorder=RawRunRecorder(cache,root/'new'/'raw.json')
            self.assertEqual(recorder.extract(PROMPT),{'assessments':[]})
            self.assertEqual(recorder.last_raw_response,RAW)
            self.assertEqual(calls,[])
            recorder.extract(PROMPT+' changed')
            self.assertEqual(calls,[PROMPT+' changed'])
            self.assertEqual(originals,{p:p.read_bytes() for p in old.rglob('*') if p.is_file()})
            self.assertEqual(set(cache.saved),{PROMPT})

    def test_interrupt_is_failed_not_recorded_as_null(self):
        with TemporaryDirectory() as tmp:
            def interrupted(prompt):raise KeyboardInterrupt()
            recorder=RawRunRecorder(SimpleNamespace(extract=interrupted),Path(tmp)/'raw.json')
            with self.assertRaises(KeyboardInterrupt):recorder.extract(PROMPT)
            self.assertEqual(json.loads((Path(tmp)/'raw.json').read_text()),[])
            attempt=json.loads((Path(tmp)/'raw.attempts.json').read_text())[0]
            self.assertEqual(attempt['status'],'request_failed')
            self.assertEqual(attempt['error_type'],'KeyboardInterrupt')

    def test_atomic_checkpoint_survives_missing_trace_response_and_rejects_changed_settings(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';(old/'working-units.trace').mkdir(parents=True)
            backend=SimpleNamespace(extract=lambda p:{'assessments':[]},last_raw_response=RAW,last_finish_reason='STOP')
            cache=RequestCheckpoints(backend,old/'working-units.checkpoints',IDENTITY)
            cache.extract(PROMPT)
            recovered=RequestCheckpoints(None,root/'new'/'request-checkpoints',IDENTITY,old)
            self.assertEqual(recovered.extract(PROMPT),{'assessments':[]})
            with self.assertRaisesRegex(ValueError,'settings changed'):
                RequestCheckpoints(None,root/'changed',dict(IDENTITY,model='other'),old)

    def backend(self, calls):
        return SimpleNamespace(last_raw_response=RAW, last_finish_reason='STOP',
            extract=lambda prompt: calls.append(prompt) or json.loads(RAW))

    def test_custom_trace_recovery_and_sibling_output_names(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';calls=[]
            for name in ('contract', 'blb'):
                trace=old/(name+'.trace')
                cache=RequestCheckpoints(self.backend(calls),old/(name+'.checkpoints'),IDENTITY,trace_directory=trace)
                TracingModel(RawRunRecorder(cache,old/(name+'.raw.json')),trace).extract(PROMPT)
            self.assertEqual(len(calls),2)
            with self.assertRaisesRegex(ValueError,'Multiple timing outputs'):
                RequestCheckpoints(None,root/'ambiguous',IDENTITY,old)
            self.assertFalse((root/'ambiguous').exists())
            for index,source in enumerate((old/'contract.json',old/'contract.checkpoints')):
                cache=RequestCheckpoints(None,root/f'recovered-{index}',IDENTITY,source)
                self.assertEqual(cache.extract(PROMPT),json.loads(RAW))
            # A run with a single output discovers its recorded custom trace automatically.
            single=root/'single'
            cache=RequestCheckpoints(self.backend(calls),single/'custom.checkpoints',IDENTITY,
                                     trace_directory=single/'custom.trace')
            cache.extract(PROMPT)
            recovered=RequestCheckpoints(None,root/'single-recovered',IDENTITY,single)
            self.assertEqual(recovered.extract(PROMPT),json.loads(RAW))

    def test_modified_checkpoint_response_is_rejected_without_touching_evidence(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old'
            cache=RequestCheckpoints(self.backend([]),old/'custom.checkpoints',IDENTITY)
            cache.extract(PROMPT)
            path=next(p for p in cache.directory.glob('*.json') if p.name!='manifest.json')
            record=json.loads(path.read_text());record['raw']='{"assessments": []}\n'
            path.write_text(json.dumps(record));changed=path.read_bytes()
            with self.assertRaisesRegex(ValueError,'response checksum mismatch'):
                RequestCheckpoints(None,root/'recovery',IDENTITY,old)
            self.assertEqual(path.read_bytes(),changed)
            self.assertFalse((root/'recovery').exists())

    def test_modified_trace_response_is_rejected_even_with_valid_atomic_checkpoint(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';trace=old/'custom.trace'
            cache=RequestCheckpoints(self.backend([]),old/'custom.checkpoints',IDENTITY,trace_directory=trace)
            TracingModel(RawRunRecorder(cache,old/'raw.json'),trace).extract(PROMPT)
            (trace/'0001-response.json').write_text('{"assessments": []}')
            with self.assertRaisesRegex(ValueError,'response checksum mismatch'):
                RequestCheckpoints(None,root/'recovery',IDENTITY,old)

    def test_unhashed_legacy_response_is_not_replayed(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';self.legacy(old)
            path=old/'working-units.trace/0001-attempt.json'
            meta=json.loads(path.read_text());meta.pop('response_sha256');path.write_text(json.dumps(meta))
            cache=RequestCheckpoints(None,root/'recovery',IDENTITY,old)
            self.assertNotIn(PROMPT,cache.saved)
            self.assertEqual(cache.unverified,1)

    def test_identity_validation_does_not_poison_destination(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old'
            RequestCheckpoints(None,old/'custom.checkpoints',IDENTITY)
            dest=root/'recovery'
            with self.assertRaisesRegex(ValueError,'settings changed'):
                RequestCheckpoints(None,dest,dict(IDENTITY,model='changed'),old)
            self.assertFalse(dest.exists())
            RequestCheckpoints(None,dest,IDENTITY,old)

    def test_cli_two_outputs_in_one_folder_and_resume_custom_filename(self):
        from test_schema6_flow import fixture
        from bauvo_working_units.project_flow import load_documents
        from bauvo_working_units.page_flow import extract_page_units
        from bauvo_working_units.timing_stage import main
        with TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root,count=1)
            docs=load_documents(root/'pages.json',root/'documents.json')
            text=docs[0].pages[0]['text']
            def earlier(prompt):
                if prompt.startswith('PAGE_EXTRACTION.'):
                    return {'working_units':[{'action':text,'name':'Task','actor':None,'performed_by':None,
                        'citations':[{'pdf_page':1,'quote':text}]}], 'carry_forward':''}
                return {'offers':[]}
            baseline=extract_page_units(docs,SimpleNamespace(extract=earlier),stop_after='PAGE_OFFERS')
            (root/'baseline.json').write_text(json.dumps(baseline));calls=[]
            class Provider:
                last_raw_response=None
                last_finish_reason='STOP'
                def extract(self,prompt):
                    calls.append(prompt)
                    data=json.loads(prompt.split('SOURCE DATA:\n')[1])
                    response={'assessments':[{'unit_id':u['id'],'answer':'NOT_STATED','citations':[]}
                                             for u in data['working_units']]}
                    self.last_raw_response=json.dumps(response)
                    return response
            for name,resume in [('contract',None),('blb',None),('recovered',root/'contract.json')]:
                argv=['timing','--working-units',str(root/'baseline.json'),'--pages',str(root/'pages.json'),
                    '--documents',str(root/'documents.json'),'--project','test','--output',str(root/(name+'.json'))]
                if resume:argv+=['--resume-from',str(resume)]
                with patch('sys.argv',argv),patch('bauvo_working_units.gemini.GeminiModel',return_value=Provider()):
                    main()
                self.assertTrue((root/(name+'.checkpoints/manifest.json')).is_file())
            self.assertEqual(len(calls),2)
            self.assertEqual(json.loads((root/'contract.json').read_text()),
                             json.loads((root/'recovered.json').read_text()))
