from clause_fixtures import decode_prompt
"""One generation per consequence batch, with full sources and no model review/repair."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from bauvo_working_units.page_flow import extract_page_units, PASS_RULES
from bauvo_working_units.phases.consequences.stage import extract_consequences
from bauvo_working_units.project_facts import collect_project_facts
from bauvo_working_units.recovery import RequestRecovery
from bauvo_working_units.schemas import RESPONSE_SCHEMAS, PHASE_THINKING_LEVELS
from bauvo_working_units.trace import ModelRequestFailed, RawRunRecorder
from consequence_fixtures import claim
from test_consequence_full_context import document


class ConsequenceSinglePassTests(unittest.TestCase):
    def setUp(self):
        self.docs = [document('D1', 'contract', [(1, 'Der Kunde reicht den Antrag ein.')])]
        self.context = [document('D1', 'amendment', [(1, 'Ein gleichwertiger Nachweis genügt ebenfalls.')]),
                        document('D1', 'blb', [(n, f'Vollständige Quellseite {n}.') for n in range(1, 13)], 'blb')]

    def baseline(self, count=1):
        def earlier(prompt):
            data = decode_prompt(prompt); kind = prompt.split('.', 1)[0]
            if kind == 'PAGE_EXTRACTION':
                ref = {'evidence_id': data['evidence_catalog'][0]['evidence_id']}
                return {'working_units': [{'name': 'Antrag', 'action': 'Antrag einreichen',
                    'actor': 'Kunde', 'performed_by': 'Kunde', 'citations': [ref]} for _ in range(count)], 'carry_forward': ''}
            if kind == 'PAGE_OFFERS': return {'offers': []}
            return {'assessments': [{'unit_id': u['id'], 'answer': 'NOT_STATED', 'citations': []}
                                    for u in data['working_units']]}
        return extract_page_units(self.docs, SimpleNamespace(extract=earlier),
            context_documents=self.context, stop_after='PAGE_TIMING',
            project_facts=collect_project_facts([], SimpleNamespace(extract=lambda _: None)))

    def run_stage(self, baseline, provider):
        before = copy.deepcopy(baseline)
        result = extract_consequences(baseline, self.docs, SimpleNamespace(extract=provider), context_documents=self.context)
        self.assertEqual(baseline, before)
        return result

    def test_exactly_one_request_per_five_unit_batch_receives_all_full_documents(self):
        baseline = self.baseline(6); calls = []; answers = []
        def provider(prompt):
            self.assertTrue(prompt.startswith('PAGE_CONSEQUENCES.'))
            data = decode_prompt(prompt); calls.append(data)
            self.assertFalse({'proposed_assessments', 'independent_assessments', 'verification'} & data.keys())
            self.assertEqual(sum(len(d['pages']) for d in data['source_documents']), 14)
            self.assertEqual({d['source_key'] for d in data['source_documents']}, {'contract', 'amendment', 'blb'})
            for original in [*self.docs, *self.context]:
                sent = next(d for d in data['source_documents'] if d['source_key'] == original.source_key)
                self.assertEqual([p['text'] for p in sent['pages']], [p['text'] for p in original.pages])
            self.assertTrue(all('reviews' not in u for u in data['working_units']))
            answer = {'assessments': [{'unit_id': u['id'], 'status': 'NOT_STATED', 'consequences': []}
                                      for u in data['working_units']]}
            answers.append(copy.deepcopy(answer)); return answer
        result = self.run_stage(baseline, provider)
        self.assertEqual([len(c['working_units']) for c in calls], [5, 1])
        added = result['gemini_responses'][len(baseline['gemini_responses']):]
        self.assertEqual([r['pass'] for r in added], ['PAGE_CONSEQUENCES'] * 2)
        self.assertEqual([r['gemini'] for r in added], answers)
        self.assertEqual(result['review_findings'], [])
        for before, after in zip(baseline['working_units'], result['working_units']):
            self.assertEqual(before['gemini'], after['gemini'])
            self.assertEqual(before['reviews'], after['reviews'][:-1])
            self.assertNotIn('consequence_verification', after['derived'])
            self.assertNotIn('verification', after['reviews'][-1]['derived'])

    def test_invalid_evidence_is_flagged_without_any_repair_or_review_call(self):
        baseline = self.baseline(); calls = []; answer = None
        def provider(prompt):
            nonlocal answer
            calls.append(prompt)
            self.assertTrue(prompt.startswith('PAGE_CONSEQUENCES.'))
            data = decode_prompt(prompt)
            answer = {'assessments': [{'unit_id': data['working_units'][0]['id'], 'status': 'DERIVED',
                                      'consequences': [claim({'evidence_id': 'E-not-in-the-catalog'})]}]}
            return copy.deepcopy(answer)
        result = self.run_stage(baseline, provider)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['status'], 'needs_review')
        self.assertIn('unknown_evidence_id', {f['code'] for f in result['review_findings']})
        self.assertEqual(result['gemini_responses'][-1]['gemini'], answer)
        self.assertEqual(result['working_units'][0]['reviews'][-1]['gemini'], answer['assessments'][0])
        self.assertNotIn('repair', result['gemini_responses'][-1]['citation_evidence'])

    def test_missing_and_unknown_task_results_are_preserved_without_regeneration(self):
        for mode in ('missing', 'unknown', 'duplicate'):
            with self.subTest(mode=mode):
                baseline = self.baseline(); calls = []
                def provider(prompt):
                    calls.append(prompt); data = decode_prompt(prompt)
                    row = {'unit_id': data['working_units'][0]['id'], 'status': 'NOT_STATED', 'consequences': []}
                    if mode == 'unknown': row['unit_id'] = 'invented-task'
                    return {'assessments': [] if mode == 'missing' else [row, row] if mode == 'duplicate' else [row]}
                result = self.run_stage(baseline, provider)
                self.assertEqual(len(calls), 1)
                self.assertEqual(result['status'], 'needs_review')
                self.assertIn({'missing': 'missing_review', 'unknown': 'unknown_review_target',
                               'duplicate': 'duplicate_review_target'}[mode], {f['code'] for f in result['review_findings']})

    def test_failed_generation_keeps_failure_without_launching_another_analysis_pass(self):
        baseline = self.baseline(); calls = []
        def provider(prompt):
            calls.append(prompt); raise ModelRequestFailed([{'finish_reason': 'MAX_TOKENS'}])
        result = self.run_stage(baseline, provider)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['status'], 'request_failed')
        self.assertEqual(result['gemini_responses'][-1]['status'], 'request_failed')
        self.assertEqual(result['working_units'], baseline['working_units'])

    def test_successful_batch_is_recovered_after_later_batch_interruption(self):
        baseline = self.baseline(6); calls = []
        class Provider:
            last_raw_response = None
            last_finish_reason = None
            interrupt = True
            def extract(self, prompt):
                self.last_raw_response = None; self.last_finish_reason = None
                data = decode_prompt(prompt); calls.append(data)
                if len(data['working_units']) == 1 and self.interrupt: raise KeyboardInterrupt()
                answer = {'assessments': [{'unit_id': u['id'], 'status': 'NOT_STATED', 'consequences': []}
                                          for u in data['working_units']]}
                self.last_raw_response = json.dumps(answer); self.last_finish_reason = 'STOP'
                return answer
        with TemporaryDirectory() as tmp:
            root = Path(tmp); old = root/'old'; provider = Provider()
            identity = {'project': 'unused', 'location': 'global', 'model': 'fixture'}
            model = RawRunRecorder(RequestRecovery(provider, old, [], identity), old/'raw.json')
            with self.assertRaises(KeyboardInterrupt):
                extract_consequences(baseline, self.docs, model, context_documents=self.context)
            before = {p: p.read_bytes() for p in old.rglob('*') if p.is_file()}
            provider.interrupt = False
            recovery = RequestRecovery(provider, root/'new', [old], identity)
            result = extract_consequences(baseline, self.docs, recovery, context_documents=self.context)
            self.assertEqual(recovery.reused, 1)
            self.assertEqual([len(c['working_units']) for c in calls], [5, 1, 1])
            self.assertEqual(result['status'], 'recorded')
            self.assertEqual(before, {p: p.read_bytes() for p in old.rglob('*') if p.is_file()})

    def test_only_generation_contract_is_registered_for_consequences(self):
        for registry in (RESPONSE_SCHEMAS, PHASE_THINKING_LEVELS, PASS_RULES):
            self.assertEqual([name for name in registry if name.startswith('PAGE_CONSEQUENCES')], ['PAGE_CONSEQUENCES'])
        row = RESPONSE_SCHEMAS['PAGE_CONSEQUENCES']['properties']['assessments']['items']
        self.assertEqual(set(row['required']), {'unit_id', 'status', 'consequences'})
        fields = row['properties']['consequences']['items']['properties']
        self.assertTrue({'trigger', 'conditions', 'exceptions', 'alternatives', 'citations', 'premises', 'reasoning'} <= fields.keys())

    def test_generic_controls_send_only_original_sources_and_tasks_with_the_current_prompt(self):
        root = Path(__file__).resolve().parents[3]
        cases = json.loads((root/'evaluation/consequences/cases.json').read_text())['cases']
        for case in cases:
            with self.subTest(case=case['id']):
                baseline = self.baseline()
                self.context = [document('D1', f'source-{i}', [(1, text)]) for i, text in enumerate(case['sources'])]
                baseline['working_units'][0]['gemini']['action'] = case['task']
                calls = []
                def provider(prompt):
                    self.assertTrue(prompt.startswith('PAGE_CONSEQUENCES.'))
                    self.assertIn(PASS_RULES['PAGE_CONSEQUENCES'], prompt)
                    data = decode_prompt(prompt); calls.append(data)
                    self.assertFalse({'expected', 'expected_review', 'reason', 'corrected', 'proposed'} & data.keys())
                    self.assertEqual(data['working_units'][0]['action'], case['task'])
                    texts = [p['text'] for d in data['source_documents'] for p in d['pages']]
                    self.assertTrue(all(source in texts for source in case['sources']))
                    return {'assessments': [{'unit_id': data['working_units'][0]['id'], 'status': 'NOT_STATED', 'consequences': []}]}
                self.run_stage(baseline, provider)
                self.assertEqual(len(calls), 1)
