import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bauvo_working_units.cli import main
from bauvo_working_units.trace import RawRunRecorder
from bauvo_working_units.gemini import GeminiModel


class FakeGemini:
    def __init__(self, *_args):
        self.last_raw_response = None

    def extract(self, prompt):
        self.last_raw_response = '{"working_units":[{"name":"unchanged"}],"extra":true}'
        return json.loads(self.last_raw_response)


class FailingGemini:
    def __init__(self):
        self.last_raw_response = None

    def extract(self, _prompt):
        self.last_raw_response = '{bad json'
        raise ValueError("invalid Gemini JSON")


class RawRunTests(unittest.TestCase):
    def test_gemini_adapter_does_not_wrap_top_level_array(self):
        class Client:
            class models:
                @staticmethod
                def generate_content(**_kwargs):
                    return type("Response", (), {"text": '[{"name":"original"}]'})()

        model = GeminiModel.__new__(GeminiModel)
        model._client = Client()
        model._model = "fake"
        model._types = type("Types", (), {"GenerateContentConfig": lambda **kwargs: kwargs})
        self.assertEqual(model.extract("prompt"), [{"name": "original"}])

    def test_records_only_response_values_before_application_mutates_them(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.json"
            recorder = RawRunRecorder(FakeGemini(), path)
            response = recorder.extract("FIRST_PASS. text")
            response["working_units"].clear()
            saved = json.loads(path.read_text())
            self.assertIsInstance(saved, list)
            self.assertEqual(saved[0]["working_units"],
                             [{"name": "unchanged"}])
            self.assertEqual(saved[0]["extra"], True)

    def test_records_response_even_when_parsing_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.json"
            recorder = RawRunRecorder(FailingGemini(), path)
            with self.assertRaises(ValueError):
                recorder.extract("FIRST_PASS. text")
            saved = json.loads(path.read_text())
            self.assertEqual(saved, ["{bad json"])

    def test_cli_writes_separate_raw_and_processed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "units.json"
            argv = ["bauvo-working-units", "--pages", "pages.json", "--documents",
                    "documents.json", "--project", "test-project", "--output", str(output)]

            def fake_pipeline(_documents, model, *_args):
                response = model.extract("FIRST_PASS. text")
                response["working_units"].clear()
                return {"schema_version": 9, "working_units": [], "offers": [],
                        "gemini_responses": []}

            with (patch.object(sys, "argv", argv),
                  patch("bauvo_working_units.cli.load_documents", return_value=[]),
                  patch("bauvo_working_units.cli.GeminiModel", FakeGemini),
                  patch("bauvo_working_units.cli.extract_project_units", fake_pipeline)):
                main()
            raw = json.loads((Path(directory) / "units.gemini-raw.json").read_text())
            processed = json.loads(output.read_text())
            self.assertEqual(len(raw), 1)
            self.assertEqual(raw[0]["working_units"],
                             [{"name": "unchanged"}])
            self.assertEqual(processed["working_units"], [])


if __name__ == "__main__":
    unittest.main()
