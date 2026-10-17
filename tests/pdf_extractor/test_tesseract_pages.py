import hashlib
import unittest
from tesseract_pages import project_tsv

HEADER='level\tblock_num\tpar_num\tline_num\tleft\ttop\twidth\theight\tconf\ttext\n'

class TesseractProjectionTests(unittest.TestCase):
    def project(self,rows=()):return project_tsv(HEADER+''.join(rows),4,100,200,'imagehash','ocr-cache/raw.json')
    def test_projection_has_stable_ids_exact_anchors_and_page_coordinates(self):
        row='5\t1\t1\t1\t10\t20\t20\t10\t97.5\t35\n5\t1\t1\t1\t35\t20\t10\t10\t99\t€\n5\t1\t1\t2\t10\t40\t40\t10\t98\tBauherr\n'
        p=self.project([row]);self.assertEqual(p['text'],'35 €\nBauherr\n')
        self.assertEqual(p['lines'][0]['id'],'L0004_00001');self.assertEqual(p['lines'][0]['bbox'],[100,100,450,150])
        self.assertEqual(p['lines'][0]['confidence'],.975)
        for item in p['lines']+p['tokens']:
            self.assertEqual(''.join(p['text'][a:b] for a,b in item['text_segments']),item['text'])
        self.assertEqual(p['text_sha256'],hashlib.sha256(p['text'].encode()).hexdigest())
        self.assertEqual(p,self.project([row]))
    def test_blank_output_is_retained_and_capabilities_are_explicit(self):
        p=self.project();self.assertEqual(p['text'],'');self.assertEqual(p['lines'],[])
        self.assertFalse(p['checkbox_detection_supported']);self.assertFalse(p['handwriting_detection_supported'])
        self.assertEqual(p['checkboxes'],[])
    def test_bad_confidence_bad_boxes_and_non_tsv_fail(self):
        for row in ['5\t1\t1\t1\t0\t0\t10\t10\tnan\ttext\n','5\t1\t1\t1\t95\t0\t10\t10\t99\ttext\n']:
            with self.assertRaises(ValueError):self.project([row])
        with self.assertRaises(ValueError):project_tsv('plain text',1,100,200,'hash','raw')
    def test_a_word_like_a_checkbox_is_not_interpreted_as_a_selection(self):
        p=self.project(['5\t1\t1\t1\t0\t0\t10\t10\t99\t☑\n'])
        self.assertEqual(p['text'],'☑\n');self.assertEqual(p['checkboxes'],[])
    def test_return_to_a_previous_line_group_is_rejected(self):
        rows=[f'5\t1\t1\t{n}\t0\t0\t10\t10\t99\tword\n' for n in [1,2,1]]
        with self.assertRaisesRegex(ValueError,'Non-contiguous'):self.project(rows)
