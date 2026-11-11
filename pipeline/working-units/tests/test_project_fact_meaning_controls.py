from historical_facts import collect_historical_facts
"""Held-out controls catch specific interpretation failures despite valid citations."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

BASE = Path(__file__).resolve().parents[1]/'evaluation'
sys.path.insert(0, str(BASE))
import project_facts_evaluation as evaluation
from bauvo_working_units.project_facts import collect_project_facts
from clause_fixtures import decode_prompt, index_answer
from test_project_facts import document


class InterpretationControlTests(unittest.TestCase):
    def test_authored_status_amount_and_qualification_controls_do_not_leak_into_model_input(self):
        cases = [c for c in json.loads((BASE/'project-facts-cases.json').read_text())['cases']
                 if 'interpretation_expectations' in c]
        self.assertEqual(len(cases), 6)
        for case in cases:
            expected = case['interpretation_expectations']
            calls = []
            def provider(prompt):
                calls.append(prompt)
                data = decode_prompt(prompt)
                self.assertNotIn('interpretation_expectations', data)
                self.assertNotIn('required_fragments', data)
                for observation in case['expectations']:
                    self.assertNotIn(observation, prompt)
                value = index_answer(data)
                for row in value['verpflichtungen']:
                    row['status'] = expected['allowed_statuses'][0]
                    row['claims'][0]['statement'] = case['text']
                    row['uncertainty'] = 'Die Bezugszuordnung bleibt offen.' if expected.get('uncertainty_required') else None
                return value
            doc = document(case['id'], case['text'])
            with self.subTest(case=case['id']):
                result = collect_historical_facts([doc], SimpleNamespace(extract=provider))
                self.assertEqual(result['status'], 'recorded')
                self.assertTrue(evaluation.interpretation_checks(case, result)['passed'])
                self.assertEqual(len(calls), 1)
                self.assertTrue(evaluation.interpretation_checks(case, result)['semantic_review_required'])

    def test_complete_source_citations_do_not_hide_an_omitted_qualification_or_price(self):
        cases = {c['id']: c for c in json.loads((BASE/'project-facts-cases.json').read_text())['cases']}
        for identifier, wrong in [('qualified-security-is-not-absolute', 'Die Sicherheit deckt die mängelfreie Herstellung.'),
                                  ('base-price-row-must-state-amount', 'Ein Gebäude mit Planung wird angeboten.')]:
            case = cases[identifier]
            raw = []
            def provider(prompt):
                value = index_answer(decode_prompt(prompt))
                for row in value['verpflichtungen']:
                    row['status'] = case['interpretation_expectations']['allowed_statuses'][0]
                    row['claims'][0]['statement'] = wrong
                raw.append(copy.deepcopy(value))
                return value
            result = collect_historical_facts([document(identifier, case['text'])], SimpleNamespace(extract=provider))
            with self.subTest(case=identifier):
                self.assertEqual(result['status'], 'recorded')
                self.assertEqual(result['review_findings'], [])
                checks = evaluation.interpretation_checks(case, result)
                self.assertFalse(checks['passed'])
                self.assertTrue(checks['missing_fragments'])
                self.assertEqual(result['gemini_responses'][0]['gemini'], raw[0])

