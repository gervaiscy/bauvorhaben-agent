import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pymupdf
from document_ocr import normalize, OPTIONS
from ocr_repairs import repair_order, recheck, safe_consensus, visual_order
from legacy_ocr_quality import report
from test_document_ocr import fixture


def row(text, box, index, start=0):
    return {'id':f'L0001_{index:05}', 'pdf_page':1,'text':text,'bbox':box,
        'text_segments':[[start,start+len(text)]],'token_ids':[], 'confidence':.999}


def page(lines):
    original='';offset=0
    for l in lines:
        l['text_segments']=[[offset,offset+len(l['text'])]]
        original+=l['text']+'\n';offset=len(original)
    return {'pdf_page':1,'text':original,'text_sha256':hashlib.sha256(original.encode()).hexdigest(),
        'lines':lines,'tokens':[], 'checkboxes':[], 'unassigned_text':'','min_confidence':.98}


class GeometryTests(unittest.TestCase):
    def test_displaced_fragment_rejoins_its_visual_row_without_changing_source_ids(self):
        p=page([row('Lage des Verdichters',[200,100,800,112],1),
                row('Next sentence',[60,120,900,132],2),row('veränderten',[60,100,170,112],3)])
        original=copy.deepcopy(p);result=repair_order(p)
        self.assertEqual([l['id'] for l in result['lines']],[p['lines'][2]['id'],p['lines'][0]['id'],p['lines'][1]['id']])
        self.assertEqual(p,original);self.assertEqual(result['provider_lines'],original['lines'])
        self.assertEqual(result['provider_text'],original['text'])
        for l in result['lines']:
            a,b=l['text_segments'][0];self.assertEqual(result['text'][a:b],l['text'])

    def test_columns_are_kept_separate_and_heading_stays_first(self):
        lines=[row('Heading',[50,20,950,40],1),row('left A',[50,100,400,112],2),
            row('right A',[600,105,950,117],3),row('left B',[50,120,400,132],4),
            row('right B',[600,125,950,137],5),row('left C',[50,140,400,152],6),row('right C',[600,145,950,157],7)]
        self.assertEqual([l['text'] for l in visual_order(lines)],['Heading','left A','left B','left C','right A','right B','right C'])

    def test_missing_geometry_keeps_every_character_and_flags_review(self):
        p=page([row('Visible',[0,0,100,12],1),row('EXTRA',None,2)])
        fixed=repair_order(p);self.assertEqual(fixed['text'],p['text'])
        self.assertEqual(fixed['repair_findings'][0]['code'],'reading_order_needs_review')

    def test_range_hyphen_is_reordered_using_tokens_not_replaced_by_guess(self):
        p=page([row('10 cm 15 cm\n-',[0,0,100,12],1)])
        # Anchors remain tied to provider text; hyphen occurs last in text, middle in pixels.
        for i,(value,a,b,x) in enumerate([('10 ',0,3,0),('cm ',3,6,20),('15 ',6,9,60),('cm',9,11,80),('-',12,13,45)],1):
            p['tokens'].append({'id':f'W{i}','text':value,'text_segments':[[a,b]],'bbox':[x,0,x+10,12]})
        p['lines'][0]['token_ids']=[w['id'] for w in p['tokens']]
        fixed=repair_order(p)
        self.assertEqual(fixed['lines'][0]['text'],'10 cm - 15 cm')
        self.assertEqual(fixed['corrections'][0]['kind'],'token_order')
        self.assertEqual(len(fixed['tokens']),5)

    def test_overlapping_tokens_are_not_silently_deleted(self):
        p=page([row('480 480',[0,0,70,12],1)])
        fixed=repair_order(p);self.assertEqual(fixed['text'],p['text'])


class CropTests(unittest.TestCase):
    def setup_page(self):
        return repair_order(page([row('Materialwert 35\\in/m2',[100,100,850,120],1)]))

    def call(self,responses,limit=3,budget=None):
        p=self.setup_page();provider=Mock(processor='processor')
        with tempfile.TemporaryDirectory() as tmp,pymupdf.open() as raster:
            out=Path(tmp);(out/'page-images').mkdir();(out/'ocr-cache').mkdir()
            r=raster.new_page(width=200,height=300);r.insert_text((20,34),'Materialwert 35 EUR/m2')
            responses=iter(responses);calls=[]
            def cached(*args):
                calls.append(args[1]);raw=next(responses);path=out/'ocr-cache'/f'{len(calls)}.json';path.write_text(json.dumps(raw));return raw,path
            with patch('ocr_repairs.cached_ocr',side_effect=cached):recheck(p,r,provider,out,limit=limit,budget=budget)
            for attempt in p['ocr_rechecks'][0]['attempts']:
                self.assertTrue((out/attempt['image']).is_file());self.assertTrue((out/attempt['raw_ocr_file']).is_file())
            return p,calls

    def test_two_high_confidence_readings_repair_currency_with_immutable_original(self):
        fixed,calls=self.call([fixture('Materialwert 35 €/m2\n')]*2)
        self.assertEqual(fixed['lines'][0]['text'],'Materialwert 35 €/m2')
        self.assertIn('35\\in',fixed['provider_text']);self.assertEqual(len(calls),2)
        self.assertEqual(fixed['ocr_rechecks'][0]['status'],'confirmed')
        self.assertEqual(fixed['corrections'][-1]['kind'],'crop_consensus')

    def test_disagreement_changed_number_and_changed_negation_require_review(self):
        for values in [('Materialwert 35 €/m2','Materialwert 36 €/m2'),('Materialwert 36 €/m2',)*2,('Materialwert nicht 35 €/m2',)*2]:
            with self.subTest(values=values):
                fixed,_=self.call([fixture(v+'\n') for v in values])
                self.assertEqual(fixed['lines'][0]['text'],'Materialwert 35\\in/m2')
                self.assertEqual(fixed['ocr_rechecks'][0]['status'],'needs_review')
                quality=report([fixed],{'documents':[{'line_ids':[l['id'] for l in fixed['lines']]}]},1)
                self.assertIn('ocr_region_needs_review',[f['code'] for f in quality['findings']])

    def test_low_confidence_and_service_failure_preserve_original(self):
        raw=fixture('Materialwert 35 €/m2\n');raw['document']['pages'][0]['lines'][0]['layout']['confidence']=.4
        fixed,_=self.call([raw,raw]);self.assertEqual(fixed['ocr_rechecks'][0]['status'],'needs_review')
        fixed,_=self.call([]);self.assertIn('failed',fixed['ocr_rechecks'][0]['reason'])

    def test_zero_budget_makes_no_api_calls_and_flags_unchecked_region(self):
        for limit,budget in [(0,None),(3,[0])]:
            fixed,calls=self.call([],limit,budget)
            self.assertFalse(calls);self.assertEqual(fixed['ocr_rechecks'][0]['status'],'needs_review')

    def test_numeric_duplicate_requires_geometric_evidence_and_words_cannot_change(self):
        self.assertFalse(safe_consensus('650 mm x 480 480mm','650 mm x 480 mm',[]))
        self.assertTrue(safe_consensus('650 mm x 480 480mm','650 mm x 480 mm',['overlapping_numeric_tokens']))
        self.assertFalse(safe_consensus('nicht 35 €/m2','35 €/m2',[]))
        self.assertTrue(safe_consensus('190/200 | Speicherinhalt','190/200 l Speicherinhalt',[]))

    def test_configuration_is_explicitly_plain_ocr_with_character_evidence(self):
        self.assertFalse(OPTIONS['ocrConfig']['premiumFeatures']['enableMathOcr'])
        self.assertTrue(OPTIONS['ocrConfig']['enableSymbol'])

class LayoutRegressionTests(unittest.TestCase):
    def test_numeric_table_cells_keep_row_associations(self):
        lines=[]
        for i in range(5):
            for col,value in enumerate((str(100+i),'Adobe','Author','Description')):
                lines.append(row(value,[col*200,100+i*18,col*200+120,112+i*18],len(lines)+1))
        # Provider sometimes emits adjacent IDs first, before the other columns.
        scrambled=lines[4:8]+lines[:4]+lines[8:]
        self.assertEqual([l['text'] for l in visual_order(scrambled)],[l['text'] for l in lines])

    def test_uneven_columns_do_not_interleave_when_one_contains_large_blank_spaces(self):
        lines=[row('left heading',[20,100,350,112],1),row('left middle',[20,200,350,212],2),
            row('left last',[20,310,350,322],3)]
        for i,y in enumerate(range(100,320,20),4):lines.append(row('right '+str(i),[500,y,900,y+12],i))
        ordered=visual_order(lines)
        self.assertEqual([l['text'] for l in ordered[:3]],['left heading','left middle','left last'])

    def test_same_row_fragments_have_spaces_and_exact_distinct_anchors(self):
        p=repair_order(page([row('Das',[20,100,50,112],1),row('Türblatt',[70,100,150,112],2)]))
        self.assertEqual(p['text'],'Das Türblatt\n')
        self.assertEqual(p['lines'][1]['text_segments'],[[4,12]])

    def test_formula_symbols_without_boxes_remain_in_evidence(self):
        raw=fixture('35\\in/m2\n');layout=copy.deepcopy(raw['document']['pages'][0]['lines'][0]['layout']);layout['boundingPoly']={}
        raw['document']['pages'][0]['symbols']=[{'layout':layout}]
        p=normalize(raw,1,'hash','ocr-cache/raw.json')
        self.assertEqual(p['symbols'][0]['text'],'35\\in/m2');self.assertIsNone(p['symbols'][0]['bbox'])

class SuspicionPriorityTests(unittest.TestCase):
    def test_currency_precedes_equivalent_math_and_heading_separator_is_not_capacity(self):
        from ocr_repairs import suspicious
        p=page([row('18 | Fliesen',[0,0,100,12],1),row('35\\in/m2',[0,20,100,32],2),
            row('650\\times490 mm',[0,40,100,52],3)])
        candidates=suspicious(p)
        self.assertEqual([c['line_id'] for c in candidates],[p['lines'][1]['id']])
        self.assertIn('currency',candidates[0]['reasons'])

class ContractualSymbolTests(unittest.TestCase):
    def test_crop_cannot_flip_comparison_sign_percentage_or_valid_currency(self):
        for before,after in [('Abstand < 2 m','Abstand > 2 m'),('Preis 5 %','Preis 5'),
            ('Preis 35 €/m2','Preis 35 $/m2'),('Wert -5','Wert 5'),('Wert .5','Wert 5')]:
            self.assertFalse(safe_consensus(before,after,[]),(before,after))
