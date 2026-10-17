from pathlib import Path
import importlib.util,json,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('acceptance',ROOT/'scripts/extraction_acceptance.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
class ExtractionApprovalTests(unittest.TestCase):
 def fixture(self,root):
  folder=root/'extraction/blb';folder.mkdir(parents=True)
  for name in mod.ARTIFACTS:(folder/name).write_text('{}')
  (folder/'pages.json').write_text(json.dumps({'schema_version':5,'pages':[{'pdf_page':1,'raw_ocr_file':'ocr-cache/raw.json','raw_ocr_sha256':__import__('hashlib').sha256(b'{}').hexdigest()}]}))
  (folder/'quality-report.json').write_text(json.dumps({'schema_version':5,'status':'awaiting_source_review','findings':[]}))
  (folder/'ocr-cache').mkdir();(folder/'ocr-cache/raw.json').write_bytes(b'{}')
  (folder/'page-images').mkdir();(folder/'page-images/page-0001.png').write_bytes(b'rendered-page')
  return folder
 def test_machine_checks_do_not_approve_and_review_is_not_overwritten(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.fixture(root)
   with self.assertRaisesRegex(ValueError,'not source-reviewed'):mod.require_approved(root,[{'key':'blb'}])
   with self.assertRaises(ValueError):mod.approve(root,'blb','','','reviewed')
   mod.approve(root,'blb','Human','Compared all source pages','reviewed');mod.require_approved(root,[{'key':'blb'}])
   with self.assertRaisesRegex(ValueError,'already exists'):mod.approve(root,'blb','Human','Reviewed','reviewed')
 def test_each_changed_artifact_invalidates_approval(self):
  for name in (*mod.ARTIFACTS,'page-images/page-0001.png'):
   with self.subTest(name=name),tempfile.TemporaryDirectory() as d:
    root=Path(d);folder=self.fixture(root);mod.approve(root,'blb','Human','Reviewed all source pages','reviewed');p=folder/name
    if name.endswith('.json'):
     x=json.loads(p.read_text());x['modified']=True;p.write_text(json.dumps(x))
    else:p.write_bytes(b'changed')
    with self.assertRaises(ValueError):mod.require_approved(root,[{'key':'blb'}])
 def test_findings_cannot_be_waived(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);folder=self.fixture(root);(folder/'quality-report.json').write_text(json.dumps({'schema_version':5,'findings':[{'code':'checkbox_needs_review'}]}))
   with self.assertRaisesRegex(ValueError,'Unresolved'):mod.approve(root,'blb','Human','Reviewed','reviewed')
 def test_missing_rendered_page_cannot_be_approved(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);folder=self.fixture(root);(folder/'page-images/page-0001.png').unlink()
   with self.assertRaises(ValueError):mod.approve(root,'blb','Human','Reviewed','reviewed')

class ScopedFindingTests(unittest.TestCase):
 fixture=ExtractionApprovalTests.fixture
 def test_transcription_findings_require_explicit_review_and_preserve_text(self):
  for code in ['uncertain_transcription','possible_nonliteral_transcription']:
   with self.subTest(code=code),tempfile.TemporaryDirectory() as d:
    root=Path(d);folder=self.fixture(root);before=(folder/'pages.json').read_bytes()
    (folder/'quality-report.json').write_text(json.dumps({'schema_version':5,'status':'needs_review','findings':[{'code':code,'pdf_page':1}]}))
    with self.assertRaises(ValueError):mod.resolve_finding(root,'blb',1,code,'Human','Compared rendered image','')
    mod.resolve_finding(root,'blb',1,code,'Human','Compared rendered image; uncertainty remains recorded','reviewed')
    self.assertEqual((folder/'pages.json').read_bytes(),before)
    self.assertEqual(json.loads((folder/'quality-report.json').read_text())['status'],'awaiting_source_review')
    with self.assertRaisesRegex(ValueError,'not source-reviewed'):mod.require_approved(root,[{'key':'blb'}])
 def test_only_source_checked_diagnostic_can_be_resolved_and_original_is_preserved(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);folder=self.fixture(root)
   (folder/'quality-report.json').write_text(json.dumps({'schema_version':5,'status':'needs_review','findings':[{'code':'low_ocr_confidence','pdf_page':1},{'code':'document_line_coverage','pdf_page':None}]}))
   with self.assertRaises(ValueError):mod.resolve_finding(root,'blb',1,'document_line_coverage','Human','Checked','reviewed')
   audit,remaining=mod.resolve_finding(root,'blb',1,'low_ocr_confidence','Human','Compared visible source; independent OCR misread the signature','reviewed')
   self.assertEqual(remaining,1);self.assertTrue((audit/'quality-report-before.json').exists())
   quality=json.loads((folder/'quality-report.json').read_text());self.assertEqual(quality['status'],'needs_review');self.assertEqual(len(quality['resolved_findings']),1)
   with self.assertRaises(ValueError):mod.approve(root,'blb','Human','Checked','reviewed')

class OCRPrimaryApprovalTests(unittest.TestCase):
 def test_gemini_review_requires_and_binds_rendered_image_inventory(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);folder=ExtractionApprovalTests().fixture(root)
   data=json.loads((folder/'pages.json').read_text());data['pages'][0]['ocr_provider']='gemini'
   (folder/'pages.json').write_text(json.dumps(data))
   with self.assertRaisesRegex(ValueError,'inventory'):mod.hashes(folder)
   image_hash=__import__('hashlib').sha256((folder/'page-images/page-0001.png').read_bytes()).hexdigest()
   inventory={'images':[{'pdf_page':1,'image_file':'page-images/page-0001.png','image_sha256':image_hash}]}
   (folder/'page-images.json').write_text(json.dumps(inventory))
   self.assertIn('page-images.json',mod.hashes(folder))
   mod.approve(root,'blb','Human','Compared rendered pages','reviewed')
   (folder/'page-images.json').write_text('{"images":["changed"]}')
   with self.assertRaises(ValueError):mod.require_approved(root,[{'key':'blb'}])

 def fixture(self,root):
  import hashlib
  folder=ExtractionApprovalTests().fixture(root)
  raw=folder/'ocr-cache/raw.json';raw.write_text('{"raw":"OCR evidence"}')
  (folder/'pages.json').write_text(json.dumps({'schema_version':5,'pages':[{'pdf_page':1,'raw_ocr_file':'ocr-cache/raw.json','raw_ocr_sha256':hashlib.sha256(raw.read_bytes()).hexdigest()}]}))
  (folder/'quality-report.json').write_text(json.dumps({'schema_version':5,'status':'awaiting_source_review','findings':[]}))
  return folder
 def test_approval_binds_raw_ocr_and_document_proposals(self):
  for name in ('ocr-cache/raw.json','documents.json'):
   with self.subTest(name=name),tempfile.TemporaryDirectory() as d:
    root=Path(d);folder=self.fixture(root);mod.approve(root,'blb','Human','Checked visible source','reviewed')
    mod.require_approved(root,[{'key':'blb'}]);(folder/name).write_text('{"changed":true}')
    with self.assertRaises(ValueError):mod.require_approved(root,[{'key':'blb'}])
 def test_source_review_of_low_confidence_preserves_the_ocr_text_and_requires_final_approval(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);folder=self.fixture(root);before=(folder/'ocr-cache/raw.json').read_bytes()
   (folder/'quality-report.json').write_text(json.dumps({'schema_version':5,'status':'needs_review','findings':[{'code':'low_ocr_confidence','pdf_page':1}]}))
   audit,left=mod.resolve_finding(root,'blb',1,'low_ocr_confidence','Human','Compared every flagged line with the rendered source','reviewed')
   self.assertEqual(left,0);self.assertEqual((folder/'ocr-cache/raw.json').read_bytes(),before)
   self.assertTrue((audit/'quality-report-before.json').exists())
   with self.assertRaisesRegex(ValueError,'not source-reviewed'):mod.require_approved(root,[{'key':'blb'}])


class CropEvidenceApprovalTests(unittest.TestCase):
 def test_approval_covers_both_crop_pixels_and_raw_response(self):
  import hashlib
  for target in ('page-images/crop.png','ocr-cache/crop.json'):
   with self.subTest(target=target),tempfile.TemporaryDirectory() as d:
    root=Path(d);folder=ExtractionApprovalTests().fixture(root)
    crop=folder/'page-images/crop.png';crop.write_bytes(b'crop pixels')
    raw=folder/'ocr-cache/crop.json';raw.write_bytes(b'crop response')
    p=json.loads((folder/'pages.json').read_text())
    p['pages'][0]['ocr_rechecks']=[{'attempts':[{'image':'page-images/crop.png','image_sha256':hashlib.sha256(crop.read_bytes()).hexdigest(),
      'raw_ocr_file':'ocr-cache/crop.json','raw_ocr_sha256':hashlib.sha256(raw.read_bytes()).hexdigest()}]}]
    (folder/'pages.json').write_text(json.dumps(p));mod.approve(root,'blb','Human','Source checked','reviewed')
    mod.require_approved(root,[{'key':'blb'}]);(folder/target).write_bytes(b'changed')
    with self.assertRaisesRegex(ValueError,'Crop OCR evidence changed'):mod.require_approved(root,[{'key':'blb'}])


class PageTextSchemaApprovalTests(unittest.TestCase):
 def test_schema6_without_lines_is_reviewable_and_remains_evidence_bound(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);folder=ExtractionApprovalTests().fixture(root)
   for name in ('pages.json','quality-report.json'):
    data=json.loads((folder/name).read_text());data['schema_version']=6
    (folder/name).write_text(json.dumps(data))
   mod.approve(root,'blb','Human','Compared all rendered pages with page text','reviewed')
   mod.require_approved(root,[{'key':'blb'}])
   data=json.loads((folder/'pages.json').read_text());data['pages'][0]['text']='Changed'
   (folder/'pages.json').write_text(json.dumps(data))
   with self.assertRaises(ValueError):mod.require_approved(root,[{'key':'blb'}])

 def test_schema6_can_regroup_without_rewriting_page_text(self):
  from extraction_review import prepare_regroup
  import hashlib
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);folder=ExtractionApprovalTests().fixture(root)
   data=json.loads((folder/'pages.json').read_text());data['schema_version']=6
   (folder/'pages.json').write_text(json.dumps(data));before=(folder/'pages.json').read_bytes()
   audit=prepare_regroup(root,'blb',lambda p:json.loads(p.read_text()),
       lambda p,data:p.write_text(json.dumps(data)),lambda p:hashlib.sha256(p.read_bytes()).hexdigest())
   self.assertTrue((audit/'audit.json').is_file())
   self.assertEqual((folder/'pages.json').read_bytes(),before)
