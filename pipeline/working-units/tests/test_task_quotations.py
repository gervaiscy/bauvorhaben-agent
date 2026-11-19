from consequence_fixtures import assessment_answer
"""Current task quotation contract across transport, source binding and handoffs."""
import copy
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bauvo_working_units.cache import CachedModel
from bauvo_working_units.call_budget import BudgetedModel, CallBudget
from bauvo_working_units.page_flow import _prompt, _view, extract_page_units
from bauvo_working_units.phases.aufgaben.quotations import PROTOCOL, bind
from bauvo_working_units.project_flow import Document
from bauvo_working_units.schemas import RESPONSE_SCHEMAS
from bauvo_working_units.trace import RawRunRecorder, TracingModel
from test_aufgaben_labelled_input import contract_facts, data, document


def task(quote, title='Aufgabe', actor=None):
    return {'title': title, 'actor': actor, 'quote': quote}


def answer(*tasks):
    return {'working_units': list(tasks)}


def extract(text, raw, **kwargs):
    return extract_page_units([document(text)], SimpleNamespace(extract=lambda _: copy.deepcopy(raw)),
                              stop_after='PAGE_EXTRACTION', **kwargs)


class TaskQuotationTests(unittest.TestCase):
    def test_provider_schema_has_only_working_units_with_title_actor_and_quote(self):
        schema = RESPONSE_SCHEMAS['PAGE_EXTRACTION']
        self.assertEqual(schema['required'], ['working_units'])
        self.assertEqual(set(schema['properties']), {'working_units'})
        self.assertFalse(schema['additionalProperties'])
        row = schema['properties']['working_units']['items']
        self.assertEqual(set(row['required']), {'title', 'actor', 'quote'})
        self.assertEqual(set(row['properties']), {'title', 'actor', 'quote'})
        self.assertFalse(row['additionalProperties'])
        self.assertIn({'type': 'null'}, row['properties']['actor']['anyOf'])

    def test_quote_is_a_literal_substring_not_an_expanded_paragraph_or_title(self):
        text = 'Vorwort. Der Bauherr beantragt Baustrom. Nur nach gesonderter Freigabe.'
        quote = 'Der Bauherr beantragt Baustrom.'
        raw = answer(task(quote, 'Baustrom beantragen', 'Bauherr'))
        before = copy.deepcopy(raw)
        result = extract(text, raw)
        unit = result['working_units'][0]
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['review_findings'], [])
        self.assertEqual(unit['gemini'], raw['working_units'][0])
        self.assertEqual(unit['derived']['source_text'], quote)
        self.assertEqual(unit['derived']['task_protocol'], PROTOCOL)
        self.assertEqual(_view(unit)['name'], 'Baustrom beantragen')
        self.assertEqual(_view(unit)['action'], quote)
        self.assertEqual(_view(unit)['actor'], 'Bauherr')
        self.assertIsNone(_view(unit)['performed_by'])
        citation = unit['derived']['citations'][0]
        self.assertEqual(text[citation['start']:citation['end']], quote)
        self.assertEqual(citation['source_key'], 'blb')
        self.assertEqual(citation['document_id'], 'D1')
        self.assertEqual(raw, before)
        self.assertEqual(result['gemini_responses'][0]['gemini'], raw)
        self.assertNotIn('reference_map', result['gemini_responses'][0])

    def test_separate_actions_can_keep_identical_quotes_and_different_actors(self):
        text = 'Der Bauherr beauftragt einen Installateur mit der Herstellung des Anschlusses.'
        raw = answer(task(text, 'Installateur beauftragen', 'Bauherr'),
                     task(text, 'Anschluss herstellen', 'Installateur'))
        result = extract(text, raw)
        first, second = result['working_units']
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(first['derived']['citations'], second['derived']['citations'])
        self.assertEqual([_view(u)['actor'] for u in result['working_units']], ['Bauherr', 'Installateur'])
        self.assertEqual([u['gemini'] for u in result['working_units']], raw['working_units'])

    def test_actor_can_remain_unknown_without_inference_from_title(self):
        result = extract('Der Prüfbericht ist zu übergeben.',
                         answer(task('Der Prüfbericht ist zu übergeben.', 'Bauherr übergibt Bericht')))
        self.assertIsNone(_view(result['working_units'][0])['actor'])
        self.assertEqual(result['review_findings'], [])

    def test_actor_role_from_window_context_survives_without_a_personal_name(self):
        # Authored prediction: this checks transport, not Gemini interpretation.
        text = ('Die antragstellende Person reicht den Antrag ein.\n\n'
                'Der Antrag kann jederzeit zurückgenommen werden.')
        quote = 'Der Antrag kann jederzeit zurückgenommen werden.'
        raw = answer(task(quote, 'Antrag zurücknehmen', 'antragstellende Person'))
        before = copy.deepcopy(raw)
        result = extract(text, raw)
        self.assertEqual(result['review_findings'], [])
        unit = result['working_units'][0]
        self.assertEqual(_view(unit)['actor'], 'antragstellende Person')
        self.assertEqual(_view(unit)['action'], quote)
        self.assertEqual(unit['derived']['citations'][0]['quote'], quote)
        self.assertEqual(unit['gemini'], before['working_units'][0])
        self.assertEqual(raw, before)

    def test_passive_actions_keep_their_selected_actor_including_unknown_performer(self):
        # Authored predictions verify transport, not Gemini's ability to find them.
        clauses = [
            ('Bei Lieferverzug des Lieferanten ist eine Vertragsstrafe zu zahlen.', 'Lieferant'),
            ('Der Lieferant darf Dritte einsetzen. Deren Namen sind dem Kunden auf Anfrage bekannt zu geben.', 'Lieferant'),
            ('Kunde und Lieferant können Änderungen vereinbaren. Die Mehrkosten sind gesondert festzulegen.', 'Kunde und Lieferant'),
            ('Ein Ablaufplan wird im Besprechungsprotokoll dokumentiert.', None)]
        raw = answer(*(task(quote, actor=actor) for quote, actor in clauses))
        result = extract('\n\n'.join(quote for quote, _ in clauses), raw)
        self.assertEqual(result['review_findings'], [])
        self.assertEqual([(_view(u)['action'], _view(u)['actor']) for u in result['working_units']], clauses)
        self.assertEqual([u['gemini'] for u in result['working_units']], raw['working_units'])

    def test_preserves_whitespace_tabs_and_unicode_exactly(self):
        quote = '2. Rate\t30 %\tnach Übergabe\r\nder Pläne – vollständig.'
        result = extract('Einleitung\r\n'+quote+'\r\n', answer(task(quote)))
        self.assertEqual(result['working_units'][0]['derived']['source_text'], quote)
        self.assertEqual(result['working_units'][0]['derived']['citations'][0]['quote'], quote)
        self.assertEqual(result['review_findings'], [])

    def test_altered_or_joined_quotations_remain_findings_without_repair_calls(self):
        original = 'Der Bauherr\nbeantragt Baustrom.\n\nAndere Regelung.\n\nEr stellt einen Verteiler bereit.'
        for quote in ('Der Bauherr beantragt Bauwasser.',
                      'beantragt Baustrom. Er stellt einen Verteiler bereit.'):
            with self.subTest(quote=quote):
                raw = answer(task(quote)); calls = []
                result = extract_page_units([document(original)], SimpleNamespace(extract=lambda p:
                    (calls.append(p), copy.deepcopy(raw))[1]), stop_after='PAGE_EXTRACTION')
                self.assertEqual(len(calls), 1)
                self.assertIn('task_quote_not_found', {f['code'] for f in result['review_findings']})
                self.assertNotIn('source_text', result['working_units'][0]['derived'])
                self.assertEqual(result['gemini_responses'][0]['gemini'], raw)

    def test_whitespace_and_linebreak_changes_restore_exact_original_source_and_audit(self):
        original = 'Der Bauherr\r\n\tstellt\u00a0den Lagerplatz\n bereit.'
        raw = answer(task('Der Bauherr stellt den Lagerplatz bereit.'))
        result = extract(original, raw)
        unit = result['working_units'][0]
        self.assertEqual(result['review_findings'], [])
        self.assertEqual(unit['gemini'], raw['working_units'][0])
        self.assertEqual(unit['derived']['source_text'], original)
        self.assertEqual(unit['derived']['citations'][0]['quote'], original)
        self.assertEqual(_view(unit)['action'], original)
        audit = result['gemini_responses'][0]['citation_evidence']
        self.assertEqual(audit['matching_protocol'], 'task-quote-matching-v2')
        self.assertEqual(audit['bindings'][0]['method'], 'unique_whitespace_quote')

    def test_one_or_two_omitted_words_restore_the_complete_source_with_omissions_audited(self):
        for omitted, original in [(['trockenen'], 'Der Bauherr stellt den trockenen Lagerplatz bereit.'),
                                   (['trockenen', 'befestigten'], 'Der Bauherr stellt den trockenen befestigten Lagerplatz bereit.'),
                                   (['nicht'], 'Der Bauherr darf nicht selbst bauen.')]:
            with self.subTest(omitted=omitted):
                quote = original
                for word in omitted:
                    quote = quote.replace(' '+word, '', 1)
                raw = answer(task(quote)); before = copy.deepcopy(raw)
                result = extract(original, raw)
                self.assertEqual(result['review_findings'], [])
                unit = result['working_units'][0]
                self.assertEqual(unit['derived']['source_text'], original)
                self.assertEqual(_view(unit)['action'], original)
                self.assertEqual(unit['derived']['citations'][0]['quote'], original)
                self.assertEqual(unit['gemini']['quote'], quote)
                binding = result['gemini_responses'][0]['citation_evidence']['bindings'][0]
                self.assertEqual(binding['method'], 'unique_omitted_words_quote')
                self.assertEqual(binding['omitted_words'], omitted)
                self.assertEqual(binding['omitted_word_count'], len(omitted))
                self.assertEqual(raw, before)

    def test_omissions_across_page_boundary_restore_canonical_per_page_spans(self):
        doc = replace(document('unused'), pages=({'pdf_page': 4, 'text': 'Der Bauherr stellt den'},
            {'pdf_page': 5, 'text': 'trockenen Lagerplatz rechtzeitig bereit.'}))
        quote = 'Der Bauherr stellt den Lagerplatz bereit.'
        result = extract_page_units([doc], SimpleNamespace(extract=lambda _: answer(task(quote))), stop_after='PAGE_EXTRACTION')
        unit = result['working_units'][0]
        self.assertEqual(result['review_findings'], [])
        self.assertEqual(unit['derived']['source_text'], 'Der Bauherr stellt den\ntrockenen Lagerplatz rechtzeitig bereit.')
        self.assertEqual([c['pdf_page'] for c in unit['derived']['citations']], [4, 5])
        self.assertEqual(unit['gemini']['quote'], quote)

    def test_changed_added_or_more_than_two_missing_words_numbers_and_punctuation_stay_unresolved(self):
        for source, quote in [
            ('Der Bauherr stellt den trockenen befestigten sicheren Lagerplatz bereit.', 'Der Bauherr stellt den Lagerplatz bereit.'),
            ('Der Bauherr stellt den Lagerplatz bereit.', 'Der Bauherr stellt den trockenen Lagerplatz bereit.'),
            ('Der Bauherr stellt den Lagerplatz bereit.', 'Der Lieferant stellt den Lagerplatz bereit.'),
            ('Die Rate von 500 EUR wird bezahlt.', 'Die Rate wird bezahlt.'),
            ('Die Rate von 500 EUR wird bezahlt.', 'Die Rate von 600 EUR wird bezahlt.'),
            ('Der Bauherr liefert; er bezahlt.', 'Der Bauherr liefert er bezahlt.')]:
            with self.subTest(source=source, quote=quote):
                result = extract(source, answer(task(quote)))
                self.assertIn('task_quote_not_found', {f['code'] for f in result['review_findings']})
                self.assertNotIn('source_text', result['working_units'][0]['derived'])

    def test_repeated_formatting_or_omission_matches_remain_ambiguous(self):
        for source in ('Der Bauherr\n liefert. Der Bauherr\t liefert.',
                       'Der Bauherr liefert heute. Der Bauherr liefert morgen.'):
            with self.subTest(source=source):
                result = extract(source, answer(task('Der Bauherr liefert.')))
                self.assertIn('task_quote_ambiguous', {f['code'] for f in result['review_findings']})
                self.assertNotIn('source_text', result['working_units'][0]['derived'])

    def test_exact_word_match_takes_precedence_over_missing_word_candidates(self):
        quote = 'Der Bauherr liefert.'
        result = extract('Der Bauherr liefert heute. '+quote, answer(task(quote)))
        self.assertEqual(result['review_findings'], [])
        self.assertEqual(result['working_units'][0]['derived']['source_text'], quote)

    def test_repeated_quote_in_one_or_several_pages_is_ambiguous_not_guessed(self):
        for pages in ([{'pdf_page': 1, 'text': 'Liefern. Liefern.'}],
                      [{'pdf_page': n, 'text': 'Liefern.'} for n in (1, 2)]):
            doc = replace(document('unused'), pages=tuple(pages))
            raw = answer(task('Liefern.'))
            result = extract_page_units([doc], SimpleNamespace(extract=lambda _: raw), stop_after='PAGE_EXTRACTION')
            self.assertIn('task_quote_ambiguous', {f['code'] for f in result['review_findings']})
            self.assertEqual(result['working_units'][0]['gemini'], raw['working_units'][0])
            self.assertNotIn('source_text', result['working_units'][0]['derived'])

    def test_overlapping_literal_occurrences_are_also_ambiguous(self):
        self.assertEqual(bind('aaa', [{'text': 'aaaa'}]), ([], 'task_quote_ambiguous'))

    def test_cross_page_quotation_has_exact_per_page_spans(self):
        doc = replace(document('unused'), pages=({'pdf_page': 4, 'text': 'Der Bauherr beantragt'},
                                                  {'pdf_page': 5, 'text': 'den Anschluss. Danach zahlt er.'}))
        quote = 'Der Bauherr beantragt\nden Anschluss.'
        raw = answer(task(quote, actor='Bauherr'))
        result = extract_page_units([doc], SimpleNamespace(extract=lambda _: raw), stop_after='PAGE_EXTRACTION')
        unit = result['working_units'][0]
        self.assertEqual(unit['derived']['source_text'], quote)
        self.assertEqual([c['pdf_page'] for c in unit['derived']['citations']], [4, 5])
        self.assertEqual([c['quote'] for c in unit['derived']['citations']],
                         ['Der Bauherr beantragt', 'den Anschluss.'])
        self.assertEqual(result['review_findings'], [])

    def test_contract_fact_quote_cannot_bind_to_a_blb_task(self):
        _, facts = contract_facts()
        raw = answer(task('Die Anschlussleistung ist im Festpreis enthalten.'))
        result = extract('Baustrom beantragen.', raw, project_facts=facts)
        self.assertIn('task_quote_not_found', {f['code'] for f in result['review_findings']})
        self.assertNotIn('source_text', result['working_units'][0]['derived'])

    def test_identical_contract_and_blb_text_binds_only_to_the_current_document(self):
        _, facts = contract_facts()
        quote = 'Die Anschlussleistung ist im Festpreis enthalten.'
        result = extract(quote, answer(task(quote)), project_facts=facts)
        self.assertEqual(result['review_findings'], [])
        self.assertEqual(result['working_units'][0]['derived']['citations'][0]['source_key'], 'blb')

    def test_no_tasks_is_a_complete_response(self):
        result = extract('Allgemeine Werbung.', answer())
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['working_units'], [])

    def test_malformed_rows_are_preserved_and_not_promoted_to_valid_task_text(self):
        rows = [None, {}, {'title': 'X', 'quote': 'Liefern.'}, task(''), task('Liefern.', actor=3),
                task('Liefern.', title=' '), {**task('Liefern.'), 'conditions': []}]
        for row in rows:
            with self.subTest(row=row):
                raw = answer(row); result = extract('Liefern.', raw)
                self.assertTrue(result['review_findings'])
                self.assertEqual(result['working_units'][0]['gemini'], row)
                self.assertNotIn('source_text', result['working_units'][0]['derived'])

    def test_plain_window_input_keeps_fact_quotes_but_no_source_label_catalog(self):
        contract, facts = contract_facts(); calls = []
        result = extract_page_units([document('Anschluss herstellen.')], SimpleNamespace(extract=lambda p:
            (calls.append(p), answer(task('Anschluss herstellen.')))[1]),
            project_facts=facts, stop_after='PAGE_EXTRACTION')
        wire = data(calls[0])
        self.assertEqual(wire['source_pages'][0]['text'], 'Anschluss herstellen.')
        self.assertNotIn('segments', wire['source_pages'][0])
        self.assertNotIn('evidence_catalog', wire)
        self.assertNotIn('source_documents', wire['project_facts'])
        self.assertNotIn('reference_protocol', wire)
        self.assertNotIn('prior_context', wire)
        citation = wire['project_facts']['facts'][0]['citations'][0]
        self.assertEqual(citation['quote'], contract.pages[0]['text'].splitlines(keepends=True)[0])
        self.assertNotIn('evidence_id', citation)
        self.assertEqual(result['review_findings'], [])

    def test_windows_have_at_most_ten_pages_and_never_cross_document_boundaries(self):
        docs = [Document(f'D{n}', f'Document {n}', 'source.pdf', schema_version=6, source_key='s',
                         pages=tuple({'pdf_page': p, 'text': f'Document {n} page {p}.'}
                                     for p in range(1+n*20, 14+n*20))) for n in (0, 1)]
        calls = []
        def provider(prompt):
            wire = data(prompt); calls.append(wire)
            return answer(task(wire['source_pages'][-1]['text']))
        result = extract_page_units(docs, SimpleNamespace(extract=provider), stop_after='PAGE_EXTRACTION')
        self.assertEqual(len(calls), 4)
        self.assertEqual([len(w['source_pages']) for w in calls], [10, 4, 10, 4])
        for wire in calls:
            n = int(wire['document']['id'][1:])
            self.assertTrue(all(p['text'].startswith(f'Document {n} ') for p in wire['source_pages']))
        self.assertEqual(result['review_findings'], [])

    def test_english_titles_do_not_request_translation_of_original_quotes(self):
        prompt = _prompt('PAGE_EXTRACTION', {'source_pages': [{'pdf_page': 1, 'text': 'Originaltext.'}]}, 'en')
        self.assertEqual(data(prompt)['source_pages'][0]['text'], 'Originaltext.')
        self.assertIn('quote bleibt unverändert', prompt)

    def test_completed_invalid_quote_response_replays_without_another_provider_call(self):
        raw = answer(task('Altered quotation.'))
        with TemporaryDirectory() as tmp:
            root = Path(tmp); calls = []
            def provider(prompt):
                calls.append(prompt); return copy.deepcopy(raw)
            model = TracingModel(RawRunRecorder(CachedModel(SimpleNamespace(extract=provider), root/'cache', {}),
                                                root/'raw.json'), root/'trace')
            first = extract_page_units([document('Original quotation.')], model, stop_after='PAGE_EXTRACTION')
            self.assertEqual(json.loads((root/'raw.json').read_text()), [raw])
            self.assertEqual(json.loads((root/'trace/0001-response.json').read_text()), raw)
            self.assertEqual((root/'trace/0001-prompt.txt').read_text(), calls[0])
            saved = {p: p.read_bytes() for p in (root/'cache').glob('*.json')}
            offline = BudgetedModel(lambda: self.fail('Unexpected provider call'), CallBudget(0))
            replay = extract_page_units([document('Original quotation.')],
                CachedModel(offline, root/'cache', {}), stop_after='PAGE_EXTRACTION')
            self.assertEqual(first, replay)
            self.assertEqual(len(calls), 1)
            self.assertEqual(saved, {p: p.read_bytes() for p in saved})

    def test_previously_rejected_whitespace_quote_replays_from_cache_without_a_paid_call(self):
        raw = answer(task('Der Bauherr liefert den Bericht.'))
        doc = document('Der Bauherr\n liefert den Bericht.')
        with TemporaryDirectory() as tmp:
            calls = []
            def provider(prompt):
                calls.append(prompt)
                return copy.deepcopy(raw)
            cache = Path(tmp)/'cache'
            with patch('bauvo_working_units.phases.aufgaben.quotations.locate',
                       return_value=(None, 'task_quote_not_found')):
                first = extract_page_units([doc], CachedModel(SimpleNamespace(extract=provider), cache, {}),
                                           stop_after='PAGE_EXTRACTION')
            self.assertNotIn('source_text', first['working_units'][0]['derived'])
            offline = BudgetedModel(lambda: self.fail('Unexpected provider call'), CallBudget(0))
            replay = extract_page_units([doc], CachedModel(offline, cache, {}), stop_after='PAGE_EXTRACTION')
            self.assertEqual(len(calls), 1)
            self.assertEqual(replay['review_findings'], [])
            self.assertEqual(replay['working_units'][0]['derived']['source_text'], doc.pages[0]['text'])
            self.assertEqual(replay['gemini_responses'][0]['gemini'], first['gemini_responses'][0]['gemini'])

    def test_saved_cumulative_result_rebinds_locally_preserving_raw_and_prior_findings(self):
        from bauvo_working_units.citation_repair import repair_saved_result
        original = 'Der Bauherr\n stellt den trockenen Lagerplatz bereit.'
        raw = answer(task('Der Bauherr stellt den Lagerplatz bereit.'),
                     task('Der Bauherr stellt den Lagerplatz rechtzeitig bereit.'))
        doc = document(original)
        with patch('bauvo_working_units.phases.aufgaben.quotations.locate',
                   return_value=(None, 'task_quote_not_found')):
            baseline = extract_page_units([doc], SimpleNamespace(extract=lambda p:
                raw if p.startswith('PAGE_EXTRACTION.') else {'offers': []}), stop_after='PAGE_OFFERS')
        before = copy.deepcopy(baseline)
        result = repair_saved_result(baseline, [doc], model=SimpleNamespace(extract=lambda _: self.fail('No paid repair')))
        self.assertEqual(baseline, before)
        self.assertEqual(result['working_units'][0]['derived']['source_text'], original)
        self.assertNotIn('source_text', result['working_units'][1]['derived'])
        self.assertEqual([u['gemini'] for u in result['working_units']], raw['working_units'])
        self.assertEqual([r['gemini'] for r in result['gemini_responses']],
                         [r['gemini'] for r in before['gemini_responses']])
        self.assertEqual(result['citation_repair']['original_review_findings'], before['review_findings'])
        self.assertEqual(result['gemini_responses'][0]['citation_evidence_history'][-1],
                         before['gemini_responses'][0]['citation_evidence'])
        self.assertEqual(sum(f['code'] == 'task_quote_not_found' for f in result['review_findings']), 1)

    def test_saved_rebinding_does_not_search_outside_original_window(self):
        from bauvo_working_units.citation_repair import repair_saved_result
        quote = 'Der Bauherr liefert den Bericht.'
        doc = replace(document('unused'), pages=({'pdf_page': 1, 'text': 'Ein anderer Text.'},
                                                 {'pdf_page': 2, 'text': quote}))
        def provider(prompt):
            return answer(task(quote)) if data(prompt)['source_pages'][0]['pdf_page'] == 1 else answer()
        baseline = extract_page_units([doc], SimpleNamespace(extract=provider),
            chunk_pages=1, overlap_pages=0, stop_after='PAGE_EXTRACTION')
        result = repair_saved_result(baseline, [doc])
        self.assertIn('task_quote_not_found', {f['code'] for f in result['review_findings']})
        self.assertNotIn('source_text', result['working_units'][0]['derived'])

    def test_every_later_stage_receives_quoted_action_actor_and_source_location(self):
        text = 'Der Bauherr beantragt Baustrom. Er stellt einen Verteiler bereit.'
        raw = answer(task('Der Bauherr beantragt Baustrom.', 'Baustrom beantragen', 'Bauherr'),
                     task('Er stellt einen Verteiler bereit.', 'Verteiler bereitstellen', 'Bauherr'))
        seen = []
        def provider(prompt):
            phase = prompt.split('.', 1)[0]
            if phase == 'PAGE_EXTRACTION': return copy.deepcopy(raw)
            if phase == 'PAGE_OFFERS': return {'offers': []}
            wire = data(prompt); seen.append(phase)
            if phase == 'PAGE_CONSEQUENCES':
                for unit, original in zip(wire['working_units'].values(), raw['working_units']):
                    self.assertEqual(unit['quote'], original['quote'])
                    self.assertEqual(unit['actor'], original['actor'])
                    self.assertIn('timing', unit)
                return assessment_answer(wire)
            for unit, original in zip(wire['working_units'], raw['working_units']):
                self.assertEqual(unit['action'], original['quote'])
                self.assertEqual(unit['name'], original['title'])
                self.assertEqual(unit['actor'], original['actor'])
                self.assertEqual(unit['citations'][0]['quote'], original['quote'])
                self.assertEqual(unit['citations'][0]['document_id'], 'D1')
            rows = []
            for unit in wire['working_units']:
                row = {'unit_id': unit['id']}
                if phase == 'PAGE_TIMING': row.update(status='NOT_STATED', timing_rules=[], missing_evidence=[])
                elif phase == 'PAGE_CONSEQUENCES': row.update(status='NOT_STATED', consequences=[])
                else: row.update(applicability='UNKNOWN', reason='Nicht bestimmt.', applicability_rules=[], missing_evidence=[])
                rows.append(row)
            return {'assessments': rows}
        result = extract_page_units([document(text)], SimpleNamespace(extract=provider))
        self.assertEqual(seen, ['PAGE_TIMING', 'PAGE_CONSEQUENCES', 'PAGE_APPLICABILITY'])
        self.assertEqual(result['review_findings'], [])
        self.assertEqual([u['gemini'] for u in result['working_units']], raw['working_units'])

    def test_saved_quote_tasks_can_continue_without_rerunning_extraction(self):
        from bauvo_working_units.offer_stage import extract_offers
        baseline = extract('Anschluss herstellen.', answer(task('Anschluss herstellen.')))
        before = copy.deepcopy(baseline); calls = []
        def provider(prompt):
            calls.append(prompt); self.assertTrue(prompt.startswith('PAGE_OFFERS.')); return {'offers': []}
        result = extract_offers(baseline, [document('Anschluss herstellen.')], SimpleNamespace(extract=provider))
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['working_units'], baseline['working_units'])
        self.assertEqual(baseline, before)

    def test_focused_evaluation_uses_the_same_quote_binding_and_original_action(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'evaluation'))
        from focused_evaluation import evidence_payload, record_response
        from bauvo_working_units.project_flow import make_page_chunks
        doc = document('Einleitung. Anschluss herstellen.'); records = []
        raw = answer(task('Anschluss herstellen.', 'Anschluss'))
        resolved, issues = record_response(records, raw, 'PAGE_EXTRACTION', doc,
                                           evidence_payload(doc, make_page_chunks(doc)[0]))
        self.assertEqual(issues, [])
        self.assertEqual(resolved['working_units'][0]['action'], 'Anschluss herstellen.')
        self.assertEqual(records[0]['gemini'], raw)
        self.assertEqual(records[0]['quote_protocol'], PROTOCOL)
