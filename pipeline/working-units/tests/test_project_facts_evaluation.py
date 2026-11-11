from historical_facts import collect_historical_facts
from clause_fixtures import labeled_answer
from clause_fixtures import decode_prompt
from clause_fixtures import index_answer, interpreted
"""Evaluation plumbing preserves evidence; semantic expectations require live review."""
import copy
from collections import Counter
import json
from pathlib import Path
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]/'evaluation'
sys.path.insert(0, str(BASE))
import project_facts_evaluation as evaluation
from bauvo_working_units.gemini import IncompleteResponse
from bauvo_working_units.project_flow import Document
from bauvo_working_units.project_facts import collect_project_facts
from bauvo_working_units.citation_evidence import catalog
from types import SimpleNamespace


class ProjectFactsEvaluationTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        for name, value in [('CASES', BASE/'project-facts-cases.json'),
                            ('collect_project_facts', collect_historical_facts)]:
            patcher = patch.object(evaluation, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_claim_controls_detect_missing_qualifying_evidence_even_when_fact_accounting_is_complete(self):
        cases = json.loads((BASE/'project-facts-cases.json').read_text())['cases']
        controls = [c for c in cases if 'claim_expectations' in c]
        self.assertEqual(len(controls), 5)
        for case in controls:
            with self.subTest(case=case['id']):
                doc = Document('D1', 'Synthetic project document', 'synthetic.pdf', schema_version=6,
                               source_key=case['id'], pages=({'pdf_page':1, 'text':case['text']},))
                entries = catalog([{**doc.pages[0], 'source_key':doc.source_key, 'document_id':doc.id}])
                refs = [{'evidence_id':e['evidence_id']} for e in entries]
                row = interpreted(refs)
                groups = case['claim_expectations']['evidence_groups']
                selected = {n for group in groups for n in group}
                row['claims'] = [{'statement':' '.join(entries[n-1]['quote'].strip() for n in group),
                                  'evidence_ids':[refs[n-1]['evidence_id'] for n in group]}
                                 for group in groups]
                row['claims'] += [{'statement':e['quote'].strip(), 'evidence_ids':[e['evidence_id']]}
                                  for n,e in enumerate(entries,1) if n not in selected]
                answer = {'verpflichtungen':[row], 'nicht_als_fakt_erfasst':[]}
                def collect(value):
                    return collect_historical_facts([doc], SimpleNamespace(extract=lambda p:labeled_answer([doc], value)))
                correct = collect(answer)
                self.assertTrue(evaluation.claim_evidence_checks(case,correct)['passed'])
                wrong = copy.deepcopy(answer)
                # Every source is still cited at fact level, but qualifiers are disconnected.
                wrong['verpflichtungen'][0]['claims'] = [
                    {'statement':e['quote'].strip(), 'evidence_ids':[e['evidence_id']]} for e in entries]
                disconnected = collect(wrong)
                self.assertEqual(disconnected['status'],'recorded')
                checks = evaluation.claim_evidence_checks(case,disconnected)
                self.assertFalse(checks['passed'])
                self.assertTrue(checks['missing_claim_evidence_groups'])
                self.assertTrue(checks['semantic_review_required'])

    def test_authored_controls_detect_wrong_exclusions_and_grouping_despite_complete_accounting(self):
        cases=json.loads((BASE/'project-facts-cases.json').read_text())['cases']
        controls=[case for case in cases if 'selection_expectations' in case]
        self.assertEqual(len(controls),9)
        for case in controls:
            with self.subTest(case=case['id']):
                doc=Document('D1','Synthetic project document','synthetic.pdf',schema_version=6,
                    source_key=case['id'],pages=({'pdf_page':1,'text':case['text']},))
                entries=catalog([{**doc.pages[0],'source_key':doc.source_key,'document_id':doc.id}])
                line_ids={number:[{'evidence_id':entry['evidence_id']}] for number,entry in enumerate(entries,1)}
                expected=case['selection_expectations']
                answer={'verpflichtungen':[interpreted([ref for n in group for ref in line_ids[n]],
                    uncertainty='Die Zuordnung ist im Quelltext nicht eindeutig.' if any(
                        set(lines) <= set(group) for lines in expected.get('uncertain_groups', [])) else None)
                    for group in expected['clause_groups']], 'nicht_als_fakt_erfasst':[
                    {**line_ids[n][0],'grund':'Synthetic expected exclusion'} for n in expected['excluded_lines']]}
                def result(value):
                    return collect_historical_facts([doc],SimpleNamespace(extract=lambda p:labeled_answer([doc], value)))
                correct=result(answer)
                self.assertEqual(correct['status'],'recorded')
                self.assertTrue(evaluation.selection_checks(case,correct)['passed'])
                wrong=copy.deepcopy(answer)
                uses=Counter(ref['evidence_id'] for row in wrong['verpflichtungen'] for ref in row['zitate'])
                position=next(i for i,row in enumerate(wrong['verpflichtungen'])
                              if any(uses[ref['evidence_id']]==1 for ref in row['zitate']))
                removed=wrong['verpflichtungen'].pop(position)['zitate']
                remaining={r['evidence_id'] for row in wrong['verpflichtungen'] for r in row['zitate']}
                wrong['nicht_als_fakt_erfasst'] += [{**ref,'grund':'Incorrect administrative classification'}
                    for ref in removed if ref['evidence_id'] not in remaining]
                wrongly_excluded=result(wrong)
                self.assertEqual(wrongly_excluded['status'],'recorded')
                checks=evaluation.selection_checks(case,wrongly_excluded)
                self.assertFalse(checks['passed']);self.assertTrue(checks['missing_indexed_ids'])
                if len(expected['clause_groups'])>1:
                    wrong=copy.deepcopy(answer)
                    refs={ref['evidence_id']:ref for row in wrong['verpflichtungen'] for ref in row['zitate']}
                    wrong['verpflichtungen']=[interpreted(list(refs.values()))]
                    merged=result(wrong)
                    self.assertEqual(merged['status'],'recorded')
                    self.assertFalse(evaluation.selection_checks(case,merged)['groups_match'])

    def test_complete_accounting_does_not_hide_sentence_fragments_or_invented_scope(self):
        cases={case['id']:case for case in json.loads((BASE/'project-facts-cases.json').read_text())['cases']}
        incorrect_groups={
            # Every ID is present, but the VAT condition and schedule lose their beginnings.
            'wrapped-sentences-share-boundary-lines':[[1],[2,3],[4],[5]],
            # Keep the active rule while wrongly excluding its document identity and context.
            'document-identity-dates-and-project-circumstances':[[4],[6]],
            # Retention is an exception for the first instalment, not all three.
            'independent-instalments-with-scoped-retention':[[1,2,5,6],[1,3,5,6],[1,4,5,6]],
            # Adjacent numbers do not establish two areas for one room.
            'unresolved-sketch-measurements-remain-unassigned':[[1,2],[3,4,5],[6,7]],
        }
        for identifier,groups in incorrect_groups.items():
            with self.subTest(case=identifier):
                case=cases[identifier]
                doc=Document('D1','Synthetic project document','synthetic.pdf',schema_version=6,
                    source_key=identifier,pages=({'pdf_page':1,'text':case['text']},))
                entries=catalog([{**doc.pages[0],'source_key':identifier,'document_id':doc.id}])
                refs={n:{'evidence_id':entry['evidence_id']} for n,entry in enumerate(entries,1)}
                used={n for group in groups for n in group}
                answer={'verpflichtungen':[interpreted([refs[n] for n in group]) for group in groups],
                    'nicht_als_fakt_erfasst':[{**ref,'grund':'Incorrectly considered administrative'}
                        for n,ref in refs.items() if n not in used]}
                answer=labeled_answer([doc], answer)
                result=collect_historical_facts([doc],SimpleNamespace(extract=lambda p:copy.deepcopy(answer)))
                self.assertEqual(result['status'],'recorded')
                self.assertEqual(result['review_findings'],[])
                self.assertEqual(result['gemini_responses'][0]['gemini'],answer)
                checks=evaluation.selection_checks(case,result)
                self.assertFalse(checks['passed'])
                self.assertTrue(not checks['groups_match'] or checks['missing_uncertainty_groups'])

    def test_controls_use_production_pass_without_leaking_expectations_or_rewriting_answers(self):
        cases = json.loads((BASE/'project-facts-cases.json').read_text())['cases']
        self.assertEqual({c['id'] for c in cases}, {'price-binding-with-included-scheduling',
            'independent-offered-table-positions', 'permissions-versus-included-services',
            'condition-in-following-sentence', 'contract-attachment-labelled-offer',
            'both-parties-and-general-payment-rule', 'payment-milestones-and-retention-basis',
            'weather-exception-in-neighbouring-sentence',
            'contract-hierarchy-costs-and-qualified-consent',
            'source-scoped-footer-and-express-amendment',
            'qualified-assurance-without-strengthening',
            'ambiguous-area-assignment-is-not-an-alternative',
            'offer-footer-with-referenced-credit-conditions',
            'footer-administration-and-binding-requirement', 'pure-benefits-and-specific-included-service',
            'independent-table-positions-with-wrapped-descriptions',
            'independent-rules-with-shared-qualification', 'shared-condition-for-separate-positions',
            'wrapped-sentences-share-boundary-lines', 'document-identity-dates-and-project-circumstances',
            'independent-instalments-with-scoped-retention', 'unresolved-sketch-measurements-remain-unassigned',
            'conditional-credit-deferred-payout', 'qualified-assurance-across-lines',
            'unsigned-consent-form-observation', 'bank-confirmation-content-and-exception',
            'roof-specification-in-following-line', 'documented-project-configuration',
            'conditional-feature-does-not-establish-selection', 'offer-presence-is-not-acceptance',
            'prerequisite-does-not-establish-completion', 'multiple-provisions-in-one-span',
            'qualified-security-is-not-absolute', 'unassigned-payment-note-is-observation', 'postal-exception-survives-credit-interpretation', 'priced-offer-is-not-agreement', 'base-price-row-must-state-amount', 'signature-ocr-remains-observation'})
        calls, answers = [], []
        class Model:
            def extract(self, prompt):
                self_kind, payload = prompt.split('SOURCE DATA:\n')
                self_data = decode_prompt(prompt)
                calls.append((self_kind, self_data))
                c = {'pdf_page': 1, 'text': 'Unverified quotation'}
                # Deliberately questionable proposal: the runner must not improve it.
                answer = {'verpflichtungen': [{'verpflichtung': 'Provider value', 'zitate': [c]}]}
                answers.append(copy.deepcopy(answer))
                return answer
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'results'
            summary = evaluation.run(output, 'unused', model=Model())
            self.assertEqual(len(summary), len(cases))
            self.assertTrue(all(row['status'] == 'incomplete' for row in summary))
            for case, answer in zip(cases, answers):
                folder = output/case['id']
                result = json.loads((folder/'result.json').read_text())
                self.assertEqual(result['gemini_responses'][0]['gemini'], answer)
                self.assertEqual(result['facts'][0]['gemini'], answer['verpflichtungen'][0])
                self.assertEqual(json.loads((folder/'raw.json').read_text()), [answer])
                self.assertEqual(json.loads((folder/'expectations.json').read_text()), case['expectations'])
                self.assertIn('invalid_evidence_reference', {f['code'] for f in result['facts'][0]['review_findings']})
                self.assertEqual(result['facts'][0]['derived']['verified_citations'], [])
            for head, data in calls:
                self.assertTrue(head.startswith('PAGE_PROJECT_FACTS.'))
                self.assertEqual(set(data), {'source_pages','context_scope','documents','evidence_catalog','catalog_protocol','target_evidence_ids','evidence_protocol','reference_map','reference_protocol','provisions','provision_protocol','target_provision_ids'})
                self.assertEqual(len(data['source_pages']), 1)
                self.assertNotIn('selection_expectations',data)
                self.assertNotIn('claim_expectations',data)
            self.assertEqual([data['source_pages'][0]['text'] for _, data in calls],
                             [case['text'] for case in cases])
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
                answer=index_answer(decode_prompt(prompt))
                self.last_raw_response = json.dumps(answer)
                self.last_finish_reason = 'STOP'
                return answer
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'results'
            summary = evaluation.run(output, 'unused', model=Model())
            self.assertEqual(len(calls), len(summary) + 1)
            self.assertEqual(calls[0], calls[1])
            self.assertEqual(summary[0]['status'], 'request_failed')
            self.assertTrue(all(row['status'] == 'recorded' for row in summary[1:]))
            folder = output/summary[0]['case']
            self.assertEqual(json.loads((folder/'raw.json').read_text()), ['{unfinished', '{unfinished'])
            self.assertEqual(len(json.loads((folder/'raw.attempts.json').read_text())), 2)
