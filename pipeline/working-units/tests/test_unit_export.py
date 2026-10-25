import copy
import json
from pathlib import Path
import tempfile
import unittest

from bauvo_working_units.unit_export import units_from_responses, write_units


class UnitExportTests(unittest.TestCase):
    def test_preserves_exact_objects_order_duplicates_and_extra_provider_fields(self):
        unit={'name':'Prüfen','action':'Plan prüfen','actor':None,'performed_by':'Bauherr',
              'citations':[{'pdf_page':3,'quote':'Unveränderter Quelltext'}],'additional':'Provider value'}
        responses=[{'working_units':[unit],'carry_forward':'context'}, {'offers':[{'name':'Offer'}]},
                   {'verpflichtungen':[]}, {'working_units':[unit]}]
        before=copy.deepcopy(responses)
        units=units_from_responses(responses)
        self.assertEqual(units,[unit,unit]);self.assertEqual(responses,before)
        units[0]['action']='Independent copy'
        self.assertEqual(responses,before)

    def test_writes_only_units_and_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'new/units.json';write_units(path,[{'action':'Task'}])
            self.assertEqual(json.loads(path.read_text()),{'working_units':[{'action':'Task'}]})
            before=path.read_bytes()
            with self.assertRaises(FileExistsError):write_units(path,[])
            self.assertEqual(path.read_bytes(),before)

    def test_does_not_silently_discard_unfinished_responses_or_malformed_arrays(self):
        for value in ({'working_units':[]}, ['{unfinished'], [None], [{'working_units':None}]):
            with self.subTest(value=value),self.assertRaises(ValueError):units_from_responses(value)
        self.assertEqual(units_from_responses([{'working_units':[]}]),[])
