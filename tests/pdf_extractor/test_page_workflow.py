import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import pymupdf
import experimental_extract as extract
import document_ocr as ocr
from legacy_document_grouping import group_documents
from test_document_ocr import fixture

class PageWorkflowTests(unittest.TestCase):
    def run_extraction(self,root,boundaries='auto',model=None):
        source=root/'source.pdf';out=root/'out'
        if not source.exists():
            with pymupdf.open() as pdf:
                page=pdf.new_page();page.insert_text((50,50),'VISIBLE');page.insert_text((50,90),'HIDDEN',render_mode=3);pdf.save(source)
        argv=['extract','--input',str(source),'--output',str(out),'--project','p','--ocr-provider','google-document-ai','--document-boundaries',boundaries,'--resume']
        with patch.object(sys,'argv',argv),patch.object(ocr.GoogleDocumentOCR,'process',return_value=fixture('Visible footer condition\n')) as provider,patch.object(extract,'gemini_sdk',side_effect=model or RuntimeError('service unavailable')) as gemini:
            extract.main()
        return out,provider.call_count,gemini.call_count

    def test_boundary_failure_preserves_complete_ocr_and_usable_page_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            out,calls,_=self.run_extraction(Path(tmp));data=json.loads((out/'pages.json').read_text());page=data['pages'][0]
            self.assertEqual(page['text'],'Visible footer condition\n');self.assertNotIn('HIDDEN',page['text']);self.assertEqual(calls,1)
            self.assertEqual(json.loads((out/'documents.json').read_text())['status'],'failed')
            quality=json.loads((out/'quality-report.json').read_text());self.assertEqual(quality['status'],'awaiting_source_review');self.assertEqual(quality['boundary_findings'],['document_boundaries_unverified'])
            for name in ('sections.json','blocks.json','lines.json','annotations.json'):self.assertFalse((out/name).exists())
            self.assertIn('Complete OCR text',(out/'source-review.html').read_text())
            with pymupdf.open(out/'page-images/page-0001.png') as image:self.assertGreater(image[0].rect.width,1)

    def test_disabled_boundaries_are_model_free_and_resume_reuses_exact_ocr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out,calls,modelcalls=self.run_extraction(root,'off');before=(out/'pages.json').read_bytes()
            self.assertEqual((calls,modelcalls),(1,0));raw=next((out/'ocr-cache').glob('*.json'));evidence=raw.read_bytes()
            _,calls,modelcalls=self.run_extraction(root,'off');self.assertEqual((calls,modelcalls),(0,0));self.assertEqual(raw.read_bytes(),evidence);self.assertEqual((out/'pages.json').read_bytes(),before)
            self.assertEqual(json.loads((out/'documents.json').read_text())['status'],'disabled')

    def test_boundaries_request_only_document_spans_and_fixed_source_ids(self):
        seen=[]
        def model(*args):
            text=args[-1][0]['text'];seen.append(text)
            return {'pages':[{'pdf_page':1,'starts_new_document':True,'document_title':'Contract','evidence_line_ids':['L0001_00001'],'reason':'Visible form title','uncertain':False,'boundary_within_page':False}]}
        with tempfile.TemporaryDirectory() as tmp:
            out,_,_=self.run_extraction(Path(tmp),model=model)
            self.assertEqual(len(seen),1);self.assertNotIn('section_ref',seen[0]);self.assertNotIn('"roles"',seen[0]);self.assertIn('Visible footer condition',seen[0])
            docs=json.loads((out/'documents.json').read_text());self.assertEqual(docs['status'],'proposed');self.assertTrue(docs['attempts'][0]['valid'])

    def test_malformed_boundaries_have_bounded_repairs_and_raw_answers(self):
        page=ocr.normalize(fixture(),1,'hash','ocr-cache/raw.json');raw={'documents':[]};before=copy.deepcopy(page)
        result=group_documents(SimpleNamespace(document_boundaries='auto',project='p',location='global',model='m'),[page],page['lines'],lambda *a:raw)
        self.assertEqual(result['status'],'failed');self.assertEqual(len(result['attempts']),3);self.assertEqual(result['attempts'][0]['response'],raw);self.assertEqual(page,before)

    def test_unassigned_ocr_text_is_preserved_with_ids_without_invented_coordinates(self):
        raw=fixture('Visible\nEXTRA');raw['document']['pages'][0]['lines'][0]['layout']['textAnchor']['textSegments'][0]['endIndex']='8'
        before=copy.deepcopy(raw);page=ocr.normalize(raw,1,'hash','ocr-cache/raw.json')
        self.assertEqual(page['text'],raw['document']['text']);self.assertEqual(page['lines'][1]['text'],'EXTRA');self.assertIsNone(page['lines'][1]['bbox']);self.assertTrue(page['lines'][1]['geometry_missing']);self.assertEqual(raw,before)

    def test_pixel_crop_consensus_reaches_page_text_grouping_and_bound_evidence(self):
        from unittest.mock import Mock
        from document_ocr import digest
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'input.pdf';out=root/'out'
            with pymupdf.open() as pdf:
                p=pdf.new_page();p.insert_text((20,30),'35 EUR/m2');pdf.save(source)
            argv=['extract','--input',str(source),'--output',str(out),'--project','p','--ocr-provider','google-document-ai','--document-boundaries','off','--ocr-recheck-limit','1']
            with patch.object(sys,'argv',argv),patch.object(ocr.GoogleDocumentOCR,'process',side_effect=[fixture('35\\in/m2\n'),fixture('35 €/m2\n'),fixture('35 €/m2\n')]) as process:
                extract.main()
            self.assertEqual(process.call_count,3)
            pages=json.loads((out/'pages.json').read_text());p=pages['pages'][0]
            self.assertEqual(p['text'],'35 €/m2\n');self.assertEqual(p['provider_text'],'35\\in/m2\n')
            self.assertEqual(p['lines'][0]['id'],p['provider_lines'][0]['id'])
            docs=json.loads((out/'documents.json').read_text());self.assertEqual(docs['pages_sha256'],digest(pages['pages']))
            for attempt in p['ocr_rechecks'][0]['attempts']:
                self.assertTrue((out/attempt['image']).is_file());self.assertTrue((out/attempt['raw_ocr_file']).is_file())
                self.assertEqual(hashlib.sha256((out/attempt['image']).read_bytes()).hexdigest(),attempt['image_sha256'])
            self.assertIn('Original provider text',(out/'source-review.html').read_text())
            self.assertFalse(any(out.glob('*approval*')))


    def test_multiwindow_pipeline_preserves_page_snapshot_and_downstream_boundary_coverage(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'pipeline/working-units/src'))
        from bauvo_working_units.core import read_pages,read_boundaries
        from legacy_boundary_fixtures import decision,payload
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.pdf';out=root/'out';seen=[]
            with pymupdf.open() as pdf:
                for n in range(1,8):pdf.new_page().insert_text((50,50),f'Visible form {n}')
                pdf.save(source)
            def model(*args):
                data=payload(args[-1]);seen.append(data)
                return {'pages':[decision(p,start=p['pdf_page'] in [1,6],title='Offer' if p['pdf_page']>=6 else 'Contract') for p in data['pages']]}
            argv=['extract','--input',str(source),'--output',str(out),'--project','p','--ocr-provider','google-document-ai','--document-boundaries','auto']
            with patch.object(sys,'argv',argv),patch.object(ocr.GoogleDocumentOCR,'process',return_value=fixture()),patch.object(extract,'gemini_sdk',side_effect=model):extract.main()
            pages=read_pages(out/'pages.json');docs=read_boundaries(out/'documents.json',pages)
            self.assertEqual([len(w['pages']) for w in seen],[5,2])
            self.assertEqual(seen[1]['previous_document']['document_title'],'Contract')
            self.assertEqual([d['pdf_pages'] for d in docs],[[1,2,3,4,5],[6,7]])
            self.assertEqual([i for d in docs for i in d['line_ids']],[l['id'] for p in pages['pages'] for l in p['lines']])
            self.assertFalse(any(out.glob('*approval*')))
