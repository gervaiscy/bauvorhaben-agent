import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pymupdf
import document_ocr as ocr
import extract
from document_grouping import group_documents
from ocr_quality import report


def fixture(text='Visible clause\n'):
    layout={'textAnchor':{'textSegments':[{'endIndex':str(len(text))}]},'confidence':.999,
            'boundingPoly':{'normalizedVertices':[{}, {'x':.9}, {'x':.9,'y':.1}, {'y':.1}]}}
    return {'document': {'text':text,'pages':[{'dimension':{'width':100,'height':200},'lines':[{'layout':layout}]}]}}


def page(response=None,n=1):
    return ocr.normalize(response or fixture(),n,'imagehash','ocr-cache/raw.json')


class ProviderTests(unittest.TestCase):
    def test_rest_sends_only_png_pixels_and_pinned_version_with_visible_ocr_options(self):
        session=Mock();session.post.return_value=Mock(ok=True);session.post.return_value.json.return_value=fixture()
        p=ocr.GoogleDocumentOCR(session=session);self.assertEqual(p.process(b'png'),fixture())
        args,kw=session.post.call_args
        self.assertIn('/processorVersions/pretrained-ocr-v2.1-2024-08-07:process',args[0])
        self.assertEqual(kw['json']['rawDocument'],{'mimeType':'image/png','content':'cG5n'})
        self.assertFalse(kw['json']['processOptions']['ocrConfig']['enableNativePdfParsing'])
        self.assertTrue(kw['json']['processOptions']['ocrConfig']['premiumFeatures']['enableSelectionMarkDetection'])
    def test_auth_errors_fail_once_without_logging_provider_body_and_transient_errors_retry(self):
        session=Mock();session.post.return_value=Mock(ok=False,status_code=403,text='secret body')
        with self.assertRaisesRegex(RuntimeError,'roles/documentai.apiUser') as raised:ocr.GoogleDocumentOCR(session=session).process(b'png')
        self.assertNotIn('secret body',str(raised.exception));self.assertEqual(session.post.call_count,1)
        session.post.reset_mock();ok=Mock(ok=True);ok.json.return_value=fixture()
        session.post.side_effect=[Mock(ok=False,status_code=429),ok]
        with patch.object(ocr.time,'sleep') as sleep:ocr.GoogleDocumentOCR(session=session).process(b'png')
        sleep.assert_called_once_with(1)
    def test_arbitrary_endpoints_unpinned_aliases_and_invalid_resources_are_rejected(self):
        for resource in ('https://evil.example/path','projects/a/locations/eu/processors/1',ocr.DEFAULT_PROCESSOR.rsplit('/',1)[0]+'/latest'):
            with self.assertRaises(ValueError):ocr.endpoint(resource)
    def test_cache_bound_to_pixels_processor_and_options_and_detects_tampering(self):
        provider=Mock(processor=ocr.DEFAULT_PROCESSOR);provider.process.return_value=fixture()
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);result,path=ocr.cached_ocr(provider,b'image',folder)
            ocr.cached_ocr(provider,b'image',folder);self.assertEqual(provider.process.call_count,1)
            ocr.cached_ocr(provider,b'changed pixels',folder);self.assertEqual(provider.process.call_count,2)
            data=json.loads(path.read_text());data['response']['document']['text']='tampered';path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'modified'):ocr.cached_ocr(provider,b'image',folder)


class ProjectionTests(unittest.TestCase):
    def test_exact_unicode_anchor_text_zero_vertex_coordinates_and_page_stable_ids(self):
        raw=fixture('§ 4 — Bürgschaft 10 Wochen\n');before=copy.deepcopy(raw)
        row=page(raw,4);line=row['lines'][0]
        self.assertEqual(line['text'],'§ 4 — Bürgschaft 10 Wochen');self.assertEqual(line['bbox'],[0,0,900,100])
        self.assertEqual(line['id'],'L0004_00001');self.assertIsNone(line['handwritten']);self.assertEqual(raw,before)
        self.assertEqual(page(raw,4)['lines'][0],line)
    def test_native_or_unassigned_text_is_not_silently_discarded(self):
        raw=fixture('Visible clause\nEXTRA');raw['document']['pages'][0]['lines'][0]['layout']['textAnchor']['textSegments'][0]['endIndex']='15'
        row=page(raw);self.assertEqual(row['unassigned_text'],'EXTRA')
        quality=report([row],{'documents':[{'line_ids':[l['id'] for l in row['lines']]}]},1)
        self.assertIn('ocr_line_geometry_missing',[f['code'] for f in quality['findings']])
    def test_bad_anchors_geometry_and_multiple_pages_fail(self):
        raw=fixture();raw['document']['pages'][0]['lines'][0]['layout']['textAnchor']['textSegments'][0]['endIndex']='9999'
        with self.assertRaises(ValueError):page(raw)
        raw=fixture();raw['document']['pages'][0]['lines'][0]['layout']['boundingPoly']['normalizedVertices'][1]['x']=float('nan')
        with self.assertRaises(ValueError):page(raw)
        raw=fixture();raw['document']['pages']*=2
        with self.assertRaises(ValueError):page(raw)
    def test_checkbox_state_and_coordinates_are_provider_sourced_and_low_confidence_is_uncertain(self):
        raw=fixture();layout=copy.deepcopy(raw['document']['pages'][0]['lines'][0]['layout'])
        raw['document']['pages'][0]['visualElements']=[{'type':'filled_checkbox','layout':layout}]
        row=page(raw);mark=row['checkboxes'][0]
        self.assertEqual(mark['state'],'checked');self.assertEqual(mark['bbox'],[0,0,900,100])
        layout['confidence']=.5;mark=page(raw)['checkboxes'][0]
        self.assertEqual(mark['state'],'uncertain');self.assertEqual(mark['detected_state'],'checked')
