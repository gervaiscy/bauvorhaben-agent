"""Fictional completeness controls and lossless transport, without provider calls.

The synthetic answers establish what must survive selection and restoration. They
do not establish that Gemini will select these passages after a prompt change.
"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from bauvo_working_units.phases.aufgaben.passages import passage_catalog
from bauvo_working_units.page_flow import extract_page_units, _view
from bauvo_working_units.project_flow import Document


CASES = {c['id']: c for c in json.loads((Path(__file__).resolve().parents[1]
         / 'evaluation/cases.json').read_text())['cases'] if c['id'].startswith('task-evidence-')}


def run_case(case):
    documents = [Document(d['document_id'], d['document_title'], 'fictional.pdf', schema_version=6,
                         source_key='fictional', pages=tuple(copy.deepcopy(p) for p in case['pages']
                                                           if p['pdf_page'] in d['pdf_pages']))
                 for d in case['documents']]
    answers = {}
    for document in documents:
        entries = passage_catalog([{**p, 'source_key': document.source_key, 'document_id': document.id}
                                   for p in document.pages])
        answer = {'working_units': [], 'carry_forward': ''}
        for expected in case['expected_units']:
            if expected['document_id'] != document.id:
                continue
            labels = [f'S{i}' for i, entry in enumerate(entries, 1) if any(
                entry['pdf_page'] == evidence['pdf_page'] and (entry['quote'] in evidence['quote']
                                                            or evidence['quote'] in entry['quote'])
                for evidence in expected['evidence'])]
            answer['working_units'].append({'title': expected['action'], 'actor': expected['actor'],
                                            'evidence_ids': labels})
        answers[document.id] = answer
    calls = []
    def provider(prompt):
        data = json.loads(prompt.split('SOURCE DATA:\n', 1)[1])
        calls.append(data)
        return copy.deepcopy(answers[data['document']['id']])
    result = extract_page_units(documents, SimpleNamespace(extract=provider), stop_after='PAGE_EXTRACTION')
    return list(answers.values()), result, calls


class TaskEvidenceCompletenessTests(unittest.TestCase):
    def check_case(self, identifier):
        case = CASES[identifier]
        before = copy.deepcopy(case)
        answers, result, calls = run_case(case)
        self.assertEqual(len(calls), len(case['documents']))
        for call in calls:
            self.assertFalse(set(call) & {'expected_units', 'expectations', 'forbidden_inferences'})
        self.assertEqual([{'pdf_page': page['pdf_page'], 'text': page['text']}
                          for call in calls for page in call['source_pages']], case['pages'])
        self.assertEqual([r['gemini'] for r in result['gemini_responses']], answers)
        self.assertEqual(len(result['working_units']), len(case['expected_units']))
        self.assertEqual([u['gemini'] for u in result['working_units']],
                         [u for answer in answers for u in answer['working_units']])
        self.assertEqual(result['review_findings'], [])
        self.assertEqual(case, before)
        for expected, actual in zip(case['expected_units'], result['working_units']):
            restored = actual['derived']['source_text']
            for evidence in expected['evidence']:
                self.assertIn(evidence['quote'], restored)
            self.assertEqual(_view(actual)['action'], restored)
            self.assertEqual(_view(actual)['actor'], expected['actor'])
        return result

    def test_bonus_reference_and_separate_amendment_keep_document_local_bindings(self):
        result = self.check_case('task-evidence-credit-reference-context-only-amendment')
        payment, proof = result['working_units']
        self.assertIn('Zusatzvereinbarung', _view(payment)['action'])
        self.assertNotIn('Die Kundin übergibt', _view(payment)['action'])
        self.assertNotIn('Sprechzeiten', _view(payment)['action'])
        self.assertEqual({c['document_id'] for c in payment['derived']['citations']}, {'D1'})
        self.assertEqual({c['document_id'] for c in proof['derived']['citations']}, {'D2'})

    def test_equivalent_company_consents_keep_their_own_permissions_withdrawals_and_roles(self):
        result = self.check_case('task-evidence-equivalent-consents-separate-documents')
        for n, company in enumerate(('Ingenieurbüro Tal', 'Energieberatung Hafen', 'Projektservice Wald')):
            processing, withdrawal = result['working_units'][n*2:n*2+2]
            self.assertEqual(_view(processing)['actor'], company)
            self.assertEqual(_view(withdrawal)['actor'], 'Bauherrin')
            for task in (processing, withdrawal):
                self.assertNotIn('Informationen erhalten', _view(task)['action'])
                self.assertEqual({c['document_id'] for c in task['derived']['citations']}, {f'D{n+1}'})

    def test_penalty_retains_later_delivery_deadline_and_extension_cap(self):
        result = self.check_case('task-evidence-penalty-with-later-deadline-definition')
        penalty, delivery = result['working_units']
        self.assertIn('höchstens um sieben Tage', _view(penalty)['action'])
        self.assertIn('nach Freigabe der Fertigungszeichnung', _view(penalty)['action'])
        self.assertNotIn('Katalog', _view(penalty)['action'])
        self.assertNotIn('25 EUR', _view(delivery)['action'])

    def test_standard_model_and_exclusion_qualify_the_positive_scope(self):
        result = self.check_case('task-evidence-series-specification-as-scope')
        self.assertIn('Serienmodell', _view(result['working_units'][0])['action'])
        self.assertIn('Elektroanschluss', _view(result['working_units'][0])['action'])
        self.assertNotIn('Erfahrung', _view(result['working_units'][0])['action'])

    def test_mixed_paragraph_keeps_joint_agreement_and_both_individual_actors(self):
        result = self.check_case('task-evidence-independent-actions-different-parties-one-paragraph')
        tasks = result['working_units']
        self.assertEqual([_view(t)['actor'] for t in tasks],
                         ['Besteller und Lieferant', 'Besteller', 'Lieferant'])
        self.assertEqual(len({t['id'] for t in tasks}), 3)
        self.assertTrue(all(t['derived']['citations'] == tasks[0]['derived']['citations'] for t in tasks))

    def test_linked_credit_restores_amount_reference_and_amendment_condition(self):
        result = self.check_case('task-evidence-linked-credit-and-amendment')
        task = result['working_units'][0]
        self.assertEqual(len(task['gemini']['evidence_ids']), 3)
        self.assertIn('laut Zusatzvereinbarung', task['derived']['source_text'])
        self.assertIn('dreijährigen Servicevertrags', task['derived']['source_text'])
        self.assertNotIn('Telefonzentrale', task['derived']['source_text'])

    def test_receipt_alone_can_be_recorded_without_fabricating_work(self):
        result = self.check_case('task-evidence-receipt-without-assignment')
        self.assertEqual(result['working_units'], [])
        self.assertEqual(result['gemini_responses'][0]['gemini']['working_units'], [])

    def test_null_actor_keeps_passive_work_without_issuer_or_recipient_inference(self):
        result = self.check_case('task-evidence-delivery-unknown-performer')
        task = result['working_units'][0]
        self.assertIsNone(_view(task)['actor'])
        self.assertIn('dem Bauherrn', _view(task)['action'])
        self.assertNotIn('Bauplanung West', _view(task)['action'])

    def test_explicit_actor_survives_a_different_issuer_and_receipt_acknowledgement(self):
        result = self.check_case('task-evidence-delivery-explicit-performer')
        task = result['working_units'][0]
        self.assertEqual(_view(task)['actor'], 'Kontrollstelle Ost')
        self.assertNotIn('Bauplanung West', _view(task)['action'])
        self.assertNotIn('erhalten zu haben', _view(task)['action'])

    def test_nominal_work_and_each_room_row_keep_details_and_actor_evidence(self):
        result = self.check_case('task-evidence-nominal-and-table-work')
        penalty, agreement, kitchen, bath = result['working_units']
        self.assertIn('verschuldeter Verspätung', _view(penalty)['action'])
        self.assertIn('vor Ausführung', _view(agreement)['action'])
        self.assertEqual(_view(agreement)['actor'], 'beiden Vertragsparteien')
        for task, included, excluded in ((kitchen, 'Mischbatterie', 'Dusche'),
                                          (bath, 'Dusche', 'Mischbatterie')):
            self.assertEqual(_view(task)['actor'], 'Auftragnehmer')
            self.assertIn('vom Auftragnehmer ausgeführt', _view(task)['action'])
            self.assertIn(included, _view(task)['action'])
            self.assertNotIn(excluded, _view(task)['action'])

    def test_scope_exclusion_remains_context_for_positive_work(self):
        result = self.check_case('task-evidence-exclusion-without-assignment')
        task = result['working_units'][0]
        self.assertIn('nicht zum Leistungsumfang', _view(task)['action'])
        self.assertEqual(_view(task)['actor'], 'Auftragnehmer')

    def test_separate_assignment_keeps_commissioning_and_performance_with_shared_evidence(self):
        result = self.check_case('task-evidence-exclusion-with-separate-assignment')
        building, commissioning, performance = result['working_units']
        self.assertIn('nicht zum Leistungsumfang', _view(building)['action'])
        self.assertEqual(_view(commissioning)['actor'], 'Bauherr')
        self.assertEqual(_view(performance)['actor'], 'Gärtnerei')
        self.assertNotEqual(commissioning['id'], performance['id'])
        self.assertEqual(commissioning['derived']['citations'], performance['derived']['citations'])

    def test_split_extension_sentence_limit_and_exception_survive_together(self):
        result = self.check_case('task-evidence-deadline-extension-and-cap')
        text = result['working_units'][0]['derived']['source_text']
        self.assertIn('höchstens um vier Werktage', text)
        self.assertIn('bleibt die Frist unverändert', text)
        self.assertNotIn('Telefonzentrale', text)

    def test_each_alternative_has_its_own_complete_shared_evidence(self):
        result = self.check_case('task-evidence-alternative-documentation')
        first, second = result['working_units']
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(first['derived']['citations'], second['derived']['citations'])
        self.assertIn('Dann entfällt die Vorlage', first['derived']['source_text'])

    def test_payment_rows_and_the_correct_withholding_exception_survive(self):
        result = self.check_case('task-evidence-payment-table')
        first, second = (u['derived']['source_text'] for u in result['working_units'])
        self.assertIn('20 Prozent', first)
        self.assertNotIn('einbehalten', first)
        self.assertIn('80 Prozent', second)
        self.assertIn('einbehalten', second)
        self.assertNotIn('Kontaktdaten', first + second)

    def test_payout_keeps_prerequisite_deductions_and_offset_restriction(self):
        result = self.check_case('task-evidence-conditional-refund-and-offset')
        text = result['working_units'][0]['derived']['source_text']
        self.assertIn('Wenn der Auftraggeber', text)
        self.assertIn('Reparaturkosten', text)
        self.assertIn('Verrechnung', text)
        self.assertNotIn('Kundenportal', text)

    def test_selecting_one_paragraph_does_not_silently_add_a_separate_qualifying_paragraph(self):
        case = CASES['task-evidence-deadline-extension-and-cap']
        pages = copy.deepcopy(case['pages'])
        pages[0]['text'] = pages[0]['text'].replace('\n', '\n\n', 1)
        document = Document('D1', 'Fictional contract', 'fictional.pdf', schema_version=6,
                            source_key='fictional', pages=tuple(pages))
        raw = {'working_units': [{'title': 'Prüfbericht übergeben', 'actor': 'Auftragnehmer',
                                 'evidence_ids': ['S1']}], 'carry_forward': ''}
        calls = []
        result = extract_page_units([document], SimpleNamespace(extract=lambda p:
            (calls.append(p), copy.deepcopy(raw))[1]), stop_after='PAGE_EXTRACTION')
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['gemini_responses'][0]['gemini'], raw)
        self.assertEqual(result['working_units'][0]['derived']['source_text'],
                         case['pages'][0]['text'].splitlines(keepends=True)[0])
