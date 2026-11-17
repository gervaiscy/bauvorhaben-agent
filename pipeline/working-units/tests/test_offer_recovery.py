"""Explicit offline recovery preserves raw errors and validates every original source span."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bauvo_working_units.citation_evidence import evidence_span
from bauvo_working_units.core import digest
from bauvo_working_units.phases.offers.recovery import recover_offers, main
from bauvo_working_units.phases.offers.response import resolve_response
from bauvo_working_units.phases.offers.stage import extract_offers
from bauvo_working_units.phases.offers.payload import LEGACY_PROTOCOL
from bauvo_working_units.phases.offers.validation import validate
from bauvo_working_units.phases.offers.context import review_context
from bauvo_working_units.phases.timing.stage import extract_timing
from test_offer_boundaries import baseline, doc, offer, wire


class OfferRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.documents = [doc('D1','local',[(1,'Das Gerät darf nicht verdeckt werden.'),
                                           (2,'Ein Sensor ist separat beauftragbar.'),
                                           (3,'Ein Monitor ist separat beauftragbar.')])]
        self.raw_quote = 'Das Gerät wird nicht verdeckt.'
        def provider(prompt):
            page = wire(prompt)['source_pages'][0]
            return {'offers':[offer([{'pdf_page':page['pdf_page'],
                'quote':self.raw_quote if page['pdf_page']==1 else page['passages'][0]['text']}])]}
        prior = baseline(self.documents,2)
        self.saved = extract_offers(prior,self.documents,SimpleNamespace(extract=provider),chunk_pages=2)
        # Authored historical v6 artifact: legacy quotation recovery is separate
        # from the current provider contract, which accepts passage labels only.
        self.saved['offers_protocol'] = LEGACY_PROTOCOL
        self.saved['review_findings'] = []
        for response in self.saved['gemini_responses']:
            if response['pass'] != 'PAGE_OFFERS':continue
            response.pop('reference_map',None);response.pop('reference_protocol',None)
            response['quote_protocol']='offer-original-quote-v1'
            pages=[{**p,'source_key':'local','document_id':'D1'} for p in self.documents[0].pages
                   if p['pdf_page'] in response['primary_pages']]
            resolved,audit,issues=resolve_response(response['gemini'],pages)
            response['citation_evidence']=audit
            self.saved['review_findings'].extend({'response_id':response['id'],**f} for f in issues)
            for record in self.saved['offers']:
                meta=record['derived']
                if meta['response_id']==response['id']:
                    meta['citations']=resolved['offers'][meta['response_position']]['citations']
                    meta['review_context']=review_context(meta['citations'],self.documents[0])
        validate(SimpleNamespace(responses=self.saved['gemini_responses'], offers=self.saved['offers'],
                                 findings=self.saved['review_findings']),protocol=LEGACY_PROTOCOL)
        self.assertEqual(self.saved['status'],'incomplete')
        page = {**self.documents[0].pages[0], 'source_key':'local', 'document_id':'D1'}
        self.manifest = {'input_sha256':digest(self.saved),'selections':[{
            'offer_id':self.saved['offers'][0]['id'], 'citation_position':0,
            'model_citation_sha256':digest(self.saved['offers'][0]['gemini']['citations'][0]),
            'source_span':evidence_span(page,0,len(page['text'])),
            'reason':'Source inspected; preserve the original prohibition instead of the model wording.'}]}

    def recover(self,saved=None,manifest=None):
        return recover_offers(self.saved if saved is None else saved,self.documents,
                              self.manifest if manifest is None else manifest,chunk_pages=2)

    def test_completed_run_recovers_without_rewriting_raw_or_loosening_default_matcher(self):
        before = copy.deepcopy(self.saved)
        result = self.recover()
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(self.saved,before)
        self.assertEqual(result['working_units'],before['working_units'])
        self.assertEqual(result['project_facts'],before['project_facts'])
        self.assertEqual([o['gemini'] for o in result['offers']],[o['gemini'] for o in before['offers']])
        self.assertEqual([r['gemini'] for r in result['gemini_responses']],
                         [r['gemini'] for r in before['gemini_responses']])
        self.assertTrue(all(o['derived']['offer_validation']=='valid' for o in result['offers']))
        self.assertEqual(result['offers'][0]['derived']['citations'][0]['quote'],self.documents[0].pages[0]['text'])
        self.assertTrue(result['offers'][0]['derived']['review_context'])
        audit = result['offer_source_recovery']
        self.assertEqual(audit['original_review_findings'],before['review_findings'])
        self.assertEqual(audit['new_provider_calls'],0)
        response = next(r for r in result['gemini_responses'] if r['pass']=='PAGE_OFFERS')
        self.assertEqual(response['citation_evidence']['bindings'][0]['method'],'explicit_original_source_selection')
        self.assertEqual(response['citation_evidence']['bindings'][0]['original_model_citation']['quote'],self.raw_quote)
        self.assertTrue(response['citation_evidence_history'][0]['findings'])
        page = {**self.documents[0].pages[0], 'source_key':'local', 'document_id':'D1'}
        self.assertTrue(resolve_response(response['gemini'],[page])[2])
        # Handoff accepts the resolved source binding, without a hidden review call.
        timing = extract_timing(result,self.documents,SimpleNamespace(extract=lambda _:self.fail('No tasks to assess')),
                                chunk_pages=2)
        self.assertIn('PAGE_TIMING',timing['completed_passes'])

    def test_inherited_findings_are_retained_and_do_not_become_offer_failures(self):
        saved = copy.deepcopy(self.saved)
        saved['review_findings'].append({'code':'existing_task_review_note','unit_id':'earlier-task'})
        manifest = {**self.manifest,'input_sha256':digest(saved)}
        result = self.recover(saved,manifest)
        self.assertEqual(result['status'],'needs_review')
        self.assertEqual(result['review_findings'],[saved['review_findings'][-1]])

    def test_missing_selection_stays_blocking_and_no_finding_is_silently_lost(self):
        result = self.recover(manifest={**self.manifest,'selections':[]})
        self.assertEqual(result['status'],'incomplete')
        self.assertTrue(any(f['code']=='offer_validation_failed' for f in result['review_findings']))
        with self.assertRaisesRegex(ValueError,'offer validation'):
            extract_timing(result,self.documents,SimpleNamespace(extract=lambda _:self.fail('No call')),chunk_pages=2)

    def test_wrong_input_citation_or_source_fingerprints_fail_before_output(self):
        mutations = [lambda m:m.update(input_sha256='changed'),
                     lambda m:m['selections'][0].update(model_citation_sha256='changed'),
                     lambda m:m['selections'][0]['source_span'].update(page_sha256='changed'),
                     lambda m:m['selections'][0]['source_span'].update(quote='Changed source wording.'),
                     lambda m:m['selections'][0]['source_span'].update(start=True),
                     lambda m:m['selections'][0]['source_span'].update(end=999),
                     lambda m:m['selections'][0]['source_span'].update(source_key='other'),
                     lambda m:m['selections'][0]['source_span'].update(document_id='D2'),
                     lambda m:m['selections'][0]['source_span'].update(pdf_page=3),
                     lambda m:m['selections'][0].update(offer_id='unknown'),
                     lambda m:m['selections'][0].update(citation_position=-1),
                     lambda m:m['selections'][0].update(reason=''),
                     lambda m:m['selections'].append(copy.deepcopy(m['selections'][0]))]
        before = copy.deepcopy(self.saved)
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                manifest = copy.deepcopy(self.manifest);mutate(manifest)
                with self.assertRaises(ValueError):self.recover(manifest=manifest)
        self.assertEqual(self.saved,before)

    def test_changed_original_pages_cannot_use_an_old_selection(self):
        changed = [doc('D1','local',[(1,'Source changed.'),(2,self.documents[0].pages[1]['text']),
                                   (3,self.documents[0].pages[2]['text'])])]
        with self.assertRaisesRegex(ValueError,'source pages'):
            recover_offers(self.saved,changed,self.manifest,chunk_pages=2)

    def test_cli_writes_successful_separate_result_and_refuses_overwrite(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);saved=root/'original.json';selections=root/'selections.json';output=root/'recovered/result.json'
            saved.write_text(json.dumps(self.saved));selections.write_text(json.dumps(self.manifest))
            original_bytes=saved.read_bytes()
            args=['recover','--working-units',str(saved),'--pages','pages','--documents','documents',
                  '--selections',str(selections),'--output',str(output),'--chunk-pages','2']
            with patch('sys.argv',args),patch('bauvo_working_units.phases.offers.recovery.load_documents',return_value=self.documents),\
                 patch('bauvo_working_units.gemini.GeminiModel',side_effect=AssertionError('No provider construction')):
                main()
                self.assertEqual(json.loads(output.read_text())['status'],'recorded')
                raw=json.loads(output.with_name('result.gemini-raw.json').read_text())
                self.assertEqual(raw,[r['gemini'] for r in self.saved['gemini_responses'] if r['pass']=='PAGE_OFFERS'])
                self.assertEqual(saved.read_bytes(),original_bytes)
                with self.assertRaises(SystemExit):main()


if __name__ == '__main__':
    unittest.main()
