"""Production timing wire format and offline evidence binding, without paid calls."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from bauvo_working_units.cache import CachedModel, provider_request
from bauvo_working_units.call_budget import BudgetedModel, CallBudget
from bauvo_working_units.citation_evidence import catalog, canonical
from bauvo_working_units.page_flow import _prompt, extract_page_units
from bauvo_working_units.phases.timing.response import resolve_response
from bauvo_working_units.project_facts import facts_view, evidence_pages
from bauvo_working_units.timing_stage import extract_timing
from bauvo_working_units.schemas import RESPONSE_SCHEMAS
from test_aufgaben_labelled_input import contract_facts, document, data


class CompactTimingTests(unittest.TestCase):
    def setUp(self):
        self.contract, self.facts = contract_facts()
        self.local = document('Der Bauherr stellt den Anschluss\nzwei Wochen vor Baubeginn bereit.')
        self.pages = [{**p, 'source_key': self.local.source_key, 'document_id': self.local.id}
                      for p in self.local.pages]

    def test_real_prompt_has_plain_unchanged_pages_and_only_id_text_per_fact(self):
        page = {**self.pages[0], 'primary': True, 'checkboxes': [{'checked': True}]}
        payload = {'source_key': 'blb', 'document': {'id': 'D1'}, 'source_pages': [page],
                   'evidence_catalog': catalog(self.pages), 'working_units': [],
                   'project_facts': facts_view(self.facts)}
        before = copy.deepcopy(payload)
        prompt = _prompt('PAGE_TIMING', payload, 'de'); wire = data(prompt)
        self.assertEqual(wire['source_pages'], [page])
        self.assertEqual(wire['project_facts'], {'facts': [
            {'id': f['id'], 'text': f['derived']['text']} for f in self.facts['facts']]})
        self.assertEqual(set(wire['project_facts']['facts'][0]), {'id', 'text'})
        self.assertNotIn('evidence_catalog', prompt)
        self.assertNotIn('source_documents', prompt)
        self.assertNotIn('Eine nicht ausgewählte Vertragsklausel', prompt)
        self.assertEqual(prompt.count(self.local.pages[0]['text'].replace('\n', '\\n')), 1)
        self.assertEqual(payload, before)
        encoded = prompt.split('\n\nSOURCE DATA:\n', 1)[0].rsplit('\n\n', 1)[1]
        self.assertEqual(json.loads(encoded), RESPONSE_SCHEMAS['PAGE_TIMING'])

    def test_page_quotes_restore_original_whitespace_and_fact_ids_restore_contract_provenance(self):
        fact = self.facts['facts'][0]
        raw = {'assessments': [{'unit_id': 'U1', 'answer': 'Zwei Wochen vor Baubeginn.',
            'citations': [{'pdf_page': 1, 'quote': self.local.pages[0]['text'].replace('\n', ' ')},
                          {'fact_id': fact['id']}]}]}
        before = copy.deepcopy(raw)
        resolved, audit, issues = resolve_response(raw, self.pages, facts_view(self.facts), evidence_pages(self.facts))
        self.assertEqual(issues, [])
        refs = resolved['assessments'][0]['citations']
        self.assertEqual(refs[0]['quote'], self.local.pages[0]['text'])
        self.assertEqual(refs[0]['source_key'], 'blb')
        self.assertEqual(refs[1]['quote'], fact['derived']['verified_citations'][0]['quote'])
        self.assertEqual(refs[1]['source_key'], 'contract')
        self.assertEqual([b['method'] for b in audit['bindings']], ['source_page_quote', 'project_fact_reference'])
        self.assertEqual(raw, before)

    def test_bad_selections_remain_findings_without_repair_or_arbitrary_rebinding(self):
        for citation, code in [({'fact_id': 'UNKNOWN'}, 'unknown_fact_id'),
                               ({'fact_id': []}, 'unknown_fact_id'),
                               ({'evidence_id': 'private-old-id'}, 'invalid_citation'),
                               ({'pdf_page': 99, 'quote': 'Text'}, 'citation_page_outside_window'),
                               ({'pdf_page': 1, 'quote': 'Invented words'}, 'citation_quote_not_found')]:
            with self.subTest(citation=citation):
                raw = {'assessments': [{'unit_id': 'U', 'answer': 'Zeitvorgabe', 'citations': [citation]}]}
                resolved, _, issues = resolve_response(raw, self.pages, facts_view(self.facts), evidence_pages(self.facts))
                self.assertEqual(issues[0]['code'], code)
                self.assertEqual(resolved, raw)

    def test_fact_reference_requires_original_verified_evidence_not_a_rewritten_claim(self):
        view = facts_view(self.facts); identifier = view['facts'][0]['id']
        raw = {'assessments': [{'unit_id': 'U', 'answer': 'Zeitvorgabe', 'citations': [{'fact_id': identifier}]}]}
        for change in ('empty', 'quote', 'offset'):
            with self.subTest(change=change):
                altered = copy.deepcopy(view)
                if change == 'empty': altered['facts'][0]['citations'] = []
                elif change == 'quote': altered['facts'][0]['citations'][0]['quote'] = 'Invented'
                else: altered['facts'][0]['citations'][0]['start'] += 1
                _, _, issues = resolve_response(raw, self.pages, altered, evidence_pages(self.facts))
                self.assertEqual(issues[0]['code'], 'invalid_fact_evidence')

    def test_nonadjacent_fact_passages_stay_visibly_separate_and_share_original_identity(self):
        page = {'source_key': 'contract', 'document_id': 'D1', 'pdf_page': 1,
                'text': 'Erste Regel.\nNicht ausgewählt.\nEine Ausnahme.'}
        refs = [canonical(e) for e in catalog([page])]
        from bauvo_working_units.phases.project_facts.index import source_text
        text = source_text([refs[0], refs[-1]], [page])
        view = {'facts': [{'id': 'F1', 'text': text, 'citations': [refs[0], refs[-1]]}]}
        wire = data(_prompt('PAGE_TIMING', {'source_pages': [], 'project_facts': view}, 'de'))
        self.assertIn('[separate source passage]', wire['project_facts']['facts'][0]['text'])
        raw = {'assessments': [{'unit_id': 'U', 'answer': 'Zeitregel', 'citations': [{'fact_id': 'F1'}]}]}
        resolved, _, issues = resolve_response(raw, [], view, [page])
        self.assertEqual(issues, [])
        self.assertEqual(resolved['assessments'][0]['citations'], [refs[0], refs[-1]])

    def test_current_stage_preserves_earlier_artifacts_and_cache_replays_offline(self):
        quote = self.local.pages[0]['text']
        def earlier(prompt):
            return {'working_units': [{'title': 'Anschluss bereitstellen', 'actor': 'Bauherr', 'quote': quote}]} if prompt.startswith('PAGE_EXTRACTION.') else {'offers': []}
        baseline = extract_page_units([self.local], SimpleNamespace(extract=earlier),
                                     project_facts=self.facts, stop_after='PAGE_OFFERS')
        before = copy.deepcopy(baseline); calls = []
        def timing(prompt):
            wire = data(prompt); calls.append(wire)
            self.assertNotIn('evidence_catalog', wire)
            return {'assessments': [{'unit_id': u['id'], 'answer': 'Zwei Wochen vor Baubeginn.',
                'citations': [{'pdf_page': 1, 'quote': quote}, {'fact_id': wire['project_facts']['facts'][0]['id']}]} for u in wire['working_units']]}
        with TemporaryDirectory() as tmp:
            first = extract_timing(baseline, [self.local], CachedModel(SimpleNamespace(extract=timing), Path(tmp), {}))
            replay = extract_timing(baseline, [self.local], CachedModel(
                BudgetedModel(lambda: self.fail('No provider allowed'), CallBudget(0)), Path(tmp), {}))
        self.assertEqual(first, replay)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first['review_findings'], [])
        self.assertEqual(baseline, before)
        self.assertEqual(first['offers'], before['offers'])
        self.assertEqual(first['project_facts'], before['project_facts'])
        self.assertEqual(first['gemini_responses'][:len(before['gemini_responses'])], before['gemini_responses'])
        review = first['working_units'][0]['reviews'][0]
        self.assertEqual(review['gemini']['citations'][1], {'fact_id': self.facts['facts'][0]['id']})
        self.assertEqual(review['derived']['resolved_evidence']['citations'][1]['source_key'], 'contract')

    def test_request_identity_changes_when_sources_facts_or_response_contract_change(self):
        prompt = _prompt('PAGE_TIMING', {'source_pages': self.pages, 'project_facts': facts_view(self.facts)}, 'de')
        original = provider_request(prompt, {})
        changed = provider_request(prompt.replace('zwei Wochen', 'drei Wochen'), {})
        self.assertNotEqual(original, changed)
        self.assertNotIn('evidence_id', json.dumps(original['response_schema']))

    def test_invalid_fact_selection_stays_visible_without_an_extra_repair_call(self):
        quote = self.local.pages[0]['text']
        def earlier(prompt):
            return {'working_units': [{'title': 'Anschluss', 'actor': None, 'quote': quote}]} if prompt.startswith('PAGE_EXTRACTION.') else {'offers': []}
        baseline = extract_page_units([self.local], SimpleNamespace(extract=earlier),
            project_facts=self.facts, stop_after='PAGE_OFFERS')
        calls = []
        def timing(prompt):
            calls.append(prompt)
            self.assertTrue(prompt.startswith('PAGE_TIMING.'))
            return {'assessments': [{'unit_id': data(prompt)['working_units'][0]['id'],
                                    'answer': 'Eine Frist', 'citations': [{'fact_id': 'UNKNOWN'}]}]}
        result = extract_timing(baseline, [self.local], SimpleNamespace(extract=timing))
        self.assertEqual(len(calls), 1)
        self.assertIn('unknown_fact_id', {f['code'] for f in result['review_findings']})
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['working_units'][0]['reviews'][0]['gemini']['citations'], [{'fact_id': 'UNKNOWN'}])

    def test_page_quote_cannot_borrow_a_contract_passage_or_choose_repeated_words(self):
        raw = {'assessments': [{'unit_id': 'U', 'answer': 'Zeitvorgabe', 'citations': [
            {'pdf_page': 1, 'quote': self.contract.pages[0]['text'].splitlines()[0]}]}]}
        _, _, issues = resolve_response(raw, self.pages, facts_view(self.facts), evidence_pages(self.facts))
        self.assertEqual(issues[0]['code'], 'citation_quote_not_found')
        repeated = [{**self.pages[0], 'text': 'Der Bauherr liefert.\nDer Bauherr liefert.'}]
        raw['assessments'][0]['citations'] = [{'pdf_page': 1, 'quote': 'Der Bauherr liefert.'}]
        _, _, issues = resolve_response(raw, repeated, {})
        self.assertEqual(issues[0]['reason'], 'ambiguous')
