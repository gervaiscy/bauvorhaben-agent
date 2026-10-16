from page_test_support import pages_fixture
"""Business regressions and a real CLI boundary test; no cloud credentials required."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from customer_report import build_input, compile_report, fingerprint, render_report, shifted, reconciliation_input
from enrich_working_units import Source
from source_integrity import align, verify_citations, realign_citations


def fixture():
    text = 'Elektroplan vor Wandplanung. Planungszeit 4 bis 8 Wochen. Bau innerhalb von 10 Wochen. Preisbindung 12 Monate.'
    lines = {'L1': {'id': 'L1', 'pdf_page': 1, 'text': text}}
    refs = ['electrical', 'planning', 'release', 'start']
    source = Source('blb', {'working_units': [
        {'id': r, 'gemini': {'name': r}, 'derived': {'source_line_ids': ['L1'], 'source_excerpt': text}}
        for r in refs]}, [{'id': 'S1', 'line_ids': ['L1']}], lines)
    payload = build_input([source], date(2025, 7, 3), {'contract_signed_verified': True})
    c = [{'source_key': 'blb', 'line_ids': ['L1'], 'quote': text}]
    units = [{'id': r, 'member_refs': ['blb:' + r], 'name': r, 'actor': 'Bauherr', 'action': r,
              'kind': 'OBLIGATION', 'citations': copy.deepcopy(c),
              'scope': {'status': 'APPLIES', 'condition': None, 'condition_citations': [], 'reason': ''},
              'requirements': [], 'reconciliation_notes': []} for r in refs]
    def edge(index, predecessor, min_days, max_days):
        units[index]['requirements'] = [{'mode': 'ALL', 'selected': None, 'members': [
            {'unit_id': predecessor, 'lag_days': {'min': min_days, 'max': max_days}, 'citations': copy.deepcopy(c)}]}]
    edge(1, 'electrical', 0, 0)
    edge(2, 'planning', 28, 56)
    edge(3, 'release', 70, 70)
    def rule(rid, target, anchor, amount, unit='weeks'):
        return {'id': rid, 'target': target, 'anchor': anchor,
                'offset': {'unit': unit, 'min': amount, 'max': amount},
                'kind': 'PLANNING_TARGET', 'requires_selection': None, 'citations': copy.deepcopy(c), 'assumptions': []}
    proposal = {'input_fingerprint': payload['input_fingerprint'], 'units': units,
                'rules': [rule('price', 'anchor:price', 'contract_signed', 12, 'months'),
                          rule('start', 'unit:start', 'anchor:price', 0),
                          rule('bad_local_target', 'unit:electrical', 'anchor:price', -10)],
                'unresolved_questions': []}
    return source, payload, proposal


class IntegrityTests(unittest.TestCase):
    def test_quote_realignment_preserves_original_and_only_changes_unique_location(self):
        registry={'x':{'L1':{'text':'Vorlage der'},'L2':{'text':'Planung erforderlich.'}}}
        raw={'citations':[{'source_key':'x','line_ids':['L1'],'quote':'Vorlage der Planung erforderlich.'}]}
        aligned,repairs=realign_citations(raw,registry)
        self.assertEqual(aligned['citations'][0]['line_ids'],['L1','L2'])
        self.assertEqual(raw['citations'][0]['line_ids'],['L1'])
        self.assertEqual(len(repairs),1)
        verify_citations(aligned['citations'],registry)

    def test_ambiguous_or_wrong_document_quote_is_not_realigned(self):
        registry={'x':{'L1':{'text':'Beleg'},'L2':{'text':'Beleg'}},'y':{'L1':{'text':'Andere Klausel'}}}
        for citation in [{'source_key':'x','line_ids':['missing'],'quote':'Beleg'},
                         {'source_key':'y','line_ids':['L1'],'quote':'Beleg'},
                         {'source_key':'x','line_ids':['L1'],'quote':'Erfundener Beleg'}]:
            aligned,repairs=realign_citations(citation,registry)
            self.assertEqual(aligned,citation);self.assertEqual(repairs,[])
    def test_shifted_line_ids_repaired_by_exact_excerpt(self):
        unit = {'derived': {'source_line_ids': ['L1'], 'source_excerpt': 'Die erste Rate beträgt 5 %.'}}
        result = align(unit, {'L1': {'text': 'Streitbeilegung'}, 'L9': {'text': 'Die erste Rate beträgt 5 %.'}})
        self.assertEqual(result['status'], 'REALIGNED')
        self.assertEqual(result['line_ids'], ['L9'])
        self.assertEqual(unit['derived']['source_line_ids'], ['L1'])

    def test_ambiguous_or_missing_excerpt_never_guessed(self):
        unit = {'derived': {'source_line_ids': ['wrong'], 'source_excerpt': 'Beleg'}}
        self.assertEqual(align(unit, {'a': {'text': 'Beleg'}, 'b': {'text': 'Beleg'}})['status'], 'UNRESOLVED')
        self.assertEqual(align(unit, {'a': {'text': 'Andere Klausel'}})['status'], 'UNRESOLVED')

    def test_existing_line_id_does_not_validate_wrong_quote(self):
        with self.assertRaises(ValueError) as caught:
            verify_citations([{'source_key': 'x', 'line_ids': ['L1'], 'quote': 'Gutschrift'}], {'x': {'L1': {'text': 'Nettofläche'}}})
        self.assertIn("submitted_quote='Gutschrift'",str(caught.exception))
        self.assertIn("exact_source_text='Nettofläche'",str(caught.exception))


class SchedulingTests(unittest.TestCase):
    def test_unselected_option_error_identifies_dependent_action(self):
        _, payload, proposal = fixture()
        proposal['units'][0]['kind'] = 'OPTION'
        report = compile_report(payload, proposal)
        errors = [e['detail'] for e in report['errors'] if e['code'] == 'UNCONFIRMED_SELECTION']
        self.assertTrue(any('planning' in e and 'electrical' in e and 'Never invent' in e for e in errors))
        self.assertEqual(report['schedules'], {})

    def test_prompt_projection_preserves_all_source_lines_and_unit_ids(self):
        _, payload, _ = fixture()
        original = copy.deepcopy(payload)
        payload["units"][0]["original"]["reviews"] = [{"due": "invented"}]
        view = reconciliation_input(payload)
        self.assertEqual(view["source_registry"], payload["source_registry"])
        self.assertEqual([u["id"] for u in view["units"]], [u["id"] for u in payload["units"]])
        self.assertEqual(view["input_fingerprint"], original["input_fingerprint"])
        self.assertNotIn("reviews", view["units"][0])
        self.assertIn("reviews", payload["units"][0]["original"])

    def test_report_displays_confirmed_planning_baseline(self):
        _, payload, proposal = fixture()
        report = compile_report(payload, proposal)
        rendered = render_report(report)
        self.assertIn("Bestätigter Planungsanker: 2025-07-03", rendered)
        self.assertIn("Kalenderdaten sind Szenariowerte", rendered)
        self.assertIn("Quellen anzeigen", rendered)

    def test_excluded_work_keeps_its_exclusion_evidence_in_html(self):
        _, payload, proposal = fixture()
        for unit in proposal['units']: unit['requirements']=[]
        unit=proposal['units'][0]
        payload['source_registry']['blb']['L2']={'text':'Ohne Malerarbeiten <Beleg>', 'pdf_page':9}
        unit['scope'].update(status='DOES_NOT_APPLY',reason='Angebot schließt dies aus',
            condition_citations=[{'source_key':'blb','line_ids':['L2'],'quote':'Ohne Malerarbeiten <Beleg>'}])
        report=compile_report(payload,proposal)
        self.assertEqual(report['status'],'DRAFT')
        rendered=render_report(report)
        self.assertIn('Ausgeschlossen',rendered)
        self.assertIn('PDF-Seite 9',rendered)
        self.assertIn('Ohne Malerarbeiten &lt;Beleg&gt;',rendered)
        self.assertNotIn('PLANNING_TARGET:',rendered)

    def test_backward_propagation_fixes_electrical_prerequisite(self):
        _, payload, proposal = fixture()
        original = copy.deepcopy(proposal)
        report = compile_report(payload, proposal)
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['schedules']['conservative']['targets']['electrical'], '2026-02-27')
        self.assertEqual(report['schedules']['conservative']['targets']['release'], '2026-04-24')
        self.assertEqual(report['schedules']['optimistic']['targets']['electrical'], '2026-03-27')
        self.assertEqual(proposal, original)
        self.assertIn('bad_local_target', [d['rule_id'] for d in report['schedules']['conservative']['deadlines']['electrical']])

    def test_unknown_contract_anchor_has_no_fabricated_dates(self):
        _, payload, proposal = fixture()
        payload['settings']['contract_signed_verified'] = False
        report = compile_report(payload, proposal)
        self.assertEqual(report['status'], 'DRAFT')
        self.assertEqual(report['schedules']['conservative']['targets'], {})
        self.assertTrue(report['questions'])

    def test_planned_start_is_not_actual_start(self):
        _, payload, proposal = fixture()
        completion = copy.deepcopy(proposal['rules'][1]); completion.update(id='complete', target='unit:release', anchor='unit:start', offset={'unit': 'weeks', 'min': 46, 'max': 46})
        proposal['rules'].append(completion)
        result = compile_report(payload, proposal)
        self.assertFalse(any(d['rule_id'] == 'complete' for d in result['schedules']['conservative']['deadlines'].get('release', [])))
        self.assertTrue(any('unit:start' in q for q in result['questions']))

    def test_actual_event_forward_deadline(self):
        _, payload, proposal = fixture()
        payload['settings']['events'] = {'actual_start': {'verified': True, 'date': '2026-07-03', 'evidence': 'Bautagebuch 03.07.2026'}}
        proposal['rules'] = [dict(proposal['rules'][1], anchor='event:actual_start', target='unit:release', offset={'unit': 'weeks', 'min': 46, 'max': 46}, kind='CONTRACTUAL')]
        report = compile_report(payload, proposal)
        self.assertEqual(report['schedules']['conservative']['deadlines']['release'][0]['date'], '2027-05-21')

    def test_month_end_and_leap_year(self):
        self.assertEqual(shifted(date(2024, 2, 29), {'unit': 'months', 'min': 12, 'max': 12}, 'conservative'), date(2025, 2, 28))
        self.assertEqual(shifted(date(2026, 4, 24), {'unit': 'weeks', 'min': -12, 'max': -12}, 'conservative'), date(2026, 1, 30))

    def test_cycle_blocks_all_dates(self):
        _, payload, proposal = fixture()
        proposal['units'][0]['requirements'] = copy.deepcopy(proposal['units'][1]['requirements'])
        proposal['units'][0]['requirements'][0]['members'][0]['unit_id'] = 'start'
        result = compile_report(payload, proposal)
        self.assertIn('DEPENDENCY_CYCLE', [e['code'] for e in result['errors']])
        self.assertEqual(result['schedules'], {})

    def test_missing_dependency_blocks_publication(self):
        _, payload, proposal = fixture()
        proposal['units'][1]['requirements'][0]['members'][0]['unit_id'] = 'symbolic_unresolved_key'
        report = compile_report(payload, proposal)
        self.assertEqual(report['status'], 'BLOCKED')
        with self.assertRaises(ValueError): render_report(report)

    def test_conditional_scope_requires_project_proof(self):
        _, payload, proposal = fixture()
        proposal['units'][0]['scope']['condition'] = 'Malerarbeiten gesondert vereinbart'
        report = compile_report(payload, proposal)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertTrue(any('scope proof' in e['detail'] for e in report['errors']))

    def test_source_failure_cannot_become_exclusion(self):
        _, payload, proposal = fixture()
        payload['source_integrity']['blb:electrical']['status'] = 'UNRESOLVED'
        proposal['units'][0]['scope']['status'] = 'DOES_NOT_APPLY'
        report = compile_report(payload, proposal)
        self.assertIn('SOURCE_UNRESOLVED', [e['code'] for e in report['errors']])

    def test_any_requires_confirmed_choice_not_all_branches(self):
        _, payload, proposal = fixture()
        group = proposal['units'][3]['requirements'][0]
        group['mode'] = 'ANY'
        group['members'].append(dict(copy.deepcopy(group['members'][0]), unit_id='electrical'))
        payload['settings']['selections'] = ['release']
        report = compile_report(payload, proposal)
        self.assertEqual(report['errors'], [])
        self.assertEqual([e['from'] for e in report['dependency_edges'] if e['to'] == 'start'], ['release'])

    def test_duplicate_members_and_stale_proposal_rejected(self):
        _, payload, proposal = fixture()
        proposal['units'][1]['member_refs'].append('blb:electrical')
        proposal['input_fingerprint'] = 'old'
        self.assertEqual({e['code'] for e in compile_report(payload, proposal)['errors']}, {'STALE_INPUT', 'MEMBERSHIP'})

    def test_canonical_merge_retains_every_member(self):
        _, payload, proposal = fixture()
        duplicate = copy.deepcopy(payload['units'][0]); duplicate['id'] = 'blb:duplicate'
        payload['units'].append(duplicate)
        payload['source_integrity']['blb:duplicate'] = payload['source_integrity']['blb:electrical']
        proposal['units'][0]['member_refs'].append('blb:duplicate')
        report = compile_report(payload, proposal)
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['member_to_canonical']['blb:duplicate'], 'electrical')

    def test_historical_event_cannot_receive_deadline(self):
        _, payload, proposal = fixture()
        proposal['units'][0]['kind'] = 'EVENT'
        self.assertIn('EVENT_DEADLINE', [e['code'] for e in compile_report(payload, proposal)['errors']])

    def test_malformed_model_output_fails_closed(self):
        _, payload, proposal = fixture()
        for bad in [None, [], {}, {'units': [None], 'rules': []}]:
            self.assertEqual(compile_report(payload, bad)['status'], 'BLOCKED')
        proposal['units'][0]['scope'] = []
        self.assertEqual(compile_report(payload, proposal)['status'], 'BLOCKED')

    def test_review_bound_to_result_and_html_escaped(self):
        _, payload, proposal = fixture()
        proposal['units'][0]['name'] = '<script>alert(1)</script>'
        report = compile_report(payload, proposal)
        draft = render_report(report)
        self.assertIn('ENTWURF', draft)
        self.assertNotIn('<script>', draft)
        review = {'approved': True, 'review_digest': report['review_digest'], 'reviewed_by': 'Test reviewer', 'reviewed_at': '2026-10-11'}
        self.assertIn('Geprüfte', render_report(report, review))
        with self.assertRaises(ValueError): render_report(report, dict(review, review_digest='old'))

    def test_real_cli_writes_validated_json_and_html(self):
        source, payload, proposal = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            for name, value in [('units.json', source.assembled), ('lines.json', list(source.lines.values())), ('pages.json', pages_fixture(list(source.lines.values()))), ('proposal.json', proposal)]:
                (p/name).write_text(json.dumps(value))
            manifest = {'contract_signed': '2025-07-03', 'report_settings': payload['settings'], 'sources': [{'key': 'blb', 'working_units': 'units.json', 'pages': 'pages.json'}]}
            (p/'manifest.json').write_text(json.dumps(manifest))
            from enrich_working_units import load_manifest
            signed,loaded=load_manifest(p/'manifest.json')
            proposal['input_fingerprint']=build_input(loaded,signed,payload['settings'])['input_fingerprint']
            (p/'proposal.json').write_text(json.dumps(proposal))
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('customer_report.py')), '--manifest', str(p/'manifest.json'), '--proposal', str(p/'proposal.json'), '--output', str(p/'result.json'), '--html', str(p/'report.html')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(json.loads((p/'result.json').read_text())['schedules']['conservative']['targets']['electrical'], '2026-02-27')
            self.assertTrue((p/'report.html').exists())


if __name__ == '__main__': unittest.main()
