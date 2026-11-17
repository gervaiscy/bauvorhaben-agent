"""Readable timing exports retain lossless evidence and downstream handoffs."""
import copy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clause_fixtures import decode_prompt
from bauvo_working_units.phases.timing.artifact import (
    load_artifact, output_paths, review_view, validation_view, write_outputs,
)


class TimingArtifactTests(unittest.TestCase):
    def setUp(self):
        self.citation = {'source_key': 'blb', 'document_id': 'D1', 'pdf_page': 1,
                         'quote': 'Vor Baubeginn.', 'start': 0, 'end': 15,
                         'page_sha256': 'original-page', 'evidence_id': 'original-evidence'}
        row = {'unit_id': 'U1', 'answer': 'Vor Baubeginn.',
               'citations': [{'fact_id': 'F1'}]}
        self.unit = {'id': 'U1', 'gemini': {'title': 'Wasser bereitstellen', 'actor': 'Bauherr',
                    'quote': 'Wasser bereitstellen.'},
                    'derived': {'source_text': 'Wasser\nbereitstellen.', 'source_key': 'blb',
                                'document_id': 'D1', 'document_title': 'Leistungsbeschreibung',
                                'response_id': 'E1', 'response_position': 0},
                    'reviews': [{'id': 'T1:review:000', 'response_id': 'T1', 'kind': 'PAGE_TIMING',
                                 'gemini': row, 'derived': {'resolved_evidence': {
                                     **copy.deepcopy(row), 'citations': [self.citation,
                                         {**self.citation, 'source_key': 'contract', 'quote': 'Ausnahme.'}]}}}]}
        self.result = {'status': 'needs_review', 'completed_passes': ['PAGE_TIMING'],
                       'working_units': [self.unit], 'offers': [{'original': 'offer'}],
                       'project_facts': {'source_documents': [{'source_key': 'contract',
                           'document_id': 'D1', 'title': 'Vertrag'}]},
                       'gemini_responses': [{'id': 'T1', 'gemini': {'assessments': [row]}},
                           {'id': 'E1', 'gemini': {'working_units': [self.unit['gemini']]}}],
                       'review_findings': [
                           {'response_id': 'T1', 'row': 0, 'code': 'timing_answer_without_citation'},
                           {'response_id': 'T1', 'path': '$.assessments[0].citations[0]',
                            'code': 'unknown_fact_id'},
                           {'response_id': 'E1', 'path': '$.working_units[0].quote',
                            'code': 'task_quote_not_found'},
                           {'code': 'model_request_failed', 'response_id': 'failed'}]}

    def test_readable_view_has_task_and_timing_with_distinguishable_original_sources(self):
        before = copy.deepcopy(self.result)
        view = review_view(self.result)
        self.assertEqual(set(view), {'status', 'working_units'})
        unit = view['working_units'][0]
        self.assertEqual(set(unit), {'id', 'title', 'actor', 'quote', 'timing'})
        self.assertEqual(unit['quote'], 'Wasser\nbereitstellen.')
        self.assertEqual(unit['actor'], 'Bauherr')
        self.assertEqual(unit['timing'], {'answer': 'Vor Baubeginn.', 'citations': [
            {'document': 'Leistungsbeschreibung', 'pdf_page': 1, 'quote': 'Vor Baubeginn.'},
            {'document': 'Vertrag', 'pdf_page': 1, 'quote': 'Ausnahme.'}]})
        view['working_units'][0]['timing']['citations'].clear()
        self.assertEqual(self.result, before)

    def test_unbound_task_unknown_reference_and_missing_or_duplicate_assessments_stay_visible(self):
        del self.unit['derived']['source_text']
        self.unit['gemini']['actor'] = None
        self.unit['reviews'][0]['derived'] = {}
        self.unit['reviews'][0]['gemini']['citations'] = [{'fact_id': 'UNKNOWN'}]
        self.unit['reviews'].append(copy.deepcopy(self.unit['reviews'][0]))
        unit = review_view(self.result)['working_units'][0]
        self.assertFalse(unit['quote_verified'])
        self.assertIsNone(unit['actor'])
        self.assertEqual(unit['quote'], self.unit['gemini']['quote'])
        self.assertEqual(unit['timing']['citations'], [{'fact_id': 'UNKNOWN'}])
        self.assertEqual(len(unit['additional_timing_assessments']), 1)
        self.unit['reviews'] = []
        self.result['status'] = 'request_failed'
        view = review_view(self.result)
        self.assertEqual(view['status'], 'request_failed')
        self.assertIsNone(view['working_units'][0]['timing'])

    def test_findings_get_target_ids_without_losing_original_findings(self):
        before = copy.deepcopy(self.result)
        report = validation_view(self.result)
        self.assertEqual([f.get('unit_id') for f in report['findings']], ['U1', 'U1', 'U1', None])
        for raw, enriched in zip(before['review_findings'], report['findings']):
            self.assertTrue(raw.items() <= enriched.items())
        self.assertEqual(self.result, before)

    def test_lossless_audit_round_trip_and_legacy_loading(self):
        with TemporaryDirectory() as tmp:
            output = Path(tmp)/'new/custom.json'
            audit = write_outputs(output, self.result)
            self.assertEqual(audit.name, 'custom.audit.json')
            self.assertEqual(json.loads(audit.read_text()), self.result)
            self.assertEqual(load_artifact(output), self.result)
            self.assertEqual(load_artifact(audit), self.result)
            self.assertEqual(json.loads(output.read_text()), review_view(self.result))
            self.assertEqual(json.loads(output_paths(output)[2].read_text()), validation_view(self.result))

    def test_existing_sidecars_are_never_overwritten_and_mismatched_review_is_rejected(self):
        with TemporaryDirectory() as tmp:
            output = Path(tmp)/'custom.json'
            for collision in output_paths(output):
                collision.write_text('original evidence')
                with self.assertRaises(FileExistsError):
                    write_outputs(output, self.result)
                self.assertEqual(collision.read_text(), 'original evidence')
                self.assertEqual(list(Path(tmp).iterdir()), [collision])
                collision.unlink()
            write_outputs(output, self.result)
            review = json.loads(output.read_text())
            review['working_units'][0]['timing']['answer'] = 'Changed'
            output.write_text(json.dumps(review))
            with self.assertRaisesRegex(ValueError, 'does not match'):
                load_artifact(output)
            output_paths(output)[1].unlink()
            with self.assertRaisesRegex(ValueError, 'requires its audit'):
                load_artifact(output)


class TimingArtifactCliTests(unittest.TestCase):
    def test_cli_outputs_resume_and_both_downstream_entrypoints_use_the_matching_audit(self):
        from test_schema6_flow import fixture
        from bauvo_working_units.project_flow import load_documents
        from bauvo_working_units.page_flow import extract_page_units
        from bauvo_working_units.project_facts import collect_project_facts
        from bauvo_working_units.phases.timing.stage import main as timing_main
        from bauvo_working_units.phases.consequences.stage import main as consequences_main
        from bauvo_working_units.phases.applicability.stage import main as applicability_main

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root, count=1)
            docs = load_documents(root/'pages.json', root/'documents.json')
            quote = docs[0].pages[0]['text']
            def earlier(prompt):
                return ({'working_units': [{'title': 'Aufgabe', 'actor': None, 'quote': quote}]}
                        if prompt.startswith('PAGE_EXTRACTION.') else {'offers': []})
            facts = collect_project_facts([], SimpleNamespace(extract=lambda p: self.fail('No fact sources')))
            baseline = extract_page_units(docs, SimpleNamespace(extract=earlier),
                                          project_facts=facts, stop_after='PAGE_OFFERS')
            (root/'baseline.json').write_text(json.dumps(baseline))
            calls = []
            class Provider:
                last_raw_response = None
                last_finish_reason = 'STOP'
                def extract(self, prompt):
                    calls.append(prompt)
                    fields = ({'answer': 'NOT_STATED', 'citations': []} if prompt.startswith('PAGE_TIMING.')
                              else {'status': 'NOT_STATED', 'consequences': [], 'citations': []}
                              if prompt.startswith('PAGE_CONSEQUENCES.')
                              else {'applicability': 'UNCLEAR', 'reason': 'Keine Festlegung', 'citations': []})
                    response = {'assessments': [{'unit_id': u['id'], **fields}
                        for u in decode_prompt(prompt)['working_units']]}
                    self.last_raw_response = json.dumps(response, ensure_ascii=False)
                    return response
            def run(main, source, output, extra=(), limit=1):
                argv = ['stage', '--working-units', str(source), '--pages', str(root/'pages.json'),
                        '--documents', str(root/'documents.json'), '--output', str(output),
                        '--project', 'test', '--max-new-calls', str(limit), *extra]
                with patch('sys.argv', argv), patch('sys.stdout', new=io.StringIO()), patch(
                        'bauvo_working_units.gemini.GeminiModel', return_value=Provider()):
                    main()
            output = root/'timing/result.json'
            run(timing_main, root/'baseline.json', output)
            review = json.loads(output.read_text())
            self.assertEqual(review['working_units'][0]['timing'], {'answer': 'NOT_STATED', 'citations': []})
            self.assertNotIn('gemini_responses', review)
            audit = load_artifact(output)
            self.assertEqual(audit['offers'], baseline['offers'])
            self.assertEqual(audit['working_units'][0]['gemini'], baseline['working_units'][0]['gemini'])
            raw = output.with_name('result.gemini-raw.json')
            original_raw = raw.read_bytes()
            self.assertEqual(json.loads(original_raw), [audit['gemini_responses'][-1]['gemini']])
            replay = root/'replay/result.json'
            run(timing_main, root/'baseline.json', replay, ['--resume-from', str(output)], limit=0)
            self.assertEqual(json.loads(replay.read_text()), review)
            self.assertEqual(load_artifact(replay), audit)
            self.assertEqual(len(calls), 1)
            for main, name in ((consequences_main, 'consequences'), (applicability_main, 'applicability')):
                following = root/name/'result.json'
                run(main, output, following)
                full = json.loads(following.read_text())
                self.assertEqual(full['working_units'][0]['reviews'][0], audit['working_units'][0]['reviews'][0])
            self.assertEqual(len(calls), 3)
            self.assertEqual(raw.read_bytes(), original_raw)

    def test_cli_rejects_sidecar_collisions_before_loading_inputs_or_contacting_provider(self):
        from bauvo_working_units.phases.timing.stage import main
        for suffix in ('.audit.json', '.validation.json', '.partial.json'):
            with self.subTest(suffix=suffix), TemporaryDirectory() as tmp:
                output = Path(tmp)/'result.json'
                sidecar = output.with_name('result' + suffix)
                sidecar.write_text('original evidence')
                argv = ['timing', '--working-units', 'unused', '--pages', 'unused',
                        '--documents', 'unused', '--project', 'test', '--output', str(output)]
                with patch('sys.argv', argv), patch('sys.stderr', new=io.StringIO()), self.assertRaises(SystemExit):
                    main()
                self.assertEqual(sidecar.read_text(), 'original evidence')
                self.assertFalse(output.exists())
