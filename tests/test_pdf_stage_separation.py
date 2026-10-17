"""The PDF folder can run independently; wrappers compose the later stage explicitly."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pymupdf

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import extract_pdfs as batch


class PDFStageSeparationTests(unittest.TestCase):
    def test_two_file_extractor_runs_without_any_application_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);module=root/'pdf-extractor';module.mkdir()
            for name in ('ocr_pipeline.py','gemini_ocr.py'):
                shutil.copy2(ROOT/'pipeline/pdf-extractor'/name,module/name)
            source=root/'source.pdf'
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((50,50),'Visible 35 EUR');pdf.save(source)
            program="""
import sys
sys.path.insert(0,sys.argv[1])
import gemini_ocr
import ocr_pipeline
assert not {'common','grouping_client','document_grouping','ocr_quality','document_pipeline'} & sys.modules.keys()
gemini_ocr.GeminiImageOCR.process=lambda self,image: {'candidates':[{'finish_reason':'STOP','content':{'parts':[{'text':'Literal 35 €/m²'}]}}]}
sys.argv=['ocr','--input',sys.argv[2],'--output',sys.argv[3],'--project','test']
ocr_pipeline.run()
"""
            result=subprocess.run([sys.executable,'-B','-c',program,str(module),str(source),str(root/'out')],
                                  capture_output=True,text=True,cwd=root)
            self.assertEqual(result.returncode,0,result.stderr)
            output=root/'out';data=json.loads((output/'pages.json').read_text())
            self.assertEqual(data['pages'][0]['text'],'Literal 35 €/m²')
            self.assertNotIn('lines',data['pages'][0])
            self.assertEqual({p.name for p in output.iterdir()},
                             {'pages.json','page-images.json','page-images','ocr-cache'})

    def test_batch_wrapper_runs_pdf_then_document_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'input';source.mkdir();(source/'contract.pdf').write_bytes(b'%PDF')
            argv=['batch','--input-prefix',str(source),'--output-root',str(root/'out'),'--project','p']
            with patch.object(sys,'argv',argv),patch.object(batch.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as invoke:
                batch.main()
            calls=[call.args[0] for call in invoke.call_args_list]
            self.assertEqual(len(calls),2)
            self.assertEqual(Path(calls[0][1]).name,'ocr_pipeline.py')
            self.assertNotIn('--model',calls[0]);self.assertNotIn('--document-boundaries',calls[0])
            self.assertEqual(Path(calls[1][1]).name,'document_pipeline.py')
            self.assertEqual(calls[1][calls[1].index('--document-boundary-model')+1],'gemini-3.5-flash')

    def test_batch_wrapper_does_not_process_documents_after_transcription_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'input';source.mkdir();(source/'contract.pdf').write_bytes(b'%PDF')
            argv=['batch','--input-prefix',str(source),'--output-root',str(root/'out'),'--project','p']
            with patch.object(sys,'argv',argv),patch.object(batch.subprocess,'run',return_value=SimpleNamespace(returncode=1)) as invoke:
                with self.assertRaises(SystemExit):batch.main()
            self.assertEqual(invoke.call_count,1)
