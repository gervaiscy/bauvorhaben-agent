"""Boundary tests for the independent enrichment pass."""

from __future__ import annotations
from page_test_support import pages_fixture

import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from enrich_working_units import Source, enrich, load_manifest


class FakeModel:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def extract(self, prompt):
        self.prompts.append(prompt)
        return self.answers.pop(0)


class NoCallsModel:
    def extract(self, prompt):
        raise AssertionError("A complete checkpoint must not call Gemini again")


def source():
    unit = {
        "id": "R0004:working_unit:000",
        "gemini": {
            "name": "Balkon abdichten", "actor": "ROSTOW Bau GmbH",
            "due": None, "evidence_line_ids": ["L00234", "L00234"],
            "depends_on": [], "trigger_events": [],
        },
        "derived": {"source_line_ids": ["L00234"], "source_excerpt": "Unterbaute Balkone werden gedämmt und abgedichtet.", "dependency_resolutions": []},
        "reviews": [{"gemini": {"modality": "ACTION"}}],
    }
    return Source(
        "blb", {"schema_version": 9, "working_units": [unit], "offers": [],
                "gemini_responses": [{"gemini": {"working_units": [unit["gemini"]]}}]},
        [{"page_context_id": "S04", "heading": "Sonderleistungen Dach",
          "line_ids": ["L00233", "L00234", "L00235"]}],
        {
            "L00233": {"id": "L00233", "pdf_page": 23,
                       "text": "Balkone werden laut Bauzeichnung gebaut."},
            "L00234": {"id": "L00234", "pdf_page": 23,
                       "text": "Unterbaute Balkone werden gedämmt und abgedichtet."},
            "L00235": {"id": "L00235", "pdf_page": 23,
                       "text": "Danach kann der Bauherr den Belag aufbringen."},
        },
    )


class EnrichmentTests(unittest.TestCase):
    def test_preserves_original_and_all_raw_model_answers(self):
        original = source()
        original_assembled = copy.deepcopy(original.assembled)
        ref = "blb:R0004:working_unit:000"
        answers = [
            {"rules": [{"rule": "Baubeginn nach Unterlagen", "citations": []}]},
            {"rules": [], "unresolved_questions": ["Baubeginn unbekannt"]},
            [{"unit_ref": ref, "condition": "Balkon in Bauzeichnung",
              "status": "UNKNOWN", "citations": [{"source_key": "blb", "line_id": "L00233"}]}],
            {"units": [{"unit_ref": ref, "due_date": None,
                        "due_kind": "EVENT_RELATIVE", "anchor_event": "Balkon gebaut"}]},
            {"ordered_unit_refs": [ref], "attention_now": [], "reasons": []},
        ]
        model = FakeModel(answers)
        with tempfile.TemporaryDirectory() as temporary:
            raw_path = Path(temporary) / "raw.json"
            result = enrich([original], date(2025, 6, 27), model, raw_path)
            self.assertEqual(json.loads(raw_path.read_text()), answers)
        self.assertEqual(result["sources"][0]["assembled"], original_assembled)
        self.assertEqual(original.assembled, original_assembled)
        self.assertEqual(result["sources"][0]["assembled"]["working_units"][0]
                         ["gemini"]["evidence_line_ids"], ["L00234", "L00234"])
        self.assertEqual(result["unit_enrichments"][0]["applicability"][0]["status"], "UNKNOWN")
        self.assertIn("Balkone werden laut Bauzeichnung gebaut", model.prompts[2])
        self.assertIn("2025-06-27", model.prompts[3])
        self.assertFalse(result["diagnostics"])

    def test_missing_review_and_order_are_diagnostics_not_deletions(self):
        original = source()
        ref = "blb:R0004:working_unit:000"
        model = FakeModel([
            {"rules": []}, {"rules": []}, {"units": []},
            {"units": [{"unit_ref": "unknown", "due_date": "2025-07-01"}]},
            {"ordered_unit_refs": [], "attention_now": [], "reasons": []},
        ])
        with tempfile.TemporaryDirectory() as temporary:
            result = enrich([original], date(2025, 6, 27), model,
                            Path(temporary) / "raw.json")
        self.assertEqual(result["unit_enrichments"][0]["unit_ref"], ref)
        self.assertEqual(result["unit_enrichments"][0]["applicability"], [])
        self.assertTrue(any("unmatched" in x for x in result["diagnostics"]))
        self.assertTrue(any("presentation_order" in x for x in result["diagnostics"]))

    def test_manifest_keeps_identical_record_ids_from_two_sources_distinct(self):
        original = source()
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for key in ("contract", "blb"):
                (base / f"{key}-units.json").write_text(json.dumps(original.assembled))
                (base / f"{key}-pages.json").write_text(json.dumps(pages_fixture(list(original.lines.values()))))
                (base / f"{key}-lines.json").write_text(json.dumps(list(original.lines.values())))
            manifest = {"contract_signed": "2025-06-27", "sources": [
                {"key": key, "working_units": f"{key}-units.json",
                 "pages": f"{key}-pages.json"}
                for key in ("contract", "blb")
            ]}
            path = base / "manifest.json"
            path.write_text(json.dumps(manifest))
            signed, sources = load_manifest(path)
        self.assertEqual(signed, date(2025, 6, 27))
        self.assertEqual([f"{s.key}:{s.assembled['working_units'][0]['id']}" for s in sources],
                         ["contract:R0004:working_unit:000", "blb:R0004:working_unit:000"])

    def test_complete_checkpoint_replays_without_model_calls(self):
        ref = "blb:R0004:working_unit:000"
        answers = [
            {"rules": []}, {"rules": []},
            {"units": [{"unit_ref": ref, "status": "UNKNOWN"}]},
            {"units": [{"unit_ref": ref, "due_date": None}]},
            {"ordered_unit_refs": [ref]},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            raw_path = Path(temporary) / "raw.json"
            trace_dir = Path(temporary) / "trace"
            first = enrich([source()], date(2025, 6, 27), FakeModel(answers),
                           raw_path, trace_dir)
            replayed = enrich([source()], date(2025, 6, 27), NoCallsModel(),
                              raw_path, trace_dir)
            self.assertEqual(replayed, first)
            self.assertEqual(len(json.loads(raw_path.read_text())), 5)


if __name__ == "__main__":
    unittest.main()

