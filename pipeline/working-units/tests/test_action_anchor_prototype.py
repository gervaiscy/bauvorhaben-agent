"""Isolated prototype mechanics and joint authored controls, without paid calls."""
import copy
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1] / 'evaluation/action_anchor_prototype'
spec = importlib.util.spec_from_file_location('anchor_prototype_runner', BASE/'runner.py')
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
core = runner.sys.modules['action_anchor_prototype.core']


def source(text='Die Kundin bestellt Bauteile. Der Lieferant liefert Bauteile.'):
    return {'source_key': 'fixture', 'document': {'id': 'D1'}, 'source_pages': [
        {'source_key': 'fixture', 'document_id': 'D1', 'pdf_page': 1, 'primary': True,
         'segments': [['S1', text]]}], 'project_facts': {'facts': []}}


def action(quote='Die Kundin bestellt Bauteile', actor='Kundin', label='S1'):
    return {'source_segment': label, 'action_quote': quote, 'actor': actor}


def expected():
    return {'expected_actions': [
        {'id': 'order', 'anchors': [{'source_segment': 'S1', 'any_of': ['bestellt Bauteile']}], 'actors': ['Kundin']},
        {'id': 'delivery', 'anchors': [{'source_segment': 'S1', 'any_of': ['liefert Bauteile']}], 'actors': ['Lieferant']}
    ], 'forbidden_actions': [], 'context_checks': [{'id': 'whole-source', 'for_actions': ['order'], 'segments': ['S1']}]}


class ActionAnchorCoreTests(unittest.TestCase):
    def test_distinct_anchors_keep_the_same_complete_paragraph_and_original_offsets(self):
        data = source(); raw = {'actions': [action(), action('Der Lieferant liefert Bauteile', 'Lieferant')]}
        before = copy.deepcopy((data, raw))
        result = core.assess(data, raw, expected())
        self.assertEqual(result['status'], 'passes_authored_controls')
        self.assertTrue(result['semantic_review_required'])
        self.assertEqual(result['raw_response'], raw)
        for task in result['tasks']:
            d = task['derived']
            self.assertEqual(d['paragraph_text'], data['source_pages'][0]['segments'][0][1])
            self.assertEqual(d['paragraph_text'][d['start']:d['end']], d['action_quote'])
            self.assertEqual(d['context_role'], 'available_source_context_not_selected_proof')
        self.assertNotEqual(result['tasks'][0]['id'], result['tasks'][1]['id'])
        self.assertEqual((data, raw), before)

    def test_context_cannot_count_as_an_extracted_action(self):
        result = core.assess(source(), {'actions': [action()]}, expected())
        self.assertEqual(result['missing_actions'], ['delivery'])
        self.assertEqual(result['status'], 'needs_review')
        self.assertIn('liefert', result['tasks'][0]['derived']['paragraph_text'])

    def test_broad_anchor_cannot_hide_grouping_behind_full_coverage(self):
        result = core.assess(source(), {'actions': [action(source()['source_pages'][0]['segments'][0][1])]}, expected())
        self.assertEqual(result['missing_actions'], [])
        self.assertEqual(result['grouped_actions'][0]['expected_actions'], ['order', 'delivery'])
        self.assertEqual(result['actor_findings'][0]['expected_action'], 'delivery')
        self.assertEqual(result['status'], 'needs_review')

    def test_wrong_actor_fails_even_when_both_actions_are_present(self):
        result = core.assess(source(), {'actions': [action(actor='Lieferant'),
            action('Der Lieferant liefert Bauteile', 'Lieferant')]}, expected())
        self.assertEqual(result['missing_actions'], [])
        self.assertEqual(len(result['actor_findings']), 1)
        self.assertEqual(result['status'], 'needs_review')

    def test_duplicate_actions_stay_visible_instead_of_being_merged(self):
        result = core.assess(source(), {'actions': [action(), action(), action('Der Lieferant liefert Bauteile', 'Lieferant')]}, expected())
        self.assertEqual(len(result['tasks']), 3)
        self.assertEqual(result['duplicate_actions'], [{'expected_action': 'order', 'tasks': ['A1', 'A2']}])
        self.assertEqual(result['status'], 'needs_review')

    def test_forbidden_exclusion_and_unmatched_candidate_are_reported_separately(self):
        data = source('Malerarbeiten sind ausgeschlossen. Die Telefonzentrale ist erreichbar.')
        oracle = {'expected_actions': [], 'forbidden_actions': [{'id': 'excluded-work',
            'anchors': [{'source_segment': 'S1', 'any_of': ['Malerarbeiten']}]}]}
        result = core.assess(data, {'actions': [action('Malerarbeiten', 'Kundin'),
                                               action('Die Telefonzentrale ist erreichbar', None)]}, oracle)
        self.assertEqual(result['forbidden_action_matches'], [{'task': 'A1', 'controls': ['excluded-work']}])
        self.assertEqual(result['unexpected_actions_requiring_review'], ['A2'])
        self.assertEqual(len(result['tasks']), 2)

    def test_exclusion_inside_a_real_action_quote_is_not_misreported_as_an_extra_task(self):
        text = 'Der Lieferant liefert Bauteile, Malerarbeiten sind ausgeschlossen.'
        oracle = {'expected_actions': [expected()['expected_actions'][1]], 'forbidden_actions': [
            {'id': 'excluded-work', 'anchors': [{'source_segment': 'S1', 'any_of': ['Malerarbeiten']}]}]}
        result = core.assess(source(text), {'actions': [action(text, 'Lieferant')]}, oracle)
        self.assertEqual(result['forbidden_action_matches'], [])

    def test_unknown_ambiguous_and_nonverbatim_anchors_remain_unbound_without_repair(self):
        for quote, label, text, code in (
            ('Bauteile liefern', 'S9', 'Bauteile liefern', 'unknown_segment'),
            ('liefern', 'S1', 'liefern und liefern', 'ambiguous_quote'),
            ('Die Kundin bezahlt', 'S1', 'Die Kundin bestellt', 'missing_quote')):
            with self.subTest(code=code):
                raw = {'actions': [action(quote, None, label)]}
                tasks, findings = core.bind(source(text), raw)
                self.assertEqual(findings[0]['code'], code)
                self.assertIsNone(tasks[0]['derived'])
                self.assertEqual(tasks[0]['raw'], raw['actions'][0])

    def test_whitespace_equivalent_quotes_restore_exact_original_text_and_preserve_raw(self):
        raw = {'actions': [action('Die Kundin bestellt Bauteile')]}
        tasks, findings = core.bind(source('Die Kundin\r\nbestellt\tBauteile.'), raw)
        self.assertEqual(findings, [])
        self.assertEqual(tasks[0]['derived']['action_quote'], 'Die Kundin\r\nbestellt\tBauteile')
        self.assertEqual(tasks[0]['derived']['quote_binding'], 'whitespace_equivalent')
        self.assertEqual(tasks[0]['raw'], raw['actions'][0])

    def test_null_actor_is_retained_and_nonprimary_action_is_flagged(self):
        data = source('Ein Nachweis ist vorzulegen.')
        raw = {'actions': [action('Ein Nachweis ist vorzulegen', None)]}
        tasks, findings = core.bind(data, raw)
        self.assertEqual(findings, [])
        self.assertIsNone(tasks[0]['derived']['actor'])
        data['source_pages'][0]['primary'] = False
        tasks, findings = core.bind(data, raw)
        self.assertEqual(findings[0]['code'], 'action_on_overlap_only_page')

    def test_expectations_and_baseline_never_leak_into_request(self):
        data = {**source(), 'expected_actions': 'ORACLE_VALUE', 'baseline': 'OLD_ANSWER'}
        prompt = core.prompt_for(data)
        self.assertNotIn('ORACLE_VALUE', prompt)
        self.assertNotIn('OLD_ANSWER', prompt)
        wire = json.loads(prompt.split('SOURCE DATA:\n', 1)[1])
        self.assertEqual(wire['source_pages'], data['source_pages'])
        self.assertEqual(wire['project_facts'], data['project_facts'])

    def test_html_escapes_source_markup_and_highlights_original_anchor(self):
        data = source('<script>alert(1)</script> Die Kundin bestellt Bauteile.')
        oracle = expected(); oracle['expected_actions'] = oracle['expected_actions'][:1]
        result = core.assess(data, {'actions': [action()]}, oracle)
        review = core.render_review(data, result)
        self.assertNotIn('<script>', review)
        self.assertIn('&lt;script&gt;', review)
        self.assertIn('<mark>Die Kundin bestellt Bauteile</mark>', review)

    def test_bad_reference_inventory_is_rejected_before_a_request(self):
        oracle = expected(); oracle['expected_actions'][0]['anchors'][0]['any_of'] = ['absent quotation']
        with self.assertRaisesRegex(ValueError, 'absent'):
            core.validate_expectations(source(), oracle)


class ActionAnchorRunnerTests(unittest.TestCase):
    def suite(self, root, name='suite', oracle=None):
        job = {'id': 'control', 'kind': 'fictional', 'source': source(), 'expectations': oracle or expected()}
        with patch.object(runner, 'fixture_jobs', return_value=iter([job])):
            runner.prepare(root/name)
        return root/name

    def backend(self, response=None):
        model = Mock()
        model.last_finish_reason = 'STOP'; model.last_diagnostics = {'fixture': True}
        response = response or {'actions': [action(), action('Der Lieferant liefert Bauteile', 'Lieferant')]}
        model.last_raw_response = json.dumps(response)
        model.extract.return_value = copy.deepcopy(response)
        return model

    def test_default_zero_budget_and_complete_preflight_prevent_client_creation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); suite = self.suite(root); factory = Mock()
            with self.assertRaises(runner.CallLimitExceeded):
                runner.run(suite, root/'run', provider_factory=factory)
            factory.assert_not_called()
            self.assertEqual(runner.read(root/'run/request-plan.json')['new_calls'], 1)

    def test_completed_response_is_cached_before_validation_and_replayed_at_zero_cost(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); suite = self.suite(root)
            raw = {'actions': [action('quote not in source')]}; backend = self.backend(raw)
            first = runner.run(suite, root/'first', max_new_calls=1, provider_factory=lambda: backend)
            self.assertEqual(first['cases'][0]['status'], 'needs_review')
            self.assertEqual(first['calls_used'], 1)
            factory = Mock(side_effect=AssertionError('No provider on replay'))
            second = runner.run(suite, root/'replay', provider_factory=factory)
            factory.assert_not_called(); backend.extract.assert_called_once()
            self.assertEqual(second['calls_used'], 0)
            self.assertEqual(runner.read(root/'replay/control/raw-response.json'), raw)

    def test_changed_local_expectations_reuse_actual_request_identity(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); suite = self.suite(root); backend = self.backend(); store = root/'store'
            runner.run(suite, root/'first', max_new_calls=1, cache_dir=store, provider_factory=lambda: backend)
            oracle = expected(); oracle['expected_actions'][0]['actors'] = ['different party']
            other = self.suite(root, 'other-suite', oracle)
            replay = runner.run(other, root/'replay', cache_dir=store, provider_factory=Mock())
            self.assertEqual(replay['calls_used'], 0)
            self.assertEqual(replay['cases'][0]['counts']['actor_findings'], 1)
            backend.extract.assert_called_once()

    def test_model_identity_change_cannot_reuse_saved_response(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); suite = self.suite(root); backend = self.backend()
            runner.run(suite, root/'first', max_new_calls=1, provider_factory=lambda: backend)
            with self.assertRaises(runner.CallLimitExceeded):
                runner.run(suite, root/'other-model', model='different-model', provider_factory=Mock())

    def test_tampered_provider_response_blocks_before_new_calls(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); suite = self.suite(root); backend = self.backend()
            runner.run(suite, root/'first', max_new_calls=1, provider_factory=lambda: backend)
            record = next((suite/'provider-responses').glob('*.json'))
            data = runner.read(record); data['response']['actions'] = []; runner.write(record, data)
            factory = Mock()
            with self.assertRaisesRegex(ValueError, 'integrity'):
                runner.run(suite, root/'tampered', max_new_calls=1, provider_factory=factory)
            factory.assert_not_called()

    def test_transport_failure_gets_no_retry_and_preserves_failure_evidence(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); suite = self.suite(root); backend = self.backend()
            backend.last_raw_response = '{unfinished'; backend.last_finish_reason = 'MAX_TOKENS'
            backend.extract.side_effect = TimeoutError()
            result = runner.run(suite, root/'failed', max_new_calls=1, provider_factory=lambda: backend)
            self.assertEqual(result['cases'][0]['status'], 'request_failed')
            self.assertEqual((root/'failed/control/raw-response.txt').read_text(), '{unfinished')
            backend.extract.assert_called_once()
            self.assertFalse(list((suite/'provider-responses').glob('*.json')))

    def test_provider_failure_stops_further_new_calls(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = [{'id': f'case-{n}', 'kind': 'fictional', 'source': {**source(), 'source_key': str(n)},
                     'expectations': expected()} for n in range(2)]
            with patch.object(runner, 'fixture_jobs', return_value=iter(jobs)):
                runner.prepare(root/'suite')
            backend = self.backend(); backend.extract.side_effect = TimeoutError()
            result = runner.run(root/'suite', root/'failed', max_new_calls=2, provider_factory=lambda: backend)
            self.assertEqual([c['status'] for c in result['cases']], ['request_failed', 'not_attempted'])
            self.assertEqual(result['calls_used'], 1)
            backend.extract.assert_called_once()

    def test_frozen_inputs_and_previous_outputs_cannot_be_overwritten(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); suite = self.suite(root)
            runner.run(suite, root/'plan', plan_only=True)
            with self.assertRaises(FileExistsError):
                runner.run(suite, root/'plan', plan_only=True)
            (suite/'control/input.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'changed'):
                runner.run(suite, root/'tampered', max_new_calls=1, provider_factory=Mock())

    def test_all_held_out_controls_bind_to_own_sources_without_expected_values_in_wire(self):
        jobs = list(runner.fixture_jobs())
        self.assertEqual(len(jobs), 11)
        for job in jobs:
            core.validate_expectations(job['source'], job['expectations'])
            wire = json.loads(core.prompt_for(job['source']).split('SOURCE DATA:\n', 1)[1])
            self.assertFalse(set(wire) & {'expected_actions', 'forbidden_actions', 'expectations', 'baseline'})
        credit = next(j for j in jobs if j['id'].startswith('task-evidence-credit-reference') and j['id'].endswith('--D1'))
        self.assertEqual(credit['source']['project_facts']['facts'][0]['citations'][0]['document_id'], 'D2')
        self.assertTrue(all(p['document_id'] == 'D1' for p in credit['source']['source_pages']))

    def test_provider_boundary_sends_own_schema_without_changing_production_registry(self):
        from bauvo_working_units.schemas import RESPONSE_SCHEMAS
        before = copy.deepcopy(RESPONSE_SCHEMAS)
        raw = {'actions': [action()]}
        response = SimpleNamespace(text=json.dumps(raw), candidates=[SimpleNamespace(finish_reason='STOP')],
                                   usage_metadata=None, model_version='fixture-version', response_id='fixture-id')
        client = Mock(); client.models.generate_content.return_value = response
        with patch('google.genai.Client', return_value=client):
            backend = runner.PrototypeModel('project', 'global', 'fixture-model')
            self.assertEqual(backend.extract('prototype prompt'), raw)
        config = client.models.generate_content.call_args.kwargs['config']
        self.assertEqual(config.response_json_schema, core.SCHEMA)
        self.assertEqual(config.thinking_config.thinking_level.value, 'MEDIUM')
        self.assertEqual(RESPONSE_SCHEMAS, before)
