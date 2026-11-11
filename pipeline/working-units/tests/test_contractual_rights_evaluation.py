"""Held-out rights controls use live contracts but offline providers; no accuracy claim."""
import copy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

BASE = Path(__file__).resolve().parents[1]/'evaluation'
sys.path.insert(0, str(BASE))
import project_facts_evaluation as evaluation
from bauvo_working_units.project_facts import collect_project_facts
from test_contractual_rights import document, payload, answer


class RightsEvaluationTests(unittest.TestCase):
    def test_formal_rules_are_retained_without_homeowner_service_or_executory_duty(self):
        cases = {c['id']: c for c in json.loads(evaluation.CASES.read_text())['cases']}
        for identifier in ('offer-footer-binding-confirmation', 'contract-formation-confirmation',
                           'amendments-require-text-form'):
            case = cases[identifier]
            # Footer case deliberately contains an unaccepted service and contact data.
            statement = case['text'].splitlines()[-1]
            for wording, expected in ((None, False), (statement, True)):
                selected = []
                def provider(prompt):
                    data = payload(prompt)
                    raw = (answer(wording, [data['target_evidence_ids'][-1]])
                           if wording else {'ansprueche': []})
                    selected.append(copy.deepcopy(raw))
                    return raw
                result = collect_project_facts([document(case['text'])], SimpleNamespace(extract=provider))
                with self.subTest(case=identifier, retained=expected):
                    self.assertEqual(result['status'], 'recorded')
                    self.assertEqual(evaluation.rights_checks(case, result)['passed'], expected)
                    self.assertEqual(result['gemini_responses'][0]['gemini'], selected[0])
                    if wording:
                        self.assertEqual(result['facts'][0]['derived']['verified_citations'][0]['quote'], statement)

    def test_formal_requirements_cannot_be_extended_to_another_company_or_strengthened(self):
        cases = {c['id']: c for c in json.loads(evaluation.CASES.read_text())['cases']}
        for identifier, wrong_statement in (
                ('offer-footer-binding-confirmation',
                 'Erklärungen der Nord Vertrieb GmbH und der Süd Bau GmbH bedürfen einer schriftlichen Bestätigung ihrer Geschäftsführung.'),
                ('contract-formation-confirmation',
                 'Die schriftliche Auftragsbestätigung des Auftragnehmers liegt vor; der Vertrag ist bereits zustande gekommen.'),
                ('amendments-require-text-form',
                 'Ein Nachtrag muss von beiden Vertragsparteien in Schriftform bestätigt werden.')):
            case = cases[identifier]
            def provider(prompt):
                data = payload(prompt)
                return answer(wrong_statement, data['target_evidence_ids'])
            result = collect_project_facts([document(case['text'])], SimpleNamespace(extract=provider))
            with self.subTest(case=identifier):
                self.assertEqual(result['status'], 'recorded')
                self.assertFalse(evaluation.rights_checks(case, result)['passed'])
                self.assertEqual(result['facts'][0]['gemini']['aussage'], wrong_statement)

    def test_standalone_duties_and_permissions_require_results_even_without_homeowner_entitlement(self):
        cases = {c['id']: c for c in json.loads(evaluation.CASES.read_text())['cases']}
        for identifier in ('homeowner-duty-only', 'bounded-advertising-permission',
                           'single-notified-viewing'):
            case = cases[identifier]
            for selected, expected in [({'ansprueche': []}, False), (answer(case['text']), True)]:
                result = collect_project_facts([document(case['text'])],
                    SimpleNamespace(extract=lambda p: copy.deepcopy(selected)))
                with self.subTest(case=identifier, empty=not selected['ansprueche']):
                    self.assertEqual(result['status'], 'recorded')
                    self.assertEqual(evaluation.rights_checks(case, result)['passed'], expected)
                    self.assertEqual(result['gemini_responses'][0]['gemini'], selected)

    def test_independent_rules_keep_qualifications_without_invented_relationships(self):
        cases = {c['id']: c for c in json.loads(evaluation.CASES.read_text())['cases']}
        for identifier, mutation in (
                ('independent-credit-and-viewing-permission',
                 lambda rows: rows[1].update(aussage='Als Gegenleistung: '+rows[1]['aussage'])),
                ('homeowner-payment-and-cost-restriction',
                 lambda rows: rows[0].update(aussage=rows[0]['aussage'].replace(
                     'nach Zugang der Herstellungsmitteilung', 'nach Rechnungsstellung')))):
            case = cases[identifier]
            complete = {'ansprueche': [{'aussage': line, 'belege': [f'S{n}']}
                                      for n, line in enumerate(case['text'].splitlines(), 1)]}
            wrong = copy.deepcopy(complete)
            mutation(wrong['ansprueche'])
            for selected, expected in ((complete, True), (wrong, False)):
                result = collect_project_facts([document(case['text'])],
                    SimpleNamespace(extract=lambda p: copy.deepcopy(selected)))
                with self.subTest(case=identifier, correct=expected):
                    self.assertEqual(result['status'], 'recorded')
                    self.assertEqual(evaluation.rights_checks(case, result)['passed'], expected)
                    self.assertEqual(result['gemini_responses'][0]['gemini'], selected)

    def test_current_cases_use_rights_contract_without_leaking_expectations_or_correcting_answers(self):
        cases = json.loads(evaluation.CASES.read_text())['cases']
        self.assertEqual(len(cases), 21)
        prompts, answers = [], []
        def provider(prompt):
            data = payload(prompt)
            prompts.append(data)
            self.assertNotIn('rights_expectations', data)
            self.assertNotIn('expectations', data)
            # Deliberately no legal analysis: the runner must not improve this.
            raw = answer('Synthetic provider interpretation.', [data['target_evidence_ids'][0]])
            answers.append(copy.deepcopy(raw))
            return raw
        with TemporaryDirectory() as tmp:
            output = Path(tmp)/'evaluation'
            summary = evaluation.run(output, model=SimpleNamespace(extract=provider))
            self.assertEqual(len(summary), 21)
            self.assertTrue(all(row['status'] == 'recorded' for row in summary))
            self.assertTrue(all(not row['rights_checks']['passed'] for row in summary))
            for case, data, raw in zip(cases, prompts, answers):
                self.assertEqual(''.join(s[1] for p in data['source_pages'] for s in p['segments']), case['text'])
                result = json.loads((output/case['id']/'result.json').read_text())
                self.assertEqual(result['schema_version'], 15)
                self.assertEqual(result['gemini_responses'][0]['gemini'], raw)
                self.assertEqual(json.loads((output/case['id']/'raw.json').read_text()), [raw])
            self.assertTrue(json.loads((output/'integrity.json').read_text())['inputs_and_code_unchanged'])

    def test_complete_quotations_cannot_hide_strengthened_guarantee_in_interpretation(self):
        case = next(c for c in json.loads(evaluation.CASES.read_text())['cases'] if c['id'] == 'qualified-security')
        doc = document(case['text'])
        wrong = collect_project_facts([doc], SimpleNamespace(extract=lambda p:
            answer('Der Bauherr hat Anspruch auf eine Bürgschaft für die mängelfreie Herstellung.')))
        self.assertEqual(wrong['status'], 'recorded')
        self.assertIn('ohne wesentliche Mängel', wrong['facts'][0]['derived']['text'])
        self.assertFalse(evaluation.rights_checks(case, wrong)['passed'])
        correct = collect_project_facts([doc], SimpleNamespace(extract=lambda p: answer(case['text'])))
        self.assertTrue(evaluation.rights_checks(case, correct)['passed'])

    def test_empty_offer_or_signature_selection_is_valid_and_does_not_create_fake_facts(self):
        cases = [c for c in json.loads(evaluation.CASES.read_text())['cases']
                 if c['rights_expectations']['max_count'] == 0]
        self.assertEqual(len(cases), 3)
        for case in cases:
            result = collect_project_facts([document(case['text'])],
                                           SimpleNamespace(extract=lambda p: {'ansprueche': []}))
            with self.subTest(case=case['id']):
                self.assertTrue(evaluation.rights_checks(case, result)['passed'])
                self.assertEqual(result['source_documents'][0]['pages'][0]['text'], case['text'])

    def test_unestablished_entitlements_are_omitted_instead_of_returned_as_candidate_observations(self):
        case = next(c for c in json.loads(evaluation.CASES.read_text())['cases']
                    if c['id'] == 'uncertain-drawing-inclusion')
        candidates = [answer('Der Bauherr hat Anspruch auf das dargestellte Arbeitszimmer.'),
                      answer('Die Zeichnung beschreibt ein Arbeitszimmer; die vertragliche Einbeziehung ist nicht belegt.'),
                      {'ansprueche': []}]
        for selected in candidates:
            result = collect_project_facts([document(case['text'])],
                SimpleNamespace(extract=lambda p: copy.deepcopy(selected)))
            with self.subTest(selected=selected):
                self.assertEqual(result['status'], 'recorded')
                self.assertEqual(result['gemini_responses'][0]['gemini'], selected)
                self.assertEqual(evaluation.rights_checks(case, result)['passed'], not selected['ansprueche'])

    def test_documented_part_is_retained_without_unsupported_additions(self):
        case = next(c for c in json.loads(evaluation.CASES.read_text())['cases']
                    if c['id'] == 'partially-established-service-scope')
        for statement, expected in [
            ('Der Bauherr hat Anspruch auf eine montierte Hauseingangstür aus Kunststoff.', True),
            ('Der Bauherr hat Anspruch auf eine Hauseingangstür aus Kunststoff und ein Aluminium-Vordach.', False),
            (None, False)]:
            def provider(prompt):
                data = payload(prompt)
                self.assertNotIn('rights_expectations', data)
                return answer(statement, data['target_evidence_ids']) if statement else {'ansprueche': []}
            result = collect_project_facts([document(case['text'])], SimpleNamespace(extract=provider))
            with self.subTest(statement=statement):
                self.assertEqual(result['status'], 'recorded')
                self.assertEqual(evaluation.rights_checks(case, result)['passed'], expected)

    def test_conditional_entitlement_is_retained_without_proof_of_fulfilment(self):
        case = next(c for c in json.loads(evaluation.CASES.read_text())['cases']
                    if c['id'] == 'conditional-discount-with-two-dates')
        selected = answer('Dem Bauherrn stehen 1.800 EUR Nachlass zu, sofern die Vertragsunterzeichnung bis zum 30.04.2025 erfolgt.')
        result = collect_project_facts([document(case['text'])],
            SimpleNamespace(extract=lambda p: copy.deepcopy(selected)))
        self.assertEqual(result['gemini_responses'][0]['gemini'], selected)
        self.assertEqual(result['status'], 'recorded')
        self.assertTrue(evaluation.rights_checks(case, result)['passed'])

    def test_complete_evidence_does_not_mask_lost_exceptions_or_invented_conditions(self):
        cases = {c['id']: c for c in json.loads(evaluation.CASES.read_text())['cases']}
        incomplete = {
            'conditional-credit-with-postal-exception':
                '750 EUR Gutschrift, wenn sämtliche Rechnungen innerhalb von 16 Tagen nach Rechnungsstellung bezahlt werden. '
                'Auszahlung innerhalb von vier Wochen nach letzter Zahlung und Rückgabe der Bürgschaft.',
            'weather-qualified-completion':
                'Fertigstellung innerhalb von 40 Wochen ab Erdarbeiten; Verlängerung unter bestimmten Bedingungen um höchstens drei Monate.',
            'qualified-assignment-confirmation':
                'Abtretung statt Zahlungsbürgschaft gegen eine entsprechende Bestätigung.',
            'ownership-evidence-is-associated-duty':
                'Der Anspruch steht unter der Bedingung, dass der Grundbuchauszug zehn Wochen vor Baubeginn vorgelegt wird.',
            'single-notified-viewing':
                'Hausbesichtigungen nach Anmeldung innerhalb von acht Monaten nach Fertigstellung.'}
        for identifier, statement in incomplete.items():
            case = cases[identifier]
            for wording, expected in [(statement, False), (case['text'], True)]:
                raw = []
                def provider(prompt):
                    data = payload(prompt)
                    selected = answer(wording, data['target_evidence_ids'])
                    raw.append(copy.deepcopy(selected))
                    return selected
                result = collect_project_facts([document(case['text'])], SimpleNamespace(extract=provider))
                with self.subTest(case=identifier, complete=expected):
                    self.assertEqual(result['status'], 'recorded')
                    self.assertEqual(result['gemini_responses'][0]['gemini'], raw[0])
                    self.assertEqual(evaluation.rights_checks(case, result)['passed'], expected)
