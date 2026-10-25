"""Evaluation plumbing preserves evidence; semantic expectations require live review."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]/'evaluation'
sys.path.insert(0, str(BASE))
import project_facts_evaluation as evaluation
from bauvo_working_units.gemini import IncompleteResponse


class ProjectFactsEvaluationTests(unittest.TestCase):
    def test_controls_use_production_pass_without_leaking_expectations_or_rewriting_answers(self):
        cases = json.loads((BASE/'project-facts-cases.json').read_text())['cases']
        self.assertEqual({c['id'] for c in cases}, {'price-binding-with-included-scheduling',
            'independent-offered-table-positions', 'permissions-versus-included-services',
            'condition-in-following-sentence', 'contract-attachment-labelled-offer',
            'both-parties-and-general-payment-rule'})
        calls, answers = [], []
        class Model:
            def extract(self, prompt):
                self_kind, payload = prompt.split('SOURCE DATA:\n')
                self_data = json.loads(payload)
                calls.append((self_kind, self_data))
                c = {'pdf_page': 1, 'text': 'Unverified quotation'}
                # Deliberately questionable proposal: the runner must not improve it.
                answer = {'verpflichtungen': [{'verpflichtung': 'Provider value', 'zitate': [c]}]}
                answers.append(copy.deepcopy(answer))
                return answer
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'results'
            summary = evaluation.run(output, 'unused', model=Model())
            self.assertEqual(len(summary), 6)
            self.assertTrue(all(row['status'] == 'needs_review' for row in summary))
            for case, answer in zip(cases, answers):
                folder = output/case['id']
                result = json.loads((folder/'result.json').read_text())
                self.assertEqual(result['gemini_responses'][0]['gemini'], answer)
                self.assertEqual(result['facts'][0]['gemini'], answer['verpflichtungen'][0])
                self.assertEqual(json.loads((folder/'raw.json').read_text()), [answer])
                self.assertEqual(json.loads((folder/'expectations.json').read_text()), case['expectations'])
                self.assertIn('citation_quote_not_found', {f['code'] for f in result['facts'][0]['review_findings']})
                self.assertEqual(result['facts'][0]['derived']['verified_citations'], [])
            for head, data in calls:
                self.assertTrue(head.startswith('PAGE_PROJECT_FACTS.'))
                self.assertEqual(set(data), {'source_pages'})
                self.assertEqual(len(data['source_pages']), 1)
            self.assertTrue(json.loads((output/'integrity.json').read_text())['inputs_and_code_unchanged'])
            with self.assertRaises(FileExistsError):
                evaluation.run(output, 'unused', model=Model())

    def test_failed_generation_retries_once_and_keeps_failure_before_later_cases(self):
        calls = []
        class Model:
            last_raw_response = None
            last_finish_reason = None
            def extract(self, prompt):
                calls.append(prompt)
                if len(calls) <= 2:
                    self.last_raw_response = '{unfinished'
                    self.last_finish_reason = 'MAX_TOKENS'
                    raise IncompleteResponse('unfinished_generation', 'MAX_TOKENS')
                self.last_raw_response = '{"verpflichtungen": []}'
                self.last_finish_reason = 'STOP'
                return {'verpflichtungen': []}
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'results'
            summary = evaluation.run(output, 'unused', model=Model())
            self.assertEqual(len(calls), 7)
            self.assertEqual(calls[0], calls[1])
            self.assertEqual(summary[0]['status'], 'request_failed')
            self.assertTrue(all(row['status'] == 'recorded' for row in summary[1:]))
            folder = output/summary[0]['case']
            self.assertEqual(json.loads((folder/'raw.json').read_text()), ['{unfinished', '{unfinished'])
            self.assertEqual(len(json.loads((folder/'raw.attempts.json').read_text())), 2)
