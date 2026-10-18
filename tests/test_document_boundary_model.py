"""Offline integration coverage for the Gemini 3.5 boundary-model switch."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'pipeline/extraction_support'),str(ROOT/'scripts')]
import document_pipeline
import grouping_client
import bauvo


class DocumentBoundaryModelTests(unittest.TestCase):
    def test_cli_generates_and_caches_boundary_only_outputs_without_changing_ocr(self):
        from pdf_extractor.test_document_grouping import decision,payload
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'pages.json';calls=[]
            pages=[{'pdf_page':n,'text':'Consent form\nCompany A' if n<6 else 'Consent form\nCompany B','checkboxes':[]} for n in range(1,8)]
            for p in pages:p['text_sha256']=__import__('hashlib').sha256(p['text'].encode()).hexdigest()
            source.write_text(json.dumps({'schema_version':6,'page_count':7,'pages':pages}));before=source.read_bytes()
            def model(*args):
                data=payload(args[-1]);calls.append(data)
                return {'pages':[decision(p,p['pdf_page'] in (1,6),'Consent form') for p in data['pages']]}
            argv=['documents','--input',str(source),'--project','test']
            with patch.object(sys,'argv',argv),patch.object(grouping_client,'gemini_sdk',side_effect=model):
                document_pipeline.run();document_pipeline.run()
            self.assertEqual(len(calls),2);self.assertEqual(source.read_bytes(),before)
            result=json.loads((root/'documents.json').read_text())
            self.assertEqual([d['pdf_pages'] for d in result['documents']],[list(range(1,6)),[6,7]])
            self.assertEqual(result['metadata_extraction'],'not_performed');self.assertEqual(result['review_flags'],[])
            self.assertEqual(calls[1]['previous_page']['text'],pages[4]['text'])
            self.assertEqual(set(calls[1]['previous_document']),{'document_title','start_pdf_page'})
            self.assertNotIn('sender',result['documents'][0])
            self.assertEqual(len(list((root/'grouping-cache').glob('*.json'))),2)
            self.assertTrue((root/'source-review.html').exists())

    def test_cli_retains_rejected_answer_but_retry_cache_excludes_it(self):
        from pdf_extractor.test_document_grouping import decision,payload
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'pages.json';calls=[]
            page={'pdf_page':1,'text':'Guide','checkboxes':[],'text_sha256':__import__('hashlib').sha256(b'Guide').hexdigest()}
            source.write_text(json.dumps({'schema_version':6,'page_count':1,'pages':[page]}));before=source.read_bytes()
            failed={'pages':[{'bad':'RUNAWAY_SENTINEL'*5000}]}
            def model(*args):
                text=args[-1][0]['text'];calls.append(text)
                if len(calls)==1:return failed
                self.assertNotIn('RUNAWAY_SENTINEL',text)
                return {'pages':[decision(page,True,'Guide')]}
            with patch.object(grouping_client,'gemini_sdk',side_effect=model):
                args=SimpleNamespace(input=str(source),output=None,project='test',location='global',model='gemini-3.5-flash',document_boundaries='auto',reuse_grouping_cache=[])
                document_pipeline.process(args);document_pipeline.process(args)
            result=json.loads((root/'documents.json').read_text())
            self.assertEqual(result['attempts'][0]['response'],failed);self.assertEqual(len(calls),2)
            self.assertIn('VALIDATION ERROR:',calls[1]);self.assertEqual(source.read_bytes(),before)
            self.assertEqual(result['status'],'proposed')

    def test_title_block_warning_persists_without_extra_calls_or_uncertain_boundaries(self):
        from pdf_extractor.test_document_grouping import decision,payload
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'pages.json';calls=[]
            title='Northwind Services Ltd\n\nPrivacy notice'
            pages=[{'pdf_page':n,'text':title if n==1 else 'Continuation','checkboxes':[]} for n in range(1,7)]
            for p in pages:p['text_sha256']=__import__('hashlib').sha256(p['text'].encode()).hexdigest()
            source.write_text(json.dumps({'schema_version':6,'page_count':6,'pages':pages}));before=source.read_bytes()
            def model(*args):
                data=payload(args[-1]);calls.append(data)
                return {'pages':[decision(p,p['pdf_page']==1,' '.join(title.split())) for p in data['pages']]}
            args=SimpleNamespace(input=str(source),output=None,project='test',location='global',model='gemini-3.5-flash',document_boundaries='auto',reuse_grouping_cache=[])
            with patch.object(grouping_client,'gemini_sdk',side_effect=model):document_pipeline.process(args)
            result=json.loads((root/'documents.json').read_text());quality=json.loads((root/'quality-report.json').read_text())
            self.assertEqual(source.read_bytes(),before);self.assertEqual(len(calls),2)
            self.assertEqual(calls[1]['previous_page']['text'],pages[4]['text'])
            self.assertEqual(calls[1]['previous_document']['document_title'],' '.join(title.split()))
            self.assertEqual(result['review_flags'],['title_spans_multiple_blocks:1'])
            self.assertEqual(quality['status'],'needs_review');self.assertFalse(result['documents'][0]['uncertain'])
            self.assertEqual(result['documents'][0]['pdf_pages'],list(range(1,7)))
            self.assertEqual(result['attempts'][0]['response']['pages'][0]['document_title'],' '.join(title.split()))
            self.assertIn('title_spans_multiple_blocks:1',(root/'source-review.html').read_text())

    def test_boundary_override_is_independent_of_analysis_and_transcription(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(bauvo,'ROOT',Path(tmp)),patch.dict(os.environ,{},clear=True):
            (Path(tmp)/'.env').write_text('BAUVO_MODEL=gemini-2.5-flash\nBAUVO_DOCUMENT_BOUNDARY_MODEL=custom-boundary-model\n')
            config=bauvo.load_environment()
            self.assertEqual(config['document_boundary_model'],'custom-boundary-model')
            self.assertEqual(config['model'],'gemini-2.5-flash')
            self.assertEqual(config['ocr_model'],'gemini-3.5-flash')

    def test_cache_binds_model_and_generation_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            args=SimpleNamespace(project='p',location='global',model='gemini-3.5-flash')
            folder=Path(tmp);parts=[{'text':'Source pages'}];calls=[]
            def invoke():calls.append(1);return {'pages':[]}
            grouping_client.cached_group_call(args,parts,folder,invoke)
            grouping_client.cached_group_call(args,parts,folder,invoke)
            self.assertEqual(len(calls),1)
            generation=copy.deepcopy(grouping_client.GENERATION)
            generation['thinking_config']['thinking_level']='LOW'
            with patch.object(grouping_client,'GENERATION',generation):
                grouping_client.cached_group_call(args,parts,folder,invoke)
            self.assertEqual(len(calls),2)
            args.model='different-model'
            grouping_client.cached_group_call(args,parts,folder,invoke)
            self.assertEqual(len(calls),3)
            saved=[json.loads(p.read_text()) for p in folder.glob('*.json')]
            self.assertTrue(all('generation' in p['request'] for p in saved))
