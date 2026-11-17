"""Quote ownership, raw preservation, budgets and replay; no live provider calls."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from bauvo_working_units.cache import CachedModel
from bauvo_working_units.call_budget import BudgetedModel, CallBudget
from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.phases.offers.payload import PROTOCOL
from bauvo_working_units.phases.offers.stage import extract_offers
from bauvo_working_units.phases.timing.stage import extract_timing
from bauvo_working_units.project_flow import Document
from bauvo_working_units.schemas import RESPONSE_SCHEMAS
from bauvo_working_units.trace import RawRunRecorder, TracingModel


def doc(identifier, key, pages):
    return Document(identifier, identifier, key+'.pdf', schema_version=6, source_key=key,
                    pages=tuple({'pdf_page': n, 'text': text} for n, text in pages))


def baseline(docs, chunk_pages=10):
    return extract_page_units(docs, SimpleNamespace(extract=lambda _: {'working_units': []}),
                              stop_after='PAGE_EXTRACTION', chunk_pages=chunk_pages)


def wire(prompt):
    return json.loads(prompt.split('SOURCE DATA:\n', 1)[1])


def offer(refs, name='Kamerapaket', description='Eine Baustellenkamera kann gesondert beauftragt werden.'):
    return {'name': name, 'provider': 'Lieferant', 'description': description,
            'terms': ['240 EUR'], 'citations': refs}


def citation(page):
    return {'passage': page['passages'][0]['passage'] if 'passages' in page else f"P{page['pdf_page']}-1"}


class OfferBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.target = doc('D1', 'local', [(1, 'Eine Baustellenkamera kann für 240 EUR gesondert beauftragt werden.')])
        self.context = doc('D1', 'context', [(1, 'Das Kamerapaket umfasst zwei Geräte; Montage nur bei freiem Zugang.')])

    def run_offers(self, provider, documents=None, context=None, chunk_pages=10):
        docs = documents or [self.target]
        prior = baseline(docs, chunk_pages)
        before = copy.deepcopy(prior)
        result = extract_offers(prior, docs, SimpleNamespace(extract=provider), chunk_pages=chunk_pages,
                                context_documents=[self.context] if context is None else context)
        self.assertEqual(prior, before)
        self.assertEqual(result['working_units'], prior['working_units'])
        self.assertEqual(result['project_facts'], prior['project_facts'])
        return result

    def test_only_primary_page_text_reaches_each_request_once(self):
        docs = [doc('D1', 'local', [(n, f'Lokale Seite {n}.') for n in range(1,24)])]
        captured = []
        def provider(prompt):
            captured.append(wire(prompt)); return {'offers': []}
        result = self.run_offers(provider, documents=docs)
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual([p['pdf_page'] for d in captured for p in d['source_pages']],list(range(1,24)))
        self.assertTrue(all(len(d['source_pages'])<=10 for d in captured))
        self.assertTrue(all(set(d)=={'source_pages','project_facts'} for d in captured))
        self.assertNotIn(self.context.pages[0]['text'],json.dumps(captured))

    def test_labels_on_colliding_pages_bind_only_to_current_primary_document(self):
        raw={'offers':[offer([{'passage':'P1-1'}])]}
        result=self.run_offers(lambda _:copy.deepcopy(raw))
        self.assertEqual(result['status'],'recorded')
        bound=result['offers'][0]['derived']['citations'][0]
        self.assertEqual(bound['source_key'],'local')
        self.assertEqual(bound['quote'],self.target.pages[0]['text'])
        self.assertNotEqual(bound['quote'],self.context.pages[0]['text'])

    def test_unknown_label_does_not_stop_later_windows_or_weaken_validation(self):
        docs=[doc('D1','local',[(1,'Eine größere erforderliche Abdeckung ist gesondert beauftragbar.'),
                                 (2,'Eine Kamera ist gesondert beauftragbar.'),
                                 (3,'Ein Monitor ist gesondert beauftragbar.')])]; calls=[]
        def provider(prompt):
            data=wire(prompt);calls.append(data)
            refs=([{'passage':'P1-99'}]
                  if len(calls)==1 else [citation(data['source_pages'][0])])
            return {'offers':[offer(refs)]}
        result=self.run_offers(provider,documents=docs,chunk_pages=2)
        self.assertEqual(len(calls),2)
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(len(result['offers']),2)
        self.assertEqual([o['derived']['offer_validation'] for o in result['offers']],['invalid','valid'])
        self.assertEqual(result['offers'][0]['gemini']['citations'][0]['passage'],'P1-99')
        failures=[f for f in result['review_findings'] if f['code']=='offer_validation_failed']
        self.assertEqual(len(failures),1)
        self.assertTrue(any(f['code']=='unknown_passage_id' for f in result['review_findings']))
        self.assertEqual(result['completed_passes'],['PAGE_EXTRACTION','PAGE_OFFERS'])
        with self.assertRaisesRegex(ValueError,'offer validation'):
            extract_timing(result,docs,SimpleNamespace(extract=lambda _:self.fail('No call')),chunk_pages=2)

    def test_malformed_first_response_does_not_hide_later_document(self):
        docs=[self.target,doc('D2','local',[(2,'Ein Monitor ist gesondert beauftragbar.')])]; calls=[]
        raw=[{'offers':None},{'offers':[offer([citation(docs[1].pages[0])])]}]
        def provider(prompt):
            calls.append(prompt);return copy.deepcopy(raw[len(calls)-1])
        result=self.run_offers(provider,documents=docs)
        self.assertEqual(len(calls),2)
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(len(result['offers']),1)
        self.assertEqual(result['offers'][0]['derived']['offer_validation'],'valid')
        self.assertEqual([r['gemini'] for r in result['gemini_responses'] if r['pass']=='PAGE_OFFERS'],raw)

    def test_full_flow_finishes_offer_windows_but_never_calls_downstream_on_invalid_quote(self):
        docs=[self.target,doc('D2','local',[(2,'Eine Abdeckung ist gesondert beauftragbar.')])]; calls=[]
        def provider(prompt):
            if prompt.startswith('PAGE_EXTRACTION.'):
                return {'working_units':[]}
            self.assertTrue(prompt.startswith('PAGE_OFFERS.'),'No downstream request is allowed')
            calls.append(prompt)
            return {'offers':[offer([{'passage':'P999-1'}])]}
        result=extract_page_units(docs,SimpleNamespace(extract=provider))
        self.assertEqual(len(calls),2)
        self.assertEqual(result['completed_passes'],['PAGE_EXTRACTION','PAGE_OFFERS'])
        self.assertEqual(result['status'],'incomplete')

    def test_local_offer_keeps_canonical_provenance_and_original_json(self):
        raw={'offers':[offer([citation(self.target.pages[0])])]}
        result=self.run_offers(lambda _:copy.deepcopy(raw))
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(result['offers_protocol'],PROTOCOL)
        record=result['offers'][0]
        self.assertEqual(record['gemini'],raw['offers'][0])
        bound=record['derived']['citations'][0]
        self.assertEqual(bound['source_key'],'local')
        self.assertEqual(bound['document_id'],'D1')
        self.assertEqual(bound['quote'],self.target.pages[0]['text'])
        self.assertTrue({'evidence_id','start','end','page_sha256'}<=set(bound))
        self.assertEqual(record['derived']['offer_validation'],'valid')

    def test_unknown_quote_cannot_be_hidden_by_a_good_quote(self):
        raw={'offers':[offer([citation(self.target.pages[0]),{'passage':'P1-99'}])]}
        result=self.run_offers(lambda _:copy.deepcopy(raw))
        self.assertEqual(result['status'],'incomplete')
        self.assertTrue(any('unresolved_offer_evidence' in f.get('reasons',[]) for f in result['review_findings']))
        self.assertEqual(result['offers'][0]['gemini'],raw['offers'][0])

    def test_overlap_quote_cannot_become_a_new_primary_offer(self):
        docs=[doc('D1','local',[(n,f'Seite {n}: Zusatzleistung.') for n in (1,2,3)])]
        calls=[]
        def provider(prompt):
            calls.append(wire(prompt))
            return {'offers':[]} if len(calls)==1 else {'offers':[offer([citation(docs[0].pages[1])])]}
        result=self.run_offers(provider,documents=docs,context=[],chunk_pages=2)
        self.assertEqual(calls[1]['source_pages'],[{'pdf_page':3,'passages':[{'passage':'P3-1','text':docs[0].pages[2]['text']}]}])
        self.assertEqual(result['status'],'incomplete')
        self.assertTrue(any(f['code']=='unknown_passage_id' for f in result['review_findings']))

    def test_exact_duplicates_are_preserved_and_block_without_merging(self):
        row=offer([citation(self.target.pages[0])])
        result=self.run_offers(lambda _:{'offers':[row,copy.deepcopy(row)]})
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(len(result['offers']),2)
        self.assertEqual(result['offers'][1]['derived']['duplicate_of'],result['offers'][0]['id'])

    def test_independent_offers_may_share_a_quotation(self):
        refs=[citation(self.target.pages[0])]
        result=self.run_offers(lambda _:{'offers':[offer(refs),offer(refs,name='Zusatzmonitor',description='Weiterer Monitor.')]})
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(len(result['offers']),2)
        self.assertNotEqual(result['offers'][0]['id'],result['offers'][1]['id'])

    def test_no_application_keyword_filter_deletes_a_model_classification(self):
        result=self.run_offers(lambda _:{'offers':[offer([citation(self.target.pages[0])],name='Einwilligung')]})
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(result['offers'][0]['gemini']['name'],'Einwilligung')

    def test_malformed_responses_and_citations_are_preserved_and_blocked(self):
        rows=[None,{},offer([]),offer([{'evidence_id':'S1'}]),offer([{'pdf_page':True,'quote':'x'}]),
              offer([{'pdf_page':1,'quote':''}]),offer([{'pdf_page':1,'quote':'x','source_key':'context'}])]
        responses=[{}, {'offers':None},{'offers':[],'extra':'unexpected'}]+[{'offers':[r]} for r in rows]
        for raw in responses:
            with self.subTest(raw=raw):
                result=self.run_offers(lambda _:copy.deepcopy(raw))
                self.assertEqual(result['status'],'incomplete')
                self.assertEqual(result['gemini_responses'][-1]['gemini'],raw)

    def test_invalid_complete_response_replays_offline_without_new_calls(self):
        docs=[self.target,doc('D2','local',[(2,'Eine Kamera ist gesondert beauftragbar.')])]
        prior=baseline(docs); calls=[]
        with TemporaryDirectory() as directory:
            root=Path(directory)
            def provider(prompt):
                calls.append(prompt)
                ref={'passage':'P1-99'} if len(calls)==1 else citation(wire(prompt)['source_pages'][0])
                return {'offers':[offer([ref])]}
            model=TracingModel(RawRunRecorder(CachedModel(SimpleNamespace(extract=provider),root/'cache',{}),
                                             root/'raw.json'),root/'trace')
            first=extract_offers(prior,docs,model,context_documents=[self.context])
            saved={p:p.read_bytes() for p in (root/'cache').glob('*.json')}
            offline=BudgetedModel(lambda:self.fail('No provider construction'),CallBudget(0))
            replay=extract_offers(prior,docs,CachedModel(offline,root/'cache',{}),context_documents=[self.context])
            self.assertEqual(first,replay)
            self.assertEqual(first['status'],'incomplete')
            self.assertEqual(len(calls),2)
            self.assertEqual(saved,{p:p.read_bytes() for p in saved})
            self.assertEqual(json.loads((root/'raw.json').read_text()),
                             [r['gemini'] for r in first['gemini_responses'] if r['pass']=='PAGE_OFFERS'])

    def test_completed_windows_recover_after_provider_interruption(self):
        docs=[doc('D1','local',[(n,f'Zusatzpaket {n} kann gesondert beauftragt werden.') for n in (1,2,3)])]
        prior=baseline(docs,2);calls=[]
        with TemporaryDirectory() as directory:
            def interrupted(prompt):
                calls.append(prompt)
                if len(calls)==2:raise KeyboardInterrupt()
                return {'offers':[offer([citation(wire(prompt)['source_pages'][0])])]}
            with self.assertRaises(KeyboardInterrupt):
                extract_offers(prior,docs,CachedModel(SimpleNamespace(extract=interrupted),directory,{}),chunk_pages=2)
            def finish(prompt):
                calls.append(prompt)
                return {'offers':[offer([citation(wire(prompt)['source_pages'][0])],name='Zweites Paket')]}
            result=extract_offers(prior,docs,CachedModel(SimpleNamespace(extract=finish),directory,{}),chunk_pages=2)
            self.assertEqual(len(calls),3)
            self.assertEqual(result['status'],'recorded')
            self.assertEqual(len(result['offers']),2)

    def test_insufficient_budget_blocks_the_entire_known_plan_before_any_call(self):
        from bauvo_working_units.call_budget import CallLimitExceeded
        docs=[doc('D1','local',[(n,f'Zusatzpaket {n}.') for n in (1,2,3)])]
        prior=baseline(docs,2)
        with TemporaryDirectory() as directory:
            root=Path(directory)
            budget=CallBudget(1)
            backend=BudgetedModel(lambda:self.fail('No provider may be constructed'),budget)
            model=CachedModel(backend,root/'cache',{},budget=budget,plan_path=root/'plan.json')
            with self.assertRaises(CallLimitExceeded):
                extract_offers(prior,docs,model,chunk_pages=2)
            self.assertEqual(budget.used,0)
            self.assertEqual(json.loads((root/'plan.json').read_text())[0]['planned_new_calls'],2)

    def test_identical_requests_reuse_raw_answer_but_bind_to_the_current_source(self):
        docs=[self.target,doc('D2','other',[(1,self.target.pages[0]['text'])])]
        prior=baseline(docs);calls=[]
        def provider(prompt):
            calls.append(prompt)
            return {'offers':[offer([citation(wire(prompt)['source_pages'][0])])]}
        with TemporaryDirectory() as directory:
            result=extract_offers(prior,docs,CachedModel(SimpleNamespace(extract=provider),directory,{}))
        self.assertEqual(len(calls),1)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(len(result['offers']),2)
        self.assertEqual([o['derived']['citations'][0]['source_key'] for o in result['offers']],['local','other'])
        self.assertNotEqual(result['offers'][0]['derived']['citations'][0]['evidence_id'],
                            result['offers'][1]['derived']['citations'][0]['evidence_id'])

    def test_older_offer_protocol_cannot_silently_enter_timing(self):
        result=self.run_offers(lambda _:{'offers':[]}); result.pop('offers_protocol')
        with self.assertRaisesRegex(ValueError,'older extraction contract'):
            extract_timing(result,[self.target],SimpleNamespace(extract=lambda _:self.fail('No call')))

    def test_schema_is_the_approved_five_fields_with_page_quote_citations(self):
        row=RESPONSE_SCHEMAS['PAGE_OFFERS']['properties']['offers']['items']
        self.assertEqual(set(row['required']),{'name','provider','description','terms','citations'})
        self.assertFalse(row['additionalProperties'])
        self.assertEqual(set(row['properties']['citations']['items']['required']),{'passage'})

    def test_generic_controls_keep_expectations_out_of_requests(self):
        cases=json.loads((Path(__file__).resolve().parents[1]/'evaluation/offer-cases.json').read_text())['cases']
        for case in cases:
            with self.subTest(case=case['id']):
                target=doc('D1','local',[(1,case['primary_text'])])
                seen=[]
                def provider(prompt):
                    data=wire(prompt);seen.append(data)
                    self.assertNotIn(case['id'],prompt)
                    self.assertEqual(data['source_pages'],[{'pdf_page':1,'passages':[{'passage':'P1-1','text':case['primary_text']}]}])
                    self.assertFalse(set(data)&{'expected_offers','forbidden_offers','expectations','case'})
                    # Authored expected answers exercise transport, never provider accuracy.
                    return {'offers':[{**row,'citations':[citation(data['source_pages'][0])]}
                                       for row in case['expected_offers']]}
                result=self.run_offers(provider,documents=[target],context=[])
                self.assertEqual(result['status'],'recorded')
                self.assertEqual(len(seen),1)
                self.assertEqual([o['gemini']['name'] for o in result['offers']],
                                 [o['name'] for o in case['expected_offers']])
                self.assertEqual([{key:value for key,value in o['gemini'].items() if key!='citations'}
                                  for o in result['offers']],case['expected_offers'])

    def test_cli_preserves_invalid_raw_output_and_exits_before_handoff(self):
        from unittest.mock import patch
        from bauvo_working_units.phases.offers.stage import main
        docs=[self.target,doc('D2','local',[(2,'Eine Kamera ist gesondert beauftragbar.')])]
        prior=baseline(docs);calls=[]
        def provider(prompt):
            calls.append(prompt)
            ref={'passage':'P1-99'} if len(calls)==1 else citation(wire(prompt)['source_pages'][0])
            return {'offers':[offer([ref])]}
        with TemporaryDirectory() as directory:
            root=Path(directory);saved=root/'accepted-tasks.json';saved.write_text(json.dumps(prior))
            before=saved.read_bytes();output=root/'result.json'
            args=['offers','--working-units',str(saved),'--pages','pages','--documents','documents',
                  '--output',str(output),'--project','offline-test','--max-new-calls','2']
            with patch('sys.argv',args),patch('bauvo_working_units.phases.offers.stage.load_documents',return_value=docs),\
                 patch('bauvo_working_units.gemini.GeminiModel',return_value=SimpleNamespace(extract=provider)):
                with self.assertRaisesRegex(SystemExit,'downstream stages blocked'):main()
            result=json.loads(output.read_text())
            self.assertEqual(result['status'],'incomplete')
            self.assertEqual(len(calls),2)
            self.assertEqual([o['derived']['offer_validation'] for o in result['offers']],['invalid','valid'])
            self.assertEqual(saved.read_bytes(),before)
            self.assertEqual(json.loads((root/'result.gemini-raw.json').read_text()),
                             [r['gemini'] for r in result['gemini_responses'] if r['pass']=='PAGE_OFFERS'])
            self.assertEqual(json.loads((root/'result.offers-only.json').read_text()),{'offers':[o['gemini'] for o in result['offers']]})
            self.assertEqual((root/'result.trace/0001-prompt.txt').read_text(),calls[0])
            self.assertEqual((root/'result.trace/0002-prompt.txt').read_text(),calls[1])
