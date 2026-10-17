from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_tesseract as runner
import pymupdf

class TesseractRunTests(unittest.TestCase):
    def test_local_run_emits_page_sources_evidence_and_review_without_cloud_or_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);models=root/'models';models.mkdir()
            for lang in ('deu','eng'):(models/(lang+'.traineddata')).write_bytes(b'test model')
            pdf=root/'source.pdf'
            with pymupdf.open() as document:
                p=document.new_page();p.insert_text((30,30),'VISIBLE');p.insert_text((30,60),'HIDDEN',render_mode=3);document.save(pdf)
            tsv='level\tblock_num\tpar_num\tline_num\tleft\ttop\twidth\theight\tconf\ttext\n5\t1\t1\t1\t10\t10\t100\t30\t99\tVISIBLE\n'
            out=root/'out';argv=['run_tesseract','--input',str(pdf),'--output',str(out),'--tessdata',str(models)]
            with patch.object(sys,'argv',argv),patch.object(runner.subprocess,'run',return_value=Mock(stdout='tesseract test\n')),patch.object(runner.bench,'tesseract',return_value={'tsv':tsv,'seconds':.1}),patch.object(runner.bench.GoogleDocumentOCR,'process',side_effect=AssertionError('No cloud requests')):
                runner.main()
            pages=json.loads((out/'pages.json').read_text());self.assertEqual(pages['pages'][0]['text'],'VISIBLE\n')
            self.assertNotIn('HIDDEN',pages['pages'][0]['text'])
            self.assertTrue((out/pages['pages'][0]['raw_ocr_file']).is_file())
            self.assertTrue((out/'page-images/page-0001.png').is_file())
            self.assertTrue((out/'preview-images/page-0001.png').is_file())
            review=(out/'source-review.html').read_text()
            self.assertIn('loading="lazy"',review)
            self.assertIn('preview-images/page-0001.png',review)
            self.assertIn('href="page-images/page-0001.png"',review)
            quality=json.loads((out/'quality-report.json').read_text());self.assertEqual(quality['status'],'needs_review')
            self.assertIn('checkbox_and_handwriting_detection_not_supported',[f['code'] for f in quality['findings']])
            self.assertEqual(json.loads((out/'run-summary.json').read_text())['api_calls'],0)
            self.assertFalse((out/'extraction-approvals').exists())
