"""Current homeowner-rights contract through real orchestration, using offline providers."""
import copy
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from bauvo_working_units.cache import CachedModel, provider_request
from bauvo_working_units.citation_evidence import catalog
from bauvo_working_units.call_budget import BudgetedModel, CallBudget, CallLimitExceeded
from bauvo_working_units.cli import main
from bauvo_working_units.fact_artifact import validate_facts
from bauvo_working_units.page_flow import _prompt, extract_page_units
from bauvo_working_units.phases.project_facts.index import complete_response
from bauvo_working_units.phases.project_facts.payload import expand_payload
from bauvo_working_units.phases.project_facts.revalidation import replay_project_facts
from bauvo_working_units.phases.project_facts.rights import EVIDENCE_PROTOCOL, EXTRACTION_SCOPE, target_batches
from bauvo_working_units.project_facts import collect_project_facts, facts_view, evidence_pages
from bauvo_working_units.project_flow import Document, load_documents
from bauvo_working_units.schemas import RESPONSE_SCHEMAS
from bauvo_working_units.trace import ModelRequestFailed, RawRunRecorder, RetryingModel
from bauvo_working_units.gemini import IncompleteResponse
from test_schema6_flow import fixture


def document(text, key='contract', page=1, identifier='D1'):
    return Document(identifier, 'Vertrag', key+'.pdf', schema_version=6, source_key=key,
                    pages=({'pdf_page': page, 'text': text},))


def payload(prompt):
    return json.loads(prompt.split('SOURCE DATA:\n', 1)[1])


def answer(statement='Der Bauherr hat Anspruch auf die vereinbarte Leistung.', refs=('S1',)):
    return {'ansprueche': [{'aussage': statement, 'belege': list(refs)}]}


def recorded(docs, raw):
    return collect_project_facts(docs, SimpleNamespace(extract=lambda _: copy.deepcopy(raw)))


class ContractualRightsTests(unittest.TestCase):
    def test_live_schema_is_only_statement_and_evidence_without_model_ids(self):
        schema = RESPONSE_SCHEMAS['PAGE_PROJECT_FACTS']
        self.assertEqual(schema['required'], ['ansprueche'])
        row = schema['properties']['ansprueche']['items']
        self.assertEqual(row['required'], ['aussage', 'belege'])
        self.assertEqual(row['properties']['belege']['minItems'], 1)
        self.assertFalse(row['additionalProperties'])

    def test_prompt_has_only_short_source_labels_and_plain_german_examples(self):
        calls = []
        doc = document('Der Auftragnehmer schuldet eine Bodenplatte.\n\nFirmenlogo')
        result = collect_project_facts([doc], SimpleNamespace(extract=lambda p: (calls.append(p), answer())[1]))
        prompt = calls[0]
        instructions = prompt.split('SOURCE DATA:\n')[0]
        data = payload(prompt)
        self.assertIn('Rechtsanwalts', instructions)
        current_prompt = Path(__file__).resolve().parents[1]/'src/bauvo_working_units/phases/project_facts/prompt.txt'
        self.assertIn(current_prompt.read_text().rstrip(), instructions)
        self.assertIn('FIKTIVE BEISPIELE', instructions)
        self.assertNotIn('```json', instructions)
        for field in ('provisions', 'target_provision_ids', 'provision_id', 'related_provision_ids',
                      'claims', 'nicht_als_fakt_erfasst', 'status', 'evidence_catalog'):
            self.assertNotIn(field, data)
        self.assertEqual(data['target_evidence_ids'], ['S1', 'S2'])
        self.assertEqual(expand_payload(data)['source_pages'][0]['text'], doc.pages[0]['text'])
        self.assertNotIn(next(iter(result['reference_map'].values())), prompt)
        self.assertNotIn('600 EUR', json.dumps(data))

    def test_sparse_selection_does_not_require_exclusions_and_all_source_text_remains_citable(self):
        doc = document('Der Auftragnehmer schuldet eine Bodenplatte.\n\nFirmenlogo\n\nKontakt')
        result = recorded([doc], answer())
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['review_findings'], [])
        self.assertEqual(result['excluded_passages'], [])
        self.assertNotIn('coverage', result['gemini_responses'][0])
        self.assertEqual(evidence_pages(result)[0]['text'], doc.pages[0]['text'])
        self.assertEqual(validate_facts(result, [doc]), result)

    def test_no_rights_is_valid_without_implying_semantic_completeness(self):
        doc = document('Firmenlogo\n\nDer Bauherr stellt Wasser bereit.')
        result = recorded([doc], {'ansprueche': []})
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['facts'], [])
        self.assertEqual(result['processing'], {'planned_batches': 1, 'completed_batches': 1,
                                              'semantic_completeness': 'not_verified'})
        self.assertEqual(validate_facts(result, [doc]), result)

    def test_all_context_is_sent_in_each_bounded_batch_without_provision_catalog(self):
        docs = [document('\n\n'.join(f'Leistung {n}.' for n in range(101))),
                document('Nachtrag mit Ausnahme.', 'amendment')]
        calls = []
        def provider(prompt):
            data = payload(prompt)
            calls.append(data)
            return answer(refs=[data['target_evidence_ids'][0], 'S102'])
        result = collect_project_facts(docs, SimpleNamespace(extract=provider), chunk_pages=1, max_chars=1)
        self.assertEqual([len(p['target_evidence_ids']) for p in calls], [50, 50, 2])
        self.assertTrue(all(p['source_pages'] == calls[0]['source_pages'] for p in calls))
        self.assertEqual(len(result['facts']), 3)
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(validate_facts(result, docs), result)

    def test_internal_section_boundary_is_not_split_to_meet_target_size(self):
        doc = document('§ 1 Umfang\n'+'\n'.join(f'Bedingung {n}.' for n in range(60))+'\n§ 2 Preis\nPreis.')
        calls = []
        result = collect_project_facts([doc], SimpleNamespace(extract=lambda p:
            (calls.append(payload(p)), {'ansprueche': []})[1]))
        self.assertEqual([len(p['target_evidence_ids']) for p in calls], [61, 2])
        self.assertEqual(result['status'], 'recorded')

    def test_amendment_supports_one_authoritative_interpretation_across_colliding_source_ids(self):
        docs = [document('Übergabe in 30 Wochen.', 'contract'),
                document('Nachtrag: Übergabe in 34 Wochen.', 'amendment')]
        raw = answer('Der Auftragnehmer schuldet die Übergabe innerhalb von 34 Wochen gemäß Nachtrag.', ['S1', 'S2'])
        result = recorded(docs, raw)
        self.assertEqual(result['gemini_responses'][0]['gemini'], raw)
        fact = result['facts'][0]
        self.assertEqual(fact['derived']['interpretation'], raw['ansprueche'][0]['aussage'])
        self.assertIsNone(fact['derived']['source_key'])
        self.assertEqual({c['source_key'] for c in fact['derived']['verified_citations']}, {'contract', 'amendment'})
        self.assertNotIn('claims', fact['derived'])
        self.assertEqual(validate_facts(result, docs), result)

    def test_duplicate_references_are_normalized_only_in_derived_citations(self):
        doc = document('Die Sicherheit gilt ohne wesentliche Mängel.\nDie Ausnahme bleibt erhalten.')
        raw = answer('Sicherheit ohne wesentliche Mängel unter Beibehaltung der Ausnahme.', ['S2', 'S1', 'S2'])
        before = copy.deepcopy(raw)
        result = recorded([doc], raw)
        citations = result['facts'][0]['derived']['verified_citations']
        self.assertEqual([c['quote'] for c in citations], [p['quote'] for p in catalog([
                {**doc.pages[0], 'source_key': 'contract', 'document_id': 'D1'}])])
        self.assertEqual(result['gemini_responses'][0]['gemini'], before)
        self.assertEqual(result['facts'][0]['gemini'], before['ansprueche'][0])
        self.assertEqual(raw, before)
        self.assertEqual(result['status'], 'recorded')

    def test_unknown_empty_malformed_and_old_responses_block_without_repair_or_discarding_raw(self):
        doc = document('Vertragliche Leistung.')
        for raw in (answer(refs=['S999']), answer(refs=[]), answer(refs=[None]),
                    answer(statement=''), {'ansprueche': [{'aussage':'Claim', 'belege':['S1'], 'unklarheit':None}]}, {'ansprueche': [None]},
                    {'ansprueche': None}, {'verpflichtungen': [], 'nicht_als_fakt_erfasst': []}):
            with self.subTest(raw=raw):
                calls = []
                result = collect_project_facts([doc], SimpleNamespace(extract=lambda p:
                    (calls.append(p), copy.deepcopy(raw))[1]))
                self.assertEqual(len(calls), 1)
                self.assertEqual(result['status'], 'incomplete')
                self.assertEqual(result['gemini_responses'][0]['gemini'], raw)
                self.assertEqual(validate_facts(result, [doc]), result)
                self.assertFalse(complete_response(calls[0], raw))

    def test_full_context_can_be_the_only_evidence_outside_the_assigned_batch(self):
        docs = [document('\n\n'.join(f'Leistung {n}.' for n in range(51)))]
        prompts = []
        raw = answer(refs=['S51'])
        result = collect_project_facts(docs, SimpleNamespace(extract=lambda p:
            (prompts.append(p), copy.deepcopy(raw))[1]))
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['review_findings'], [])
        self.assertEqual(len(result['facts']), 2)
        self.assertEqual(validate_facts(result, docs), result)
        self.assertTrue(all(complete_response(prompt, raw) for prompt in prompts))
        self.assertTrue(all(r['gemini'] == raw for r in result['gemini_responses']))

    def historical_scope_artifact(self, docs, raw, fail_second=False):
        from bauvo_working_units.phases.project_facts.rights import records
        calls = []
        def provider(prompt):
            calls.append(prompt)
            if fail_second and len(calls) == 2:
                raise ModelRequestFailed([{'error_type': 'ReadTimeout'}])
            return copy.deepcopy(raw)
        result = collect_project_facts(docs, SimpleNamespace(extract=provider))
        result.pop('validation_protocol')
        result['facts'] = []
        result['review_findings'] = []
        for response in result['gemini_responses']:
            if response.get('status') == 'request_failed':
                result['review_findings'].append(response['failure'])
                continue
            metadata = {k: response[k] for k in ('context_scope', 'sources', 'target_evidence_ids')}
            facts, validation, issues = records(response['gemini'], result['source_documents'],
                metadata, response['id'], _historical_target_validation=True)
            result['facts'].extend(facts)
            result['review_findings'].extend(issues)
            response['response_validation'] = validation
        result['status'] = ('request_failed' if fail_second else
                            'incomplete' if result['review_findings'] else 'recorded')
        self.assertEqual(validate_facts(result, docs), result)
        return result

    def test_offline_replay_retires_scope_failures_without_changing_raw_evidence_or_sources(self):
        docs = [document('\n\n'.join(f'Leistung {n}.' for n in range(51)))]
        old = self.historical_scope_artifact(docs, answer(refs=['S51']))
        before = copy.deepcopy(old)
        self.assertEqual(old['status'], 'incomplete')
        replayed = replay_project_facts(old, docs)
        self.assertEqual(replayed['status'], 'recorded')
        self.assertEqual(replayed['review_findings'], [])
        self.assertEqual(replayed['validation_history'][-1]['review_findings'], old['review_findings'])
        self.assertEqual(replayed['validation_history'][-1]['previous_status'], 'incomplete')
        self.assertEqual([r['gemini'] for r in replayed['gemini_responses']],
                         [r['gemini'] for r in old['gemini_responses']])
        self.assertEqual([f['derived'] for f in replayed['facts']], [f['derived'] for f in old['facts']])
        self.assertEqual(replayed['source_documents'], old['source_documents'])
        self.assertEqual(replayed['source_snapshots'], old['source_snapshots'])
        self.assertEqual(validate_facts(replayed, docs), replayed)
        self.assertEqual(old, before)

    def test_scope_revalidation_keeps_invalid_evidence_and_failed_requests_blocking(self):
        docs = [document('\n\n'.join(f'Leistung {n}.' for n in range(51)))]
        for raw, failed, code in ((answer(refs=['S51','S999']), False, 'unknown_evidence_id'),
                                  (answer(refs=[]), False, 'missing_right_evidence'),
                                  (answer(refs=['S51']), True, 'model_request_failed')):
            old = self.historical_scope_artifact(docs, raw, fail_second=failed)
            replayed = replay_project_facts(old, docs)
            with self.subTest(code=code):
                self.assertEqual(replayed['status'], 'request_failed' if failed else 'incomplete')
                self.assertIn(code, {i['code'] for i in replayed['review_findings']})
                self.assertNotIn('right_without_target_evidence', {i['code'] for i in replayed['review_findings']})
                self.assertEqual([r.get('gemini') for r in replayed['gemini_responses']],
                                 [r.get('gemini') for r in old['gemini_responses']])

    def test_historical_scope_replay_rejects_altered_citations_before_revalidation(self):
        docs = [document('\n\n'.join(f'Leistung {n}.' for n in range(51)))]
        old = self.historical_scope_artifact(docs, answer(refs=['S51']))
        old['facts'][0]['derived']['verified_citations'][0]['quote'] = 'Invented'
        with self.assertRaises(ValueError):
            replay_project_facts(old, docs)

    def test_failed_batch_preserves_completed_rights_and_blocks_downstream(self):
        doc = document('\n\n'.join(f'Leistung {n}.' for n in range(51)))
        calls = []
        def provider(prompt):
            calls.append(prompt)
            if len(calls) == 2:
                raise ModelRequestFailed([{'error_type': 'ReadTimeout'}])
            return answer()
        result = collect_project_facts([doc], SimpleNamespace(extract=provider))
        self.assertEqual(result['status'], 'request_failed')
        self.assertEqual(len(result['facts']), 1)
        self.assertEqual(result['processing']['completed_batches'], 1)
        self.assertEqual(validate_facts(result, [doc]), result)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            extract_page_units([doc], SimpleNamespace(extract=lambda p: self.fail('Unexpected downstream call')),
                               project_facts=result)

    def test_tampered_evidence_mapping_interpretation_scope_and_batches_are_rejected(self):
        doc = document('Der Bauherr hat Anspruch auf Übergabe.')
        result = recorded([doc], answer())
        for kind in ('quote', 'interpretation', 'mapping', 'scope', 'processing', 'response', 'fact', 'source'):
            changed = copy.deepcopy(result)
            if kind == 'quote': changed['facts'][0]['derived']['verified_citations'][0]['quote'] = 'Invented'
            elif kind == 'interpretation': changed['facts'][0]['derived']['interpretation'] = 'Invented'
            elif kind == 'mapping': changed['reference_map']['S1'] = 'Invented'
            elif kind == 'scope': changed['extraction_scope'] = 'all_project_information'
            elif kind == 'processing': changed['processing']['semantic_completeness'] = 'verified'
            elif kind == 'response': changed['gemini_responses'] = []
            elif kind == 'fact': changed['facts'] = []
            else: changed['source_documents'][0]['pages'][0]['text'] = 'Changed'
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_facts(changed, [doc])
        with self.assertRaises(ValueError):
            validate_facts(result, [replace(doc, pages=({'pdf_page': 1, 'text': 'Changed'},))])

    def test_rights_with_open_questions_in_statement_reach_all_five_downstream_passes(self):
        doc = document('Eine Gutschrift ist bei fristgerechter Zahlung vereinbart.')
        raw = answer('Bedingte Gutschrift bei fristgerechter Zahlung. Die vertragliche Höhe bleibt offen.')
        result = recorded([doc], raw)
        self.assertNotIn('uncertainty', result['facts'][0]['derived'])
        for phase in ('PAGE_EXTRACTION', 'PAGE_OFFERS', 'PAGE_TIMING', 'PAGE_CONSEQUENCES', 'PAGE_APPLICABILITY'):
            data = payload(_prompt(phase, {'source_pages': [], 'project_facts': facts_view(result)}, 'de'))
            view = data['project_facts']
            self.assertEqual(view['extraction_scope'], EXTRACTION_SCOPE)
            self.assertEqual(view['processing']['semantic_completeness'], 'not_verified')
            self.assertEqual(view['facts'][0]['interpretation'], raw['ansprueche'][0]['aussage'])
            self.assertNotIn('uncertainty', view['facts'][0])
            self.assertNotIn('unklarheit', view['facts'][0])
            if phase == 'PAGE_EXTRACTION':
                self.assertNotIn('source_documents', view)
                self.assertEqual(view['facts'][0]['citations'][0]['quote'], doc.pages[0]['text'])
            else:
                self.assertEqual(view['source_documents'][0]['pages'][0]['text'], doc.pages[0]['text'])

    def test_historical_schema14_uncertainty_remains_unchanged_when_loaded_and_replayed(self):
        from bauvo_working_units.phases.project_facts.rights import records, LEGACY_EVIDENCE_PROTOCOL
        doc = document('Ein zusätzliches Zimmer ist in einer Zeichnung dargestellt.')
        old = recorded([doc], answer('Die Zeichnung beschreibt ein zusätzliches Zimmer.'))
        old.update(schema_version=14, evidence_protocol=LEGACY_EVIDENCE_PROTOCOL,
                   extraction_scope='homeowner_contractual_rights')
        response = old['gemini_responses'][0]
        response['gemini']['ansprueche'][0]['unklarheit'] = 'Die vertragliche Einbeziehung ist nicht belegt.'
        metadata = {k: response[k] for k in ('context_scope', 'sources', 'target_evidence_ids')}
        facts, validation, issues = records(response['gemini'], old['source_documents'], metadata,
            response['id'], evidence_protocol=LEGACY_EVIDENCE_PROTOCOL)
        old.update(facts=facts, review_findings=issues)
        response['response_validation'] = validation
        before = copy.deepcopy(old)
        self.assertEqual(validate_facts(old, [doc]), old)
        replayed = replay_project_facts(old, [doc])
        self.assertEqual(replayed['schema_version'], 14)
        self.assertEqual(replayed['gemini_responses'], before['gemini_responses'])
        self.assertEqual(replayed['facts'], before['facts'])
        self.assertEqual(facts_view(replayed)['facts'][0]['uncertainty'],
                         before['gemini_responses'][0]['gemini']['ansprueche'][0]['unklarheit'])
        self.assertEqual(old, before)

    def test_rights_only_schema15_replays_with_original_scope_and_raw_answers(self):
        from bauvo_working_units.phases.project_facts.rights import RIGHTS_ONLY_EVIDENCE_PROTOCOL
        doc = document('Der Bauherr stellt Bauwasser bereit.')
        old = recorded([doc], {'ansprueche': []})
        old.update(evidence_protocol=RIGHTS_ONLY_EVIDENCE_PROTOCOL,
                   extraction_scope='homeowner_contractual_rights')
        before = copy.deepcopy(old)
        replayed = replay_project_facts(old, [doc])
        self.assertEqual(replayed['extraction_scope'], 'homeowner_contractual_rights')
        self.assertEqual(replayed['evidence_protocol'], RIGHTS_ONLY_EVIDENCE_PROTOCOL)
        self.assertEqual(replayed['gemini_responses'], before['gemini_responses'])
        self.assertEqual(replayed['facts'], [])
        self.assertEqual(old, before)
        old['extraction_scope'] = EXTRACTION_SCOPE
        with self.assertRaises(ValueError):
            validate_facts(old, [doc])

    def test_permissions_duties_and_formal_rules_reach_every_downstream_pass_unchanged(self):
        statements = ['Der Auftragnehmer darf für fünf Monate nach Abnahme ein Werbeschild aufstellen.',
                      'Der Bauherr muss vor Beginn der Erdarbeiten Bauwasser bereitstellen.',
                      'Verbindliche Erklärungen der Vertriebsgesellschaft bedürfen der schriftlichen Bestätigung ihrer Geschäftsführung.']
        doc = document('\n'.join(statements))
        raw = {'ansprueche': [{'aussage': statement, 'belege': [f'S{number}']}
                             for number, statement in enumerate(statements, 1)]}
        result = recorded([doc], raw)
        self.assertEqual(result['extraction_scope'], 'homeowner_contractual_position')
        self.assertEqual(result['evidence_protocol'], 'contractual-rights-v3')
        self.assertEqual(validate_facts(result, [doc]), result)
        self.assertEqual(result['gemini_responses'][0]['gemini'], raw)
        for phase in ('PAGE_EXTRACTION', 'PAGE_OFFERS', 'PAGE_TIMING', 'PAGE_CONSEQUENCES', 'PAGE_APPLICABILITY'):
            view = payload(_prompt(phase, {'source_pages': [], 'project_facts': facts_view(result)}, 'de'))['project_facts']
            with self.subTest(phase=phase):
                self.assertEqual(view['extraction_scope'], EXTRACTION_SCOPE)
                self.assertEqual([f['interpretation'] for f in view['facts']], statements)
                self.assertEqual([f['citations'][0]['quote'] for f in view['facts']],
                                 [statement+'\n' for statement in statements[:-1]]+statements[-1:])

    def test_blb_is_excluded_as_before_and_empty_package_makes_no_requests(self):
        blb = replace(document('Allgemeine Beschreibung.', 'blb'), source_type='blb')
        result = collect_project_facts([blb], SimpleNamespace(extract=lambda p: self.fail('Unexpected provider call')))
        self.assertEqual(result['status'], 'no_project_sources')
        self.assertEqual(result['gemini_responses'], [])
        self.assertEqual(validate_facts(result, [blb]), result)

    def test_completed_invalid_response_replays_offline_without_paid_retry_or_raw_changes(self):
        doc = document('Vertragliche Leistung.')
        raw = answer(refs=['S999'])
        with TemporaryDirectory() as tmp:
            cache = Path(tmp)/'cache'
            calls = []
            backend = SimpleNamespace(extract=lambda p: (calls.append(p), copy.deepcopy(raw))[1])
            first = collect_project_facts([doc], CachedModel(backend, cache, {'model': 'fixture'}))
            original = {p: p.read_bytes() for p in cache.glob('*.json')}
            offline = BudgetedModel(lambda: self.fail('Unexpected client construction'), CallBudget(0))
            second = collect_project_facts([doc], CachedModel(offline, cache, {'model': 'fixture'}))
            self.assertEqual(first, second)
            self.assertEqual(first['status'], 'incomplete')
            self.assertEqual(len(calls), 1)
            replayed = replay_project_facts(first, [doc])
            self.assertEqual(replayed['schema_version'], 15)
            self.assertEqual(replayed['gemini_responses'], first['gemini_responses'])
            self.assertEqual(replayed['facts'], first['facts'])
            self.assertEqual(original, {p: p.read_bytes() for p in original})

    def test_entire_stage_preflight_enforces_budget_before_constructing_provider(self):
        doc = document('\n\n'.join(f'Leistung {n}.' for n in range(51)))
        with TemporaryDirectory() as tmp:
            budget = CallBudget(1)
            backend = BudgetedModel(lambda: self.fail('Unexpected client construction'), budget)
            cache = CachedModel(backend, Path(tmp)/'cache', {}, plan_path=Path(tmp)/'plan.json')
            with self.assertRaises(CallLimitExceeded): collect_project_facts([doc], cache)
            self.assertEqual(budget.used, 0)
            self.assertEqual(json.loads((Path(tmp)/'plan.json').read_text())[0]['planned_new_calls'], 2)

    def test_raw_file_contains_only_exact_provider_responses_and_transport_retry_is_preserved(self):
        doc = document('Vertragliche Leistung.')
        raw = answer()
        class Provider:
            last_raw_response = None
            last_finish_reason = None
            calls = 0
            def extract(self, prompt):
                self.calls += 1
                if self.calls == 1:
                    self.last_raw_response, self.last_finish_reason = '{partial', 'MAX_TOKENS'
                    raise IncompleteResponse('unfinished_generation', 'MAX_TOKENS')
                self.last_raw_response, self.last_finish_reason = json.dumps(raw), 'STOP'
                return copy.deepcopy(raw)
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'raw.json'
            provider = Provider()
            result = collect_project_facts([doc], RetryingModel(RawRunRecorder(provider, path)))
            self.assertEqual(result['status'], 'recorded')
            self.assertEqual(json.loads(path.read_text()), ['{partial', raw])
            self.assertEqual(len(json.loads(path.with_name('raw.attempts.json').read_text())), 2)

    def test_cli_fresh_rights_run_reuse_and_explicit_offline_replay(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = root/'inputs'
            inputs.mkdir()
            fixture(inputs, count=1)
            args = ['units', '--pages', str(inputs/'pages.json'), '--documents', str(inputs/'documents.json'),
                    '--stop-after', 'project-facts', '--cache-dir', str(root/'cache')]
            for name, extra in [('first', ['--max-new-calls', '1', '--project', 'fixture']),
                                ('second', ['--project', 'fixture']),
                                ('replayed', ['--replay-project-facts', str(root/'first.json')])]:
                with patch.object(sys, 'argv', [*args, '--output', str(root/(name+'.json')), *extra]), \
                     patch('bauvo_working_units.cli.GeminiModel', return_value=SimpleNamespace(extract=lambda p: answer())) as model:
                    main()
                    self.assertEqual(model.call_count, 1 if name == 'first' else 0)
            first = json.loads((root/'first.json').read_text())
            self.assertEqual(first, json.loads((root/'second.json').read_text()))
            replayed = json.loads((root/'replayed.json').read_text())
            self.assertEqual(first['gemini_responses'], replayed['gemini_responses'])
            self.assertEqual(json.loads((root/'first.gemini-raw.json').read_text()), [answer()])
            self.assertEqual(validate_facts(first, load_documents(inputs/'pages.json', inputs/'documents.json')), first)

    def test_new_request_identity_cannot_reuse_old_schema_or_changed_amendment(self):
        prompts = []
        doc = document('Vertragliche Leistung.')
        collect_project_facts([doc], SimpleNamespace(extract=lambda p: (prompts.append(p), answer())[1]))
        first = provider_request(prompts[0], {'model': 'fixture'})
        changed = provider_request(prompts[0].replace(EVIDENCE_PROTOCOL, 'contractual-rights-v2'), {'model': 'fixture'})
        self.assertNotEqual(first, changed)
        self.assertEqual(set(first['response_schema']['properties']), {'ansprueche'})
        collect_project_facts([doc, document('Nachtrag.', 'amendment')],
            SimpleNamespace(extract=lambda p: (prompts.append(p), answer())[1]))
        self.assertNotEqual(first, provider_request(prompts[1], {'model': 'fixture'}))
