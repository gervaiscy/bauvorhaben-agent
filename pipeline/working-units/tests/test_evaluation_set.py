import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from bauvo_working_units.project_flow import load_documents,make_page_chunks

EVALUATION=Path(__file__).resolve().parents[1]/'evaluation'
spec=importlib.util.spec_from_file_location('prepare_evaluation',EVALUATION/'prepare.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class EvaluationSetTests(unittest.TestCase):
    def test_expected_evidence_is_literal_document_local_and_ids_are_unique(self):
        data=json.loads((EVALUATION/'cases.json').read_text());cases=data['cases']
        self.assertEqual(len(cases),16);self.assertEqual(len({c['id'] for c in cases}),16)
        for case in cases:
            pages={p['pdf_page']:p['text'] for p in case['pages']}
            documents={d['document_id']:d['pdf_pages'] for d in case['documents']}
            self.assertEqual([p for d in case['documents'] for p in d['pdf_pages']],list(pages))
            self.assertEqual(len({u['key'] for u in case['expected_units']}),len(case['expected_units']))
            self.assertTrue(case['forbidden_inferences'])
            for u in case['expected_units']:
                for e in u['evidence']:
                    self.assertTrue(e['quote']);self.assertIn(e['quote'],pages[e['pdf_page']])
                    self.assertIn(e['pdf_page'],documents[u['document_id']])
                self.assertIsNone(u['timing']['date'])

    def test_preparation_is_deterministic_and_uses_production_schema6_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a=root/'a';b=root/'b'
            self.assertEqual(module.prepare(a),16);self.assertEqual(module.prepare(b),16)
            for p in a.rglob('*.json'):self.assertEqual(p.read_bytes(),(b/p.relative_to(a)).read_bytes())
            for folder in a.iterdir():
                docs=load_documents(folder/'pages.json',folder/'documents.json')
                for doc in docs:
                    for c in make_page_chunks(doc):self.assertLessEqual(len(c.pages),5)
                    self.assertNotIn('expected_units',doc.pages[0])
            docs=load_documents(a/'deadline-across-window-overlap/pages.json',a/'deadline-across-window-overlap/documents.json')
            self.assertEqual([c.pages for c in make_page_chunks(docs[0])],[(1,2,3),(3,4,5),(5,6,7)])

    def test_preparation_refuses_existing_directory_and_preserves_predictions(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'evaluation';module.prepare(output)
            prediction=output/'roles-and-relative-deadline/working-units.json';prediction.write_text('{"original":"preserve"}')
            before={str(p):p.read_bytes() for p in output.rglob('*.json')}
            with self.assertRaisesRegex(ValueError,'already exists'):module.prepare(output)
            self.assertEqual(before,{str(p):p.read_bytes() for p in output.rglob('*.json')})
