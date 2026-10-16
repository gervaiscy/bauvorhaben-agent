from page_test_support import pages_fixture
import copy
import json
import tempfile
import unittest
from pathlib import Path

from customer_report import compile_report, render_report, fingerprint
from enrich_working_units import load_manifest
from test_customer_report import fixture


class PipelineIntegrityTests(unittest.TestCase):
    def test_manifest_rejects_same_ids_different_text(self):
        source, _, _ = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            lines = list(source.lines.values())
            for name, data in [('units.json', source.assembled), ('lines.json', lines), ('pages.json', pages_fixture(lines))]:
                (p/name).write_text(json.dumps(data))
            manifest = {'contract_signed': '2025-07-03', 'sources': [{'key': 'blb', 'working_units': 'units.json', 'pages': 'pages.json'}]}
            (p/'manifest.json').write_text(json.dumps(manifest))
            load_manifest(p/'manifest.json')
            lines[0]['text'] = 'Different extraction, same line ID'
            data=json.loads((p/'pages.json').read_text());data['pages'][0]['lines'][0]['text']='Tampered line';(p/'pages.json').write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'differs from authoritative'):
                load_manifest(p/'manifest.json')

    def test_unit_snapshot_must_match_transcription(self):
        source, _, _ = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            source.assembled['source_snapshots'] = [{'line_ids': ['L1'], 'sha256': 'different'}]
            for name, data in [('units.json', source.assembled), ('lines.json', list(source.lines.values())), ('pages.json', pages_fixture(list(source.lines.values())))]:
                (p/name).write_text(json.dumps(data))
            (p/'manifest.json').write_text(json.dumps({'contract_signed': '2025-07-03', 'sources': [{'key': 'blb', 'working_units': 'units.json', 'pages': 'pages.json'}]}))
            with self.assertRaisesRegex(ValueError, 'different transcription'):
                load_manifest(p/'manifest.json')

    def test_ocr_disagreement_blocks_publication(self):
        _, payload, proposal = fixture()
        payload['source_metadata'] = {'blb': {'status': 'unverified_needs_review'}}
        self.assertIn('TRANSCRIPTION_REVIEW', [e['code'] for e in compile_report(payload, proposal)['errors']])

    def test_tampered_report_cannot_reuse_approval(self):
        _, payload, proposal = fixture()
        report = compile_report(payload, proposal)
        report['units'][0]['name'] = 'Tampered'
        with self.assertRaisesRegex(ValueError, 'modified'):
            render_report(report)

    def test_date_rule_cycle_detected(self):
        _, payload, proposal = fixture()
        proposal['rules'][0]['anchor'] = 'anchor:cycle'
        proposal['rules'].append(dict(copy.deepcopy(proposal['rules'][0]), id='cycle', target='anchor:cycle', anchor='anchor:price'))
        self.assertIn('DATE_RULE_CYCLE', [e['code'] for e in compile_report(payload, proposal)['errors']])

    def test_unsupported_conditional_branch_is_not_scheduled(self):
        _, payload, proposal = fixture()
        proposal['rules'][2]['requires_selection'] = 'electrical'
        report = compile_report(payload, proposal)
        self.assertNotIn('electrical', report['schedules']['conservative']['deadlines'])


if __name__ == '__main__': unittest.main()
