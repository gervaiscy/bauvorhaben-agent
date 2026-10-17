import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import pymupdf
import ocr_pipeline as extract
import grouping_client
import document_pipeline
import gemini_ocr as ocr
from document_ocr import GoogleDocumentOCR, digest
from ocr_quality import report


def response(text='35 €/m²\n☑ Ja', reason='STOP'):
    return {'model_version': 'gemini-3.5-flash', 'candidates': [{'finish_reason': reason,
        'content': {'parts': [{'text': 'Private reasoning', 'thought': True}, {'text': text}]}}],
        'usage_metadata': {'prompt_token_count': 1325, 'candidates_token_count': 20}}


class GeminiOCRTests(unittest.TestCase):
    def test_request_contains_only_prompt_and_one_png_with_minimal_reasoning(self):
        client = Mock();client.models.generate_content.return_value.model_dump.return_value = response()
        provider = ocr.GeminiImageOCR('project', client=client)
        raw = provider.process(b'page pixels')
        kw = client.models.generate_content.call_args.kwargs
        self.assertEqual(kw['model'], 'gemini-3.5-flash')
        self.assertEqual(len(kw['contents']), 2)
        self.assertEqual(kw['contents'][0].text, ocr.PROMPT)
        self.assertEqual(kw['contents'][1].inline_data.data, b'page pixels')
        self.assertEqual(kw['contents'][1].inline_data.mime_type, 'image/png')
        self.assertEqual(kw['config'].thinking_config.thinking_level, 'MINIMAL')
        self.assertIsNone(kw['config'].thinking_config.thinking_budget)
        self.assertEqual(kw['config'].media_resolution, 'MEDIA_RESOLUTION_HIGH')
        self.assertEqual(kw['config'].response_mime_type, 'text/plain')
        self.assertEqual(ocr.transcription(raw), '35 €/m²\n☑ Ja')

    def test_exact_page_text_and_checkbox_offsets_without_derived_lines(self):
        raw = response('Heading\r\n\r\n  35 €/m²\t12 Wochen  \n☑ Ja ☐ Nein [uncertain checkbox]\n')
        before = copy.deepcopy(raw);page = ocr.normalize(raw, 4, 'image-hash', 'ocr-cache/raw.json')
        self.assertEqual(page['text'], raw['candidates'][0]['content']['parts'][1]['text'])
        self.assertNotIn('lines',page)
        self.assertEqual(page['ocr_schema_version'],6)
        for mark in page['checkboxes']:
            start,end=mark['text_segments'][0]
            self.assertIn(page['text'][start:end],['☑','☐','[uncertain checkbox]'])
        self.assertEqual([c['state'] for c in page['checkboxes']], ['checked','unchecked','uncertain'])
        self.assertFalse(page['coordinates_available']);self.assertFalse(page['confidence_available'])
        self.assertEqual(raw, before)

    def test_unknown_scores_do_not_become_confidence_failures_but_descriptions_are_flagged(self):
        page = ocr.normalize(response('Clause 12 Wochen\n[logo]\n[illegible]'),1,'img','ocr-cache/raw.json')
        docs = {'documents':[{'document_id':'D0001','pdf_pages':[1]}], 'page_assignments':[{'pdf_page':1,'document_id':'D0001'}]}
        quality = report([page], docs, 1)
        self.assertEqual({f['code'] for f in quality['findings']}, {'possible_nonliteral_transcription','uncertain_transcription'})
        self.assertIn('[logo]', page['text'])
        clean = ocr.normalize(response('Clause 12 Wochen'),1,'img','ocr-cache/raw.json')
        docs = {'documents':[{'document_id':'D0001','pdf_pages':[1]}], 'page_assignments':[{'pdf_page':1,'document_id':'D0001'}]}
        self.assertEqual(report([clean],docs,1)['status'], 'awaiting_source_review')

    def test_empty_normal_finish_is_preserved_for_explicit_blank_page_review(self):
        page = ocr.normalize(response(''),2,'image','ocr-cache/raw.json')
        self.assertNotIn('lines',page);self.assertEqual(page['text'], '')
        self.assertIn('empty_ocr_page', [f['code'] for f in report([page],{'documents':[]},2)['findings']])
        with self.assertRaisesRegex(ValueError,'Incomplete'):
            ocr.transcription({'candidates':[]})
        with self.assertRaisesRegex(ValueError,'text-only'):
            ocr.transcription({'candidates':[{'finish_reason':'STOP','content':{'parts':[{'function_call':{}}]}}]})

    def test_cache_is_pixel_model_settings_bound_and_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp);provider = ocr.GeminiImageOCR('p');provider.process = Mock(return_value=response())
            _,path = ocr.cached_transcription(provider,b'one',folder)
            ocr.cached_transcription(provider,b'one',folder);self.assertEqual(provider.process.call_count,1)
            ocr.cached_transcription(provider,b'two',folder);self.assertEqual(provider.process.call_count,2)
            provider.identity['generation']['temperature'] = .1
            ocr.cached_transcription(provider,b'one',folder);self.assertEqual(provider.process.call_count,3)
            provider.identity['generation']['temperature'] = 0
            changed = ocr.GeminiImageOCR('p',model='other');changed.process = provider.process
            ocr.cached_transcription(changed,b'one',folder);self.assertEqual(provider.process.call_count,4)
            saved=json.loads(path.read_text());saved['response']['candidates'][0]['content']['parts'][-1]['text']='changed';path.write_text(json.dumps(saved))
            with self.assertRaisesRegex(ValueError,'modified'):
                ocr.cached_transcription(provider,b'one',folder)

    def test_incomplete_attempt_is_immutable_and_explicit_resume_can_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);provider=ocr.GeminiImageOCR('p')
            provider.process=Mock(side_effect=[response('partial','MAX_TOKENS'),response('Complete')])
            with self.assertRaisesRegex(ValueError,'Incomplete'):
                ocr.cached_transcription(provider,b'pixels',folder)
            attempt=next(folder.glob('*-attempt-1.json'));before=attempt.read_bytes()
            raw,path=ocr.cached_transcription(provider,b'pixels',folder)
            self.assertEqual(ocr.transcription(raw),'Complete');self.assertEqual(attempt.read_bytes(),before)
            self.assertTrue(path.is_file());self.assertEqual(len(list(folder.glob('*-attempt-*.json'))),2)

    def test_read_through_cache_reuses_exact_transcription_and_raw_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);provider=ocr.GeminiImageOCR('p');provider.process=Mock(return_value=response())
            _,prior=ocr.cached_transcription(provider,b'pixels',root/'old')
            _,new=ocr.cached_transcription(provider,b'pixels',root/'new',[root/'old'])
            self.assertEqual(prior.read_bytes(),new.read_bytes());self.assertEqual(provider.process.call_count,1)


class GeminiPageWorkflowTests(unittest.TestCase):
    def test_all_images_exist_before_first_request_and_incomplete_text_never_reaches_grouping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'input.pdf';out=root/'out'
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((50,50),'First')
                pdf.new_page().insert_text((50,50),'Second');pdf.save(source)
            def incomplete(image):
                inventory=json.loads((out/'page-images.json').read_text())
                self.assertEqual([p['pdf_page'] for p in inventory['images']],[1,2])
                self.assertTrue(all((out/p['image_file']).is_file() for p in inventory['images']))
                return response('partial','MAX_TOKENS')
            argv=['extract','--input',str(source),'--output',str(out),'--project','p']
            with patch.object(sys,'argv',argv),patch.object(ocr.GeminiImageOCR,'process',side_effect=incomplete),patch.object(ocr.GeminiImageOCR,'close') as close,patch.object(grouping_client,'gemini_sdk') as boundary:
                with self.assertRaisesRegex(ValueError,'Incomplete'):extract.run()
                close.assert_called_once();boundary.assert_not_called()
            self.assertFalse((out/'pages.json').exists())
            self.assertTrue(list((out/'ocr-cache').glob('*-attempt-1.json')))

    def test_optional_document_boundaries_are_separate_text_only_requests(self):
        from test_document_grouping import payload,decision
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'input.pdf';out=root/'out';seen=[]
            with pymupdf.open() as pdf:pdf.new_page().insert_text((50,50),'Visible');pdf.save(source)
            def boundary(*args):
                seen.append(args);data=payload(args[-1])
                return {'pages':[decision(p,start=True,title='Contract') for p in data['pages']]}
            argv=['extract','--input',str(source),'--output',str(out),'--project','p']
            with patch.object(sys,'argv',argv),patch.object(ocr.GeminiImageOCR,'process',return_value=response('Visible clause')),patch.object(grouping_client,'gemini_sdk') as api:
                extract.run()
                api.assert_not_called()
            before=(out/'pages.json').read_bytes()
            self.assertFalse((out/'documents.json').exists())
            document_argv=['documents','--input',str(out/'pages.json'),'--project','p','--document-boundary-model','analysis-model']
            with patch.object(sys,'argv',document_argv),patch.object(grouping_client,'gemini_sdk',side_effect=boundary),patch.object(ocr.GeminiImageOCR,'process') as ocr_api:
                document_pipeline.run()
                ocr_api.assert_not_called()
            self.assertEqual((out/'pages.json').read_bytes(),before)
            self.assertEqual(len(seen),1);self.assertEqual(seen[0][2],'analysis-model')
            self.assertTrue(all(set(part)=={'text'} for part in seen[0][-1]))
            self.assertIn('Visible clause',seen[0][-1][0]['text'])
            self.assertEqual(json.loads((out/'pages.json').read_text())['request_binding']['ocr']['model'],'gemini-3.5-flash')
            # Existing grouping/review algorithms must produce identical derived artifacts.
            from types import SimpleNamespace
            from document_grouping import group_documents
            from ocr_quality import review_html
            pages=json.loads(before)['pages']
            saved=json.loads((out/'documents.json').read_text())
            args=SimpleNamespace(project='p',location='global',model='analysis-model',document_boundaries='auto')
            expected=group_documents(args,pages,lambda *a:saved['attempts'][0]['response'])
            self.assertEqual(saved,expected)
            quality=report(pages,expected,1)
            self.assertEqual(json.loads((out/'quality-report.json').read_text()),quality)
            self.assertEqual((out/'source-review.html').read_text(),review_html(pages,expected,quality))
            # Changing document processing settings never invokes OCR or changes pages.json.
            with patch.object(sys,'argv',document_argv+['--document-boundaries','off']),patch.object(grouping_client,'gemini_sdk') as api,patch.object(ocr.GeminiImageOCR,'process') as ocr_api:
                document_pipeline.run();api.assert_not_called();ocr_api.assert_not_called()
            self.assertEqual((out/'pages.json').read_bytes(),before)


    def test_default_rasterizes_every_page_and_preserves_source_order_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'input.pdf';out=root/'out'
            with pymupdf.open() as pdf:
                p=pdf.new_page();p.insert_text((50,50),'Visible');p.insert_text((50,90),'HIDDEN',render_mode=3)
                pdf.new_page();pdf.new_page().insert_text((50,50),'Third');pdf.save(source)
            argv=['extract','--input',str(source),'--output',str(out),'--project','p','--resume']
            with patch.object(sys,'argv',argv),patch.object(ocr.GeminiImageOCR,'process',side_effect=[response('Visible ☑ Ja'),response(''),response('Third 35 €/m²')]) as model,patch.object(GoogleDocumentOCR,'process') as google,patch.object(grouping_client,'gemini_sdk') as boundaries:
                extract.run()
                self.assertEqual(model.call_count,3);google.assert_not_called();boundaries.assert_not_called()
                self.assertTrue(all(call.args[0].startswith(b'\x89PNG') for call in model.call_args_list))
            pages=json.loads((out/'pages.json').read_text())
            self.assertEqual([p['pdf_page'] for p in pages['pages']],[1,2,3]);self.assertEqual(pages['pages'][1]['text'],'')
            self.assertEqual(pages['schema_version'],6)
            self.assertTrue(all('lines' not in p for p in pages['pages']))
            self.assertNotIn('boundaries',pages['request_binding'])
            for name in ('documents.json','quality-report.json','source-review.html','duplicate-pages.json','grouping-cache'):
                self.assertFalse((out/name).exists())
            self.assertNotIn('HIDDEN',json.dumps(pages))
            for page in pages['pages']:
                self.assertEqual(hashlib.sha256((out/page['image_file']).read_bytes()).hexdigest(),page['image_sha256'])
                self.assertEqual(hashlib.sha256((out/page['raw_ocr_file']).read_bytes()).hexdigest(),page['raw_ocr_sha256'])
            before=(out/'pages.json').read_bytes()
            with patch.object(sys,'argv',argv),patch.object(ocr.GeminiImageOCR,'process') as model:
                extract.run();model.assert_not_called()
            self.assertEqual((out/'pages.json').read_bytes(),before)
            with patch.object(sys,'argv',argv+['--ocr-model','another-model']):
                with self.assertRaisesRegex(ValueError,'configuration/source changed'):extract.run()
            self.assertFalse(any(out.glob('*approval*')))
            for name in ['sections.json','blocks.json','lines.json']:self.assertFalse((out/name).exists())

    def test_hidden_pdf_text_does_not_change_pixels_sent_to_ocr(self):
        with pymupdf.open() as a,pymupdf.open() as b:
            p=a.new_page();p.insert_text((50,50),'Visible')
            q=b.new_page();q.insert_text((50,50),'Visible');q.insert_text((50,90),'SECRET',render_mode=3)
            self.assertEqual(p.get_pixmap(dpi=300,alpha=False).tobytes('png'),q.get_pixmap(dpi=300,alpha=False).tobytes('png'))


class ExtractorCleanupTests(unittest.TestCase):
    def test_production_directory_contains_only_two_source_files(self):
        root = Path(__file__).resolve().parents[2]
        files = {p.name for p in (root/'pipeline/pdf-extractor').iterdir() if p.is_file()}
        self.assertEqual(files, {'ocr_pipeline.py', 'gemini_ocr.py'})

    def test_production_entry_point_imports_without_experimental_ocr_modules(self):
        import subprocess
        root = Path(__file__).resolve().parents[2]
        program = """
import sys
sys.path.insert(0, sys.argv[1])
import ocr_pipeline
assert not {'document_ocr', 'ocr_repairs', 'tesseract_pages', 'google_pipeline', 'experimental_extract', 'document_grouping', 'grouping_client', 'ocr_quality', 'document_pipeline', 'common'} & sys.modules.keys()
assert ocr_pipeline.DEFAULT_MODEL == 'gemini-3.5-flash'
"""
        result = subprocess.run([sys.executable, '-B', '-c', program, str(root/'pipeline/pdf-extractor')],
                                capture_output=True, text=True, cwd=root)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, '-B', str(root/'pipeline/pdf-extractor/ocr_pipeline.py'), '--help'],
                                capture_output=True, text=True, cwd=root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('gemini-3.5-flash', result.stdout)
        self.assertNotIn('google-document-ai', result.stdout)
        self.assertNotIn('--ocr-processor', result.stdout)
        self.assertNotIn('--document-boundaries',result.stdout)
        self.assertNotIn('--reuse-grouping-cache',result.stdout)
        self.assertNotIn('--model ',result.stdout)

    def test_extraction_fingerprint_tracks_support_but_ignores_experiments_and_tests(self):
        from cache_binding import stage_binding
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            support = root/'pipeline/extraction_support/document_grouping.py'
            support.parent.mkdir(parents=True);support.write_text('boundary implementation')
            before = stage_binding(root, 'extract')
            experiment = root/'scripts/ocr_experiments/document_ocr.py'
            experiment.parent.mkdir(parents=True);experiment.write_text('historical experiment')
            test = root/'tests/pdf_extractor/test_gemini_ocr.py'
            test.parent.mkdir(parents=True);test.write_text('offline test')
            self.assertEqual(stage_binding(root, 'extract'), before)
            support.write_text('changed boundary implementation')
            self.assertNotEqual(stage_binding(root, 'extract'), before)


class PageTextQualityTests(unittest.TestCase):
    def test_page_coverage_and_source_text_integrity_are_checked_without_lines(self):
        pages=[ocr.normalize(response('Page one'),1,'image1','raw1'),
               ocr.normalize(response('Page two'),2,'image2','raw2')]
        docs={'documents':[{'document_id':'D0001','pdf_pages':[1,2]}],
              'page_assignments':[{'pdf_page':n,'document_id':'D0001'} for n in [1,2]]}
        self.assertEqual(report(pages,docs,2)['findings'],[])
        self.assertNotIn('line_count',report(pages,docs,2))
        bad=copy.deepcopy(docs);bad['documents'][0]['pdf_pages']=[2,1]
        self.assertIn('document_page_coverage',[f['code'] for f in report(pages,bad,2)['findings']])
        bad=copy.deepcopy(docs);bad['page_assignments'][1]['document_id']='invented'
        self.assertIn('document_page_assignments',[f['code'] for f in report(pages,bad,2)['findings']])
        pages[0]['text']='Changed text'
        self.assertIn('page_text_changed',[f['code'] for f in report(pages,docs,2)['findings']])
