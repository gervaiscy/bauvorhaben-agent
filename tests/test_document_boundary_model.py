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
    def test_cli_uses_35_with_five_page_windows_and_last_document_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'pages.json';seen=[]
            pages=[{'pdf_page':n,'text':f'Title page {n}','text_sha256':__import__('hashlib').sha256(f'Title page {n}'.encode()).hexdigest(),
                    'checkboxes':[]} for n in range(1,13)]
            source.write_text(json.dumps({'schema_version':6,'page_count':12,'pages':pages}))
            before=source.read_bytes()
            def model(project,location,name,parts):
                data=json.loads(parts[0]['text'].split('SOURCE DATA:\n')[1]);seen.append((name,data))
                return {'pages':[{'pdf_page':p['pdf_page'],'starts_new_document':p['pdf_page'] in [1,8],
                    'document_title':'Contract' if p['pdf_page']<8 else 'Offer',
                    'evidence_quotes':[p['text']] if p['pdf_page'] in [1,8] else [],
                    'reason':'Visible form title','uncertain':False,'boundary_within_page':False} for p in data['pages']]}
            argv=['documents','--input',str(source),'--project','test']
            with patch.object(sys,'argv',argv),patch.object(grouping_client,'gemini_sdk',side_effect=model):
                document_pipeline.run()
            self.assertEqual([name for name,_ in seen],['gemini-3.5-flash']*3)
            self.assertEqual([len(data['pages']) for _,data in seen],[5,5,2])
            self.assertEqual([None if data['previous_document'] is None else data['previous_document']['document_title'] for _,data in seen],
                             [None,'Contract','Offer'])
            self.assertEqual(source.read_bytes(),before)
            docs=json.loads((root/'documents.json').read_text())['documents']
            self.assertEqual([d['pdf_pages'] for d in docs],[list(range(1,8)),list(range(8,13))])

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
