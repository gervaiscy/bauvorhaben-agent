import copy
import json
import unittest

from bauvo_working_units.core import SourceLine
from bauvo_working_units.project_flow import Document, extract_project_units


class RecordingModel:
    def __init__(self, first, modality=None, verification=None, relationship=None):
        self.first = first
        self.modality = modality or {"classifications": []}
        self.verification = verification or {"verifications": []}
        self.relationship = relationship or {"related_obligations": []}

    def extract(self, prompt):
        if prompt.startswith("MODALITY_PASS"):
            return copy.deepcopy(self.modality)
        if prompt.startswith("PERMISSION_VERIFICATION_PASS"):
            return copy.deepcopy(self.verification)
        if prompt.startswith("RELATIONSHIP_PASS"):
            return copy.deepcopy(self.relationship)
        return copy.deepcopy(self.first)


class LosslessFlowTests(unittest.TestCase):
    def setUp(self):
        self.document = Document("D1", "Nachtrag", None, (
            SourceLine("L1", 4, "ROSTOW stellt vor der zweiten Rate eine Bürgschaft."),
            SourceLine("L2", 4, "Die Bauherren zahlen nach der Bodenplatte."),
        ))

    def test_duplicate_proposals_and_citations_are_preserved(self):
        unit = {"dedupe_key": "guarantee", "name": "Bürgschaft stellen",
                "actor": "ROSTOW", "action": "Bürgschaft stellen", "due": "vor Rate 2",
                "evidence_line_ids": ["L1", "L1"], "scope_line_ids": ["L1", "L1"],
                "tasks": [], "depends_on": [], "scoped_dependencies": [],
                "trigger_events": [], "unexpected_gemini_field": "keep"}
        second = {**unit, "due": "anderer Termin"}
        result = extract_project_units([self.document], RecordingModel({
            "working_units": [unit, second], "offers": [], "carry_forward": {}}))
        self.assertEqual(result["schema_version"], 9)
        self.assertEqual(len(result["working_units"]), 2)
        self.assertNotEqual(result["working_units"][0]["id"], result["working_units"][1]["id"])
        self.assertEqual(result["working_units"][0]["gemini"], unit)
        self.assertEqual(result["working_units"][1]["gemini"], second)
        self.assertEqual(result["working_units"][0]["derived"]["source_line_ids"], ["L1"])
        self.assertEqual(result["gemini_responses"][0]["gemini"]["working_units"],
                         [unit, second])

    def test_missing_actor_unknown_citation_and_offer_alias_are_kept(self):
        malformed = {"dedupe_key": "guarantee", "name": "Bürgschaft",
                     "evidence_line_ids": ["MISSING"], "scope_line_ids": []}
        offer = {"dedupe_key": "offer", "name": "Angebot", "actor": "ROSTOW",
                 "evidence_line_ids": ["L1"], "scope_line_ids": []}
        result = extract_project_units([self.document], RecordingModel({
            "working_units": [malformed, None], "offers": [offer], "carry_forward": {}}))
        self.assertEqual(len(result["working_units"]), 2)
        self.assertEqual(result["working_units"][0]["gemini"], malformed)
        self.assertIsNone(result["working_units"][1]["gemini"])
        self.assertEqual(result["offers"][0]["gemini"], offer)
        self.assertIn("missing_or_invalid_provider",
                      result["offers"][0]["validation"]["diagnostics"])
        self.assertIn("unknown_evidence_line_id",
                      result["working_units"][0]["validation"]["diagnostics"])

    def test_dependencies_remain_original_and_ambiguity_is_annotated(self):
        base = {"dedupe_key": "foundation", "name": "Bodenplatte", "actor": "ROSTOW",
                "evidence_line_ids": ["L1"], "scope_line_ids": []}
        payment = {"dedupe_key": "payment", "name": "Rate zahlen", "actor": "Bauherren",
                   "evidence_line_ids": ["L2"], "scope_line_ids": [],
                   "depends_on": ["foundation", "unknown"],
                   "scoped_dependencies": [{"dedupe_key": "foundation",
                                            "affected_scope": "Rate 2", "source_line_ids": ["L2"]}]}
        result = extract_project_units([self.document], RecordingModel({
            "working_units": [base, base, payment], "offers": [], "carry_forward": {}}))
        actual = result["working_units"][2]
        self.assertEqual(actual["gemini"]["depends_on"], ["foundation", "unknown"])
        self.assertEqual([row["status"] for row in actual["derived"]["dependency_resolutions"]],
                         ["ambiguous", "unresolved", "ambiguous"])
        self.assertEqual(len(actual["derived"]["dependency_resolutions"][0]["candidate_unit_ids"]), 2)

    def test_permission_review_does_not_clear_original_due_or_tasks(self):
        unit = {"dedupe_key": "sign", "name": "Werbeschild", "actor": "ROSTOW",
                "action": "Schild aufstellen", "due": "ein Jahr nach Baufertigstellung",
                "evidence_line_ids": ["L1"], "scope_line_ids": [],
                "tasks": [{"name": "Schild besorgen", "source_line_ids": ["L1"]}],
                "depends_on": ["foundation"], "scoped_dependencies": []}
        result = extract_project_units([self.document], RecordingModel(
            {"working_units": [unit], "offers": [], "carry_forward": {}},
            modality={"classifications": [{"unit_id": "R0001:working_unit:000",
                                           "modality": "PERMISSION", "source_line_ids": ["L1"]}]},
            verification={"verifications": [{"unit_id": "R0001:working_unit:000",
                                                "confirmed": True,
                                                "permission_kind": "CONTRACTUAL_ACTIVITY_PERMISSION",
                                                "source_line_ids": ["L1"]}]}))
        actual = result["working_units"][0]
        self.assertEqual(actual["gemini"], unit)
        self.assertEqual([review["kind"] for review in actual["reviews"]],
                         ["modality", "permission_verification"])

    def test_invalid_relationship_is_kept_in_response_and_target_review(self):
        unit = {"dedupe_key": "payment", "name": "Rate zahlen", "actor": "Bauherren",
                "evidence_line_ids": ["L2"], "scope_line_ids": []}
        relation = {"target_unit_id": "R0001:working_unit:000",
                    "source_line_ids": ["UNKNOWN"], "actor": "ROSTOW", "extra": 42}
        result = extract_project_units([self.document], RecordingModel(
            {"working_units": [unit], "offers": [], "carry_forward": {}},
            relationship={"related_obligations": [relation]}))
        self.assertEqual(result["working_units"][0]["reviews"][0]["gemini"], relation)
        self.assertEqual(result["gemini_responses"][-1]["gemini"]["related_obligations"],
                         [relation])

    def test_non_object_model_response_is_kept_without_crashing(self):
        result = extract_project_units([self.document], RecordingModel("{malformed json"))
        self.assertEqual(result["working_units"], [])
        self.assertEqual(result["gemini_responses"][0]["gemini"], "{malformed json")


if __name__ == "__main__":
    unittest.main()
