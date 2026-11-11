"""Generic AND/OR proposals, evidence boundaries and immutable full-context handoffs."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_manual_applicability_review as pilot

review=pilot.review
alternatives=review.alternatives
CASES=json.loads((Path(__file__).parents[1]/'evaluation/alternatives/cases.json').read_text())['cases']


def response(case, independent=False):
    key='assessments' if independent else 'reviews'
    value=(pilot.assessment_for if independent else pilot.result_for)(case['target_unit_ids'],case['sources'][0]['source_key'])
    page=case['sources'][0]['pages'][0]
    citation={'source_key':case['sources'][0]['source_key'],'document_id':'D1','pdf_page':page['pdf_page'],
              'quote':page['text'],'relation':'SUPPORTS'}
    for row in value[key]:
        for finding in row['findings'].values():finding['evidence']=[copy.deepcopy(citation)]
    value['alternative_groups']=copy.deepcopy(case['expected_groups'])
    return value


class AlternativeValidationTests(unittest.TestCase):
    def validate(self,case,value):
        return review.validate_response(value,case['target_unit_ids'],case['sources'],catalog=case['task_catalog'])

    def test_generic_any_of_and_all_of_controls_accept_source_bound_unknown_choices(self):
        self.assertEqual({case['id'] for case in CASES},{'simple-any-of','all-of-within-any-of',
                         'route-specific-deadlines','waived-duty-with-unknown-alternative'})
        for case in CASES:
            with self.subTest(case=case['id']):
                value=response(case);original=copy.deepcopy(value)
                self.assertEqual(self.validate(case,value),[])
                group=value['alternative_groups'][0]
                self.assertEqual(group['operator'],'ANY_OF')
                self.assertTrue(all(route['operator']=='ALL_OF' for route in group['routes']))
                self.assertEqual(group['selection'],{'status':'NOT_ESTABLISHED','route_id':None,'evidence':[]})
                self.assertEqual(value,original)
        grouped=response(CASES[1])['alternative_groups'][0]
        self.assertEqual([len(route['actions']) for route in grouped['routes']],[2,1])

    def test_wrong_references_structure_and_unsupported_choice_evidence_remain_findings(self):
        case=CASES[1]
        mutations={
            'unknown-unit':lambda g:g['routes'][0]['actions'][0].update(unit_id='unknown'),
            'unconditional-action':lambda g:g['routes'][1]['actions'][0].update(applicability='APPLIES'),
            'exclusive-choice':lambda g:g.update(operator='EXACTLY_ONE'),
            'one-route':lambda g:g['routes'].pop(),
            'empty-route':lambda g:g['routes'][0].update(actions=[]),
            'duplicate-route':lambda g:g['routes'][1].update(id='A'),
            'duplicate-action':lambda g:g['routes'][1]['actions'][0].update(id='A1'),
            'duplicate-unit':lambda g:g['routes'][0]['actions'][1].update(unit_id='ASSIGN'),
            'no-group-evidence':lambda g:g.update(evidence=[]),
            'context-only':lambda g:g['evidence'][0].update(relation='CONTEXT'),
            'bad-quote':lambda g:g['routes'][0]['evidence'][0].update(quote='fabricated'),
            'wrong-source':lambda g:g['routes'][0]['actions'][0]['evidence'][0].update(source_key='another'),
            'wrong-document':lambda g:g['routes'][1]['evidence'][0].update(document_id='D2'),
            'wrong-page':lambda g:g['routes'][0]['timing'].update(answer='Tomorrow',evidence=[{
                **g['evidence'][0],'pdf_page':99}]),
            'missing-route-timing':lambda g:g['routes'][1].update(timing={'answer':'Tomorrow','evidence':[]}),
            'no-condition':lambda g:g['routes'][0]['actions'][0].update(condition=''),
            'out-of-batch':lambda g:g.update(target_unit_ids=['UNREVIEWED']),
            'guessed-selection':lambda g:g['selection'].update(route_id='A'),
            'selected-unknown-route':lambda g:g['selection'].update(status='DOCUMENTED',route_id='unknown',evidence=g['evidence']),
            'selection-without-evidence':lambda g:g['selection'].update(status='DOCUMENTED',route_id='A'),
        }
        for name,mutate in mutations.items():
            with self.subTest(name=name):
                value=response(case);mutate(value['alternative_groups'][0]);original=copy.deepcopy(value)
                self.assertTrue(self.validate(case,value));self.assertEqual(value,original)
        value=response(case);value['alternative_groups'].append(copy.deepcopy(value['alternative_groups'][0]))
        self.assertTrue(self.validate(case,value))

    def test_unresolved_task_locator_stays_visible_without_fabricating_a_new_binding(self):
        case=copy.deepcopy(CASES[1]);case['task_catalog'][1]['locator_status']='UNRESOLVED'
        self.assertTrue(any('unresolved source locators' in issue for issue in self.validate(case,response(case))))

    def test_documented_selection_needs_a_known_route_and_its_own_source_evidence(self):
        case=copy.deepcopy(CASES[0]);text='Die Parteien wählen ausdrücklich die Schlüsselübergabe.'
        case['sources'][0]['pages'].append({'pdf_page':2,'text':text})
        case['sources'][0]['documents'][0]['pdf_pages'].append(2)
        value=response(case)
        value['alternative_groups'][0]['selection']={'status':'DOCUMENTED','route_id':'A','evidence':[{
            'source_key':'synthetic','document_id':'D1','pdf_page':2,'quote':text,'relation':'SUPPORTS'}]}
        self.assertEqual(self.validate(case,value),[])

    def test_checklist_preserves_distinct_deadlines_optional_actions_and_unknown_selection(self):
        records=[{'batch_id':'B001','alternative_groups':case['expected_groups'],'validation_findings':[]} for case in (CASES[2],CASES[3])]
        html=alternatives.render_checklist(records)
        self.assertIn('zwanzig Tage vor Montage',html);self.assertIn('zwei Tage vor Montage',html)
        self.assertIn('Neue bedingte Aufgabe vorgeschlagen',html)
        self.assertIn('Nicht aus den Unterlagen festgestellt',html)
        self.assertNotIn('type="checkbox"',html)
        self.assertEqual(records[1]['alternative_groups'][0]['routes'][1]['actions'][0]['unit_id'],None)

    def test_checklist_escapes_model_text_and_does_not_present_invalid_groups_as_usable_routes(self):
        groups=copy.deepcopy(CASES[0]['expected_groups']);groups[0]['requirement']='<script>alert(1)</script>'
        records=[{'batch_id':'<img src=x>','alternative_groups':groups,'validation_findings':[]}]
        html=alternatives.render_checklist(records)
        self.assertNotIn('<script>',html);self.assertNotIn('<img',html);self.assertIn('&lt;script&gt;',html)
        records[0]['validation_findings']=['Missing source evidence']
        html=alternatives.render_checklist(records)
        self.assertIn('Missing source evidence',html);self.assertNotIn('ALL_OF',html)


class AlternativeReviewIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=pilot.ManualReviewTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def freeze_case(self,index):
        case=copy.deepcopy(CASES[index]);f=self.fixture
        case['sources'][0]['source_key']=f.key
        def rebind(value):
            if isinstance(value,dict):
                if value.get('source_key')=='synthetic':value['source_key']=f.key
                for item in value.values():rebind(item)
            elif isinstance(value,list):
                for item in value:rebind(item)
        rebind(case)
        manifest=review.read(f.run/'pipeline.json')
        paths={key:f.run/manifest['steps'][key]['artifact'] for key in manifest['steps']}
        page_path=paths['01-ocr/contract-01'];boundary_path=paths['02-documents/contract-01']
        pages=review.read(page_path);pages.update(page_count=len(case['sources'][0]['pages']),pages=case['sources'][0]['pages'])
        boundary=review.read(boundary_path);boundary['documents']=case['sources'][0]['documents']
        boundary['pages_sha256']=review.digest(pages['pages'])
        review.write(page_path,pages);review.write(boundary_path,boundary)
        unit_path=paths['08-applicability/contract-01'];result=review.read(unit_path);template=result['working_units'][0]
        result['working_units']=[]
        for task in case['task_catalog']:
            unit=copy.deepcopy(template);unit['id']=task['unit_id']
            unit['gemini'].update(name=task['task_label'],action='ORIGINAL_ACTION_ONLY',
                citations=[{'pdf_page':1,'quote':pages['pages'][0]['text']}])
            unit['reviews'][0]['gemini'].update(applicability='DOES_NOT_APPLY',reason='ORIGINAL_REASON_ONLY')
            result['working_units'].append(unit)
        review.write(unit_path,result)
        for key,path in paths.items():manifest['steps'][key]['sha256']=review.sha(path)
        review.write(f.run/'pipeline.json',manifest)
        review.write(f.plan,{'batches':[{'unit_ids':case['target_unit_ids']}]})
        return case

    def test_full_review_links_tasks_outside_batch_and_saves_groups_and_local_checklist_separately(self):
        case=self.freeze_case(1);f=self.fixture
        originals={str(p):review.sha(p) for p in f.run.rglob('*') if p.is_file()}
        f.prepare()
        independent=response(case,True);comparison=response(case)
        provider=pilot.FakeProvider(pilot.Response(json.dumps(comparison)))
        provider.assessment_response=pilot.Response(json.dumps(independent))
        manifest=review.execute(f.output,lambda _:provider)
        sent=[json.loads(args['contents'].split(review.INPUT_DATA_MARKER,1)[1]) for kind,args in provider.calls if kind=='generate']
        self.assertEqual(len(sent),2)
        self.assertEqual({task['unit_id'] for task in sent[0]['task_catalog']},{'ASSIGN','BANK','GUARANTEE'})
        self.assertEqual([target['unit_id'] for target in sent[0]['targets']],['ASSIGN'])
        initial=json.dumps(sent[0]);self.assertNotIn('ORIGINAL_REASON_ONLY',initial);self.assertNotIn('ORIGINAL_ACTION_ONLY',initial)
        self.assertNotIn('expected_groups',initial);self.assertNotIn('forbidden_inferences',initial)
        self.assertEqual(sent[0]['task_catalog'],sent[1]['task_catalog'])
        self.assertEqual(sent[1]['independent_assessment'],independent)
        self.assertEqual(review.read(f.output/'B001/comparison/result.json'),comparison)
        groups=review.read(f.output/'alternatives.json')
        self.assertEqual(groups['records'][0]['alternative_groups'],comparison['alternative_groups'])
        self.assertEqual(groups['records'][0]['validation_findings'],[])
        self.assertEqual(groups['semantic_status'],'unreviewed_model_opinions')
        self.assertEqual(manifest['generation_requests_sent'],2)
        self.assertEqual(manifest['checklist_sha256'],review.sha(f.output/'checklist.html'))
        self.assertEqual(review.read(f.output/'summary.json')['alternative_groups_proposed'],1)
        self.assertEqual(originals,{str(p):review.sha(p) for p in f.run.rglob('*') if p.is_file()})

    def test_missing_alternative_action_is_an_explicit_conditional_proposal_without_editing_extraction(self):
        case=self.freeze_case(3);f=self.fixture;f.prepare()
        provider=pilot.FakeProvider(pilot.Response(json.dumps(response(case))))
        provider.assessment_response=pilot.Response(json.dumps(response(case,True)))
        review.execute(f.output,lambda _:provider)
        groups=review.read(f.output/'alternatives.json')['records'][0]['alternative_groups']
        guarantee=groups[0]['routes'][1]['actions'][0]
        self.assertIsNone(guarantee['unit_id']);self.assertEqual(guarantee['applicability'],'CONDITIONAL')
        originals=review.read(f.run/'08-applicability/contract-01/result.json')['working_units']
        self.assertEqual(len(originals),2)
        self.assertTrue(all(u['gemini']['action']=='ORIGINAL_ACTION_ONLY' for u in originals))
        self.assertIn('acht Wochen vor Baubeginn',(f.output/'checklist.html').read_text())
        self.assertIn('Vor Baubeginn',(f.output/'checklist.html').read_text())

    def test_unknown_references_and_bad_evidence_keep_raw_groups_and_findings(self):
        case=self.freeze_case(1);f=self.fixture;f.prepare()
        bad=response(case);bad['alternative_groups'][0]['routes'][1]['actions'][0]['unit_id']='UNKNOWN'
        bad['alternative_groups'][0]['evidence'][0]['quote']='fabricated'
        provider=pilot.FakeProvider(pilot.Response(json.dumps(bad)))
        provider.assessment_response=pilot.Response(json.dumps(response(case,True)))
        manifest=review.execute(f.output,lambda _:provider)
        self.assertEqual(manifest['status'],'recorded_with_findings')
        self.assertEqual(review.read(f.output/'B001/comparison/result.json'),bad)
        record=review.read(f.output/'alternatives.json')['records'][0]
        self.assertEqual(record['alternative_groups'],bad['alternative_groups'])
        self.assertEqual(len(record['validation_findings']),2)
        self.assertNotIn('ALL_OF',(f.output/'checklist.html').read_text())

    def test_changed_helper_dependency_blocks_old_preparation_before_provider_construction(self):
        f=self.fixture;f.prepare()
        with patch.object(review,'stage_binding',return_value='changed-helper'):
            with self.assertRaisesRegex(ValueError,'dependencies changed'):
                review.execute(f.output,lambda _:self.fail('Provider must not be constructed'))
        from cache_binding import dependencies, PROTOCOL
        self.assertEqual(set(dependencies(review.ROOT,'full_context_review')),
                         {'scripts/manual_applicability_review.py','scripts/review_alternatives.py','scripts/cache_binding.py'})
        self.assertEqual(PROTOCOL['full_context_review'],1)

    def test_malformed_unselected_tasks_stay_in_catalog_without_leaking_nested_proposals(self):
        self.freeze_case(1);f=self.fixture
        path=f.run/'08-applicability/contract-01/result.json'
        for i,proposal in enumerate((None,{'name':{'reason':'PRIOR_MUST_NOT_LEAK'},
                                         'citations':[{'pdf_page':{'reason':'PRIOR_MUST_NOT_LEAK'}}]})):
            with self.subTest(proposal=proposal):
                result=review.read(path)
                next(u for u in result['working_units'] if u['id']=='BANK')['gemini']=proposal
                review.write(path,result)
                manifest=review.read(f.run/'pipeline.json')
                manifest['steps']['08-applicability/contract-01']['sha256']=review.sha(path)
                review.write(f.run/'pipeline.json',manifest)
                output=f.root/('malformed-'+str(i))
                review.prepare(f.run,f.plan,output)
                request=review.read(output/'B001/assessment/request.json')
                payload=json.loads(request['contents'].split(review.INPUT_DATA_MARKER,1)[1])
                entry=next(t for t in payload['task_catalog'] if t['unit_id']=='BANK')
                self.assertEqual(entry['locator_status'],'UNRESOLVED');self.assertIsNone(entry['task_label'])
                self.assertNotIn('PRIOR_MUST_NOT_LEAK',request['contents'])
                self.assertEqual(next(u for u in review.read(path)['working_units'] if u['id']=='BANK')['gemini'],proposal)
