"""Offline benchmark checks; no source documents, downloads or API calls."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import benchmark_ocr as bench


class BenchmarkMetricsTests(unittest.TestCase):
    def test_levenshtein_counts_insertions_deletions_and_word_order(self):
        self.assertEqual(bench.distance('abc','abc'),0)
        self.assertEqual(bench.distance('abc','axc!'),2)
        self.assertEqual(bench.distance(['not','included'],['included','not']),2)

    def test_display_equivalence_does_not_correct_currency_or_litre_errors(self):
        self.assertTrue(bench.score('45° und 650 x 480 mm','45^{\\circ} und 650\\times480 mm',[])['exact_display_match'])
        for expected,actual in [('35 €/m²','35\\in/m2'),('50,00 €/m²','50,00~E/m^{2}'),('190/200 l','190/200 I')]:
            result=bench.score(expected,actual,[expected])
            self.assertFalse(result['critical'][0]['correct']);self.assertGreater(result['display_character_errors'],0)

    def test_critical_checks_preserve_negations_units_and_reading_order(self):
        result=bench.score('wird nicht verputzt; 2 Wochen vor Baubeginn','wird verputzt; 2 Tage vor Baubeginn',
            ['wird nicht verputzt','2 Wochen vor Baubeginn'])
        self.assertFalse(any(c['correct'] for c in result['critical']))
        self.assertTrue(bench.score('50,00 €/m²','50,00 €/m2',['50,00 €/m²'])['critical'][0]['correct'])

    def test_empty_response_is_penalized_and_ambiguous_case_reported_separately(self):
        a=bench.score('35 €/m²','',['35 €/m²'])
        self.assertEqual(a['character_errors'],len('35 €/m²'))
        records=[{'variant':'a','id':'control_a','ambiguity':None,'error':'provider failed','scores':a},
            {'variant':'a','id':'capacity','ambiguity':'glyph','scores':bench.score('l','l',['l'])}]
        result=bench.aggregate(records)[0]
        self.assertEqual(result['critical_correct'],1);self.assertEqual(result['critical_total'],2)
        self.assertEqual(result['unambiguous_critical_total'],1);self.assertEqual(result['unambiguous_critical_correct'],0)
        self.assertEqual(result['failed_regions'],1)

    def test_fixed_region_selection_does_not_search_for_expected_words(self):
        page={'tokens':[{'bbox':[0,0,50,10],'text_segments':[[0,6]]}, {'bbox':[100,0,150,10],'text_segments':[[7,12]]}],
            'text':'inside wrong','lines':[]}
        self.assertEqual(bench.region_text(page,[0,0,60,20]),'inside')
        self.assertEqual(bench.region_text(page,[200,0,300,20]),'')

    def test_tsv_parser_preserves_word_order_and_column_coordinates(self):
        raw='level\tblock_num\tpar_num\tline_num\tleft\ttop\twidth\theight\ttext\n5\t1\t1\t1\t10\t20\t30\t10\t35\n5\t1\t1\t1\t45\t20\t10\t10\t€\n'
        words=bench.parse_tsv(raw,100,100)
        self.assertEqual(words[0]['bbox'],[100,200,400,300]);self.assertEqual(bench.tess_text(words,[0,0,1000,1000]),'35 €')

class BenchmarkRenderingTests(unittest.TestCase):
    def test_direct_render_respects_cropbox_and_has_twice_the_pixels_at_600dpi(self):
        import pymupdf
        with pymupdf.open() as pdf:
            page=pdf.new_page(width=400,height=200)
            page.insert_text((40,50),'VISIBLE');page.insert_text((250,50),'OUTSIDE CROP')
            page.set_cropbox(pymupdf.Rect(0,0,200,200))
            low=bench.render(page,300,[0,0,1000,500]);high=bench.render(page,600,[0,0,1000,500])
            self.assertLessEqual(abs(high.width-2*low.width),1)
            self.assertLessEqual(abs(high.height-2*low.height),1)
            self.assertGreater(high.width,low.width);self.assertLess(high.width,2000)

    def test_repaired_region_does_not_include_words_outside_a_partial_line_crop(self):
        import hashlib
        p={'text':'outside inside\n','text_sha256':hashlib.sha256(b'outside inside\n').hexdigest(),
            'pdf_page':1,'tokens':[{'id':'W1','text':'outside ','bbox':[0,0,70,10],'text_segments':[[0,8]]},
            {'id':'W2','text':'inside','bbox':[100,0,150,10],'text_segments':[[8,14]]}],
            'lines':[{'id':'L1','pdf_page':1,'text':'outside inside','bbox':[0,0,150,10],
            'text_segments':[[0,14]],'token_ids':['W1','W2']}]}
        self.assertEqual(bench.region_text(p,[90,0,160,20],True).strip(),'inside')
        self.assertEqual(p['lines'][0]['text'],'outside inside')

class BenchmarkBudgetTests(unittest.TestCase):
    def test_failed_request_reservation_survives_restart_and_enforces_cap(self):
        import tempfile
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            provider=Mock(processor='p');provider.process.side_effect=RuntimeError('unavailable')
            budget=bench.BudgetProvider(provider,Path(tmp),1)
            with self.assertRaisesRegex(RuntimeError,'unavailable'):budget.process(b'pixels')
            resumed=bench.BudgetProvider(provider,Path(tmp),1)
            self.assertEqual(resumed.entries[0]['status'],'failed')
            with self.assertRaisesRegex(RuntimeError,'limit reached'):resumed.process(b'new pixels')
            self.assertEqual(provider.process.call_count,1)

class TSVBoundaryTests(unittest.TestCase):
    def test_plain_text_output_is_rejected_instead_of_becoming_false_empty_ocr(self):
        with self.assertRaisesRegex(ValueError,'did not return TSV'):bench.parse_tsv('A single visible line\n',100,100)
