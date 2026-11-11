"""Offline tests for frozen input identity, request bounds and provider evidence preservation."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location('manual_review', Path(__file__).parents[1]/'scripts/manual_applicability_review.py')
if not spec.origin or not Path(spec.origin).is_file():
    spec = importlib.util.spec_from_file_location('manual_review', Path(__file__).with_name('manual_applicability_review.py'))
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


class Response:
    def __init__(self, text=None, finish='STOP', total_tokens=123):
        self.text = text
        self.candidates = [SimpleNamespace(finish_reason=finish)]
        self.usage_metadata = None
        self.total_tokens = total_tokens

    def model_dump_json(self, **kwargs):
        return json.dumps({'text': self.text, 'finish_reason': self.candidates[0].finish_reason,
                           'total_tokens': self.total_tokens})


def result_for(ids, source_key):
    citation = {'source_key': source_key, 'document_id': 'D1', 'pdf_page': 1,
                'quote': 'Der Bauherr stellt Bauwasser bereit.', 'relation': 'SUPPORTS'}
    finding = {'source_supported_answer': 'Bauwasser bereitstellen.', 'verdict': 'CORRECT',
               'explanation': 'Im Quelltext belegt.', 'evidence': [citation], 'proposed_correction': None}
    return {'reviews': [{'unit_id': unit_id, 'findings': {name: copy.deepcopy(finding) for name in review.DIMENSIONS},
                         'uncertainties': []} for unit_id in ids], 'alternative_groups':[]}


def assessment_for(ids, source_key):
    comparison = result_for(ids, source_key)
    return {'assessments': [{'unit_id': row['unit_id'], 'findings': {
        name: {'source_supported_answer': 'Independent source answer',
               'evidence': finding['evidence'], 'uncertainties': []}
        for name, finding in row['findings'].items()}} for row in comparison['reviews']], 'alternative_groups':[]}


class FakeProvider:
    def __init__(self, response, tokens=123):
        self.models = self
        self.response = response
        self.tokens = tokens
        self.calls = []
        self.closed = False
        self.assessment_response = None
        self.comparison_tokens = tokens

    def count_tokens(self, **kwargs):
        self.calls.append(('count', kwargs))
        payload = json.loads(kwargs['contents'].split(review.INPUT_DATA_MARKER, 1)[1])
        return Response(total_tokens=self.tokens if 'targets' in payload else self.comparison_tokens)

    def generate_content(self, **kwargs):
        self.calls.append(('generate', kwargs))
        payload = json.loads(kwargs['contents'].split(review.INPUT_DATA_MARKER, 1)[1])
        if 'targets' in payload:
            ids = [target['unit_id'] for target in payload['targets']]
            return self.assessment_response or Response(json.dumps(assessment_for(ids, payload['sources'][0]['source_key'])))
        return self.response

    def close(self):
        self.closed = True


class ManualReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run = self.root/'pipeline'
        self.run.mkdir()
        pdf = self.root/'source.pdf'
        pdf.write_bytes(b'fixture PDF fingerprint; no PDF parser needed for frozen text')
        self.source = {'id': 'contract-01', 'type': 'project', 'pdf': str(pdf), 'sha256': review.sha(pdf)}
        pages = {'schema_version': 6, 'source': str(pdf), 'sha256': review.sha(pdf), 'page_count': 1,
                 'pages': [{'pdf_page': 1, 'text': 'Der Bauherr stellt Bauwasser bereit.',
                            'checkboxes': [{'checked': False}], 'transcription_findings': ['OCR_UNCERTAIN']}]}
        self.key = review.digest({'source': str(pdf), 'pdf_sha256': review.sha(pdf)})[:16]
        unit = {'id': 'U1', 'gemini': {'name': 'Bauwasser bereitstellen', 'action': 'Bauwasser bereitstellen',
                'citations': [{'pdf_page': 1, 'quote': 'Der Bauherr stellt Bauwasser bereit.'}]},
                'derived': {'source_key': self.key, 'document_id': 'D1'},
                'reviews': [{'kind': 'PAGE_APPLICABILITY', 'gemini': {'applicability': 'APPLIES'}}]}
        manifest = {'binding': {'sources': [self.source], 'project': 'fixture-project', 'location': 'global', 'model': 'test-model'},
                    'steps': {}, 'status': 'needs_review'}

        def stage(key, value):
            folder = self.run/key
            folder.mkdir(parents=True)
            path = folder/'result.json'
            review.write(path, value)
            manifest['steps'][key] = {'status': 'complete', 'artifact': str(path.relative_to(self.run)), 'sha256': review.sha(path)}
            return path

        page_file = stage('01-ocr/contract-01', pages)
        boundaries = {'schema_version': 6,
                      'documents': [{'document_id': 'D1', 'document_title': 'Fixture', 'pdf_pages': [1], 'uncertain': True}],
                      'status': 'needs_review', 'review_flags': ['BOUNDARY_UNCERTAIN'], 'pages_sha256': review.digest(pages['pages'])}
        stage('02-documents/contract-01', boundaries)
        stage('08-applicability/contract-01', {'status': 'needs_review', 'working_units': [unit], 'review_findings': ['KEEP_ME']})
        review.write(self.run/'pipeline.json', manifest)
        self.plan = self.root/'plan.json'
        review.write(self.plan, {'batches': [{'unit_ids': ['U1'], 'selection_note': 'EXPECTED_VERDICT_MUST_NOT_LEAK'}]})
        self.output = self.root/'review'

    def prepare(self):
        return review.prepare(self.run, self.plan, self.output)

    def update_unit(self, update):
        path = self.run/'08-applicability/contract-01/result.json'
        data = review.read(path)
        update(data['working_units'][0])
        review.write(path, data)
        manifest = review.read(self.run/'pipeline.json')
        manifest['steps']['08-applicability/contract-01']['sha256'] = review.sha(path)
        review.write(self.run/'pipeline.json', manifest)

    def test_independent_input_never_receives_prior_answers_quotes_or_metadata(self):
        def contaminate(unit):
            unit['gemini'].update(action='PRIOR_ACTION_ONLY', actor='PRIOR_ACTOR_ONLY',
                                  performed_by='PRIOR_EXECUTOR_ONLY')
            unit['gemini']['citations'][0]['quote'] = 'PRIOR_QUOTE_ONLY'
            unit['derived']['prior_context'] = 'PRIOR_METADATA_ONLY'
            unit['reviews'][0]['gemini']['reason'] = 'PRIOR_APPLICABILITY_ONLY'
            unit['reviews'].extend([
                {'kind': 'PAGE_TIMING', 'gemini': {'answer': 'PRIOR_DEADLINE_ONLY'}},
                {'kind': 'PAGE_CONSEQUENCES', 'gemini': {'consequences': ['PRIOR_CONSEQUENCE_ONLY']}}])
        self.update_unit(contaminate)
        self.prepare()
        folder = self.output/'B001/assessment'
        request = review.read(folder/'request.json')
        for marker in ['PRIOR_ACTION_ONLY', 'PRIOR_ACTOR_ONLY', 'PRIOR_EXECUTOR_ONLY', 'PRIOR_QUOTE_ONLY',
                       'PRIOR_METADATA_ONLY', 'PRIOR_APPLICABILITY_ONLY', 'PRIOR_DEADLINE_ONLY',
                       'PRIOR_CONSEQUENCE_ONLY', 'KEEP_ME', 'EXPECTED_VERDICT_MUST_NOT_LEAK']:
            self.assertNotIn(marker, request['contents'])
        payload = json.loads(request['contents'].split(review.INPUT_DATA_MARKER, 1)[1])
        self.assertEqual(set(payload), {'sources', 'targets', 'task_catalog'})
        self.assertEqual(payload['targets'], [{'unit_id': 'U1', 'task_label': 'Bauwasser bereitstellen',
            'source_key': self.key, 'document_id': 'D1', 'pdf_pages': [1]}])
        self.assertNotIn('verdict', json.dumps(request['config']['response_json_schema']))
        self.assertFalse((self.output/'B001/comparison').exists())

    def test_evidence_reference_tasks_use_resolved_locators_without_leaking_proposals(self):
        def select_reference(unit):
            unit['gemini']['citations']=[{'evidence_id':'E-source-span'}]
            unit['derived']['citations']=[{'evidence_id':'E-source-span','source_key':self.key,
                'document_id':'D1','pdf_page':1,'quote':'RESOLVED_QUOTE_MUST_NOT_LEAK'}]
        self.update_unit(select_reference)
        self.prepare()
        request=review.read(self.output/'B001/assessment/request.json')
        payload=json.loads(request['contents'].split(review.INPUT_DATA_MARKER,1)[1])
        self.assertEqual(payload['targets'][0]['pdf_pages'],[1])
        self.assertNotIn('RESOLVED_QUOTE_MUST_NOT_LEAK',request['contents'])
        self.assertNotIn('E-source-span',request['contents'])

    def test_both_sent_requests_use_german_prompts_with_the_same_four_questions(self):
        self.prepare()
        provider = FakeProvider(Response(json.dumps(result_for(['U1'], self.key))))
        review.execute(self.output, lambda _: provider)
        requests = [args for kind, args in provider.calls if kind == 'generate']
        self.assertEqual(len(requests), 2)
        for request, prompt in zip(requests, [review.ASSESSMENT_PROMPT, review.COMPARISON_PROMPT]):
            instructions, payload = request['contents'].split(review.INPUT_DATA_MARKER, 1)
            self.assertEqual(instructions, prompt)
            for section in ['Rolle:', 'Kontext:', 'Frage:', 'Erwartetes Ergebnis:']:
                self.assertIn(section, instructions)
            for number, question in enumerate(review.QUESTIONS.values(), 1):
                self.assertIn(f'{number}. {question}', instructions)
            self.assertIn('nicht oder nicht rechtzeitig erledigt', instructions)
            self.assertIn('EINGABEDATEN (JSON):', request['contents'])
            self.assertNotIn('INPUT DATA (JSON):', request['contents'])
            self.assertIn('Verzögerungen' if prompt == review.ASSESSMENT_PROMPT else 'widersprüchlich', instructions)
            self.assertIn('sources', json.loads(payload))
            self.assertEqual(request['contents'].encode('utf-8').decode('utf-8'), request['contents'])

    def test_timing_omissions_use_full_sources_and_amendment_and_keep_corrections_separate(self):
        manifest=review.read(self.run/'pipeline.json')
        page_path=self.run/manifest['steps']['01-ocr/contract-01']['artifact']
        boundary_path=self.run/manifest['steps']['02-documents/contract-01']['artifact']
        pages=review.read(page_path)
        base='Das Bauwerk ist binnen 24 Wochen ab Beginn der vertraglichen Arbeiten des Auftragnehmers fertigzustellen.'
        qualifications='Kältebedingte Ausfalltage verlängern die Frist nur bei offenen Fassadenarbeiten. Nach Ende der Kälte kommen zwei Wiederaufnahmetage hinzu; insgesamt höchstens sechs Wochen Verlängerung.'
        amendment='Unterzeichneter Nachtrag: Für dieses Vorhaben beträgt die Obergrenze einschließlich Wiederaufnahme vier Wochen statt sechs Wochen.'
        pages['page_count']=3
        pages['pages']=[{'pdf_page':i,'text':text,'checkboxes':[]} for i,text in enumerate((base,qualifications,amendment),1)]
        boundaries=review.read(boundary_path)
        boundaries['documents'][0]['pdf_pages']=[1,2]
        boundaries['documents'].append({'document_id':'D2','document_title':'Nachtrag','pdf_pages':[3],'uncertain':False})
        boundaries['pages_sha256']=review.digest(pages['pages'])
        review.write(page_path,pages);review.write(boundary_path,boundaries)
        for key,path in (('01-ocr/contract-01',page_path),('02-documents/contract-01',boundary_path)):
            manifest['steps'][key]['sha256']=review.sha(path)
        review.write(self.run/'pipeline.json',manifest)
        def proposed(unit):
            unit['gemini'].update(name='Bauwerk fertigstellen',action='Bauwerk fertigstellen',
                                  citations=[{'pdf_page':1,'quote':base}])
            unit['reviews'].append({'kind':'PAGE_TIMING','gemini':{
                'unit_id':'U1','answer':'Innerhalb von 24 Wochen ab Baubeginn.','citations':[{'pdf_page':1,'quote':base}]}})
        self.update_unit(proposed)
        originals={str(path):review.sha(path) for path in self.run.rglob('*') if path.is_file()}
        self.prepare()
        independent=assessment_for(['U1'],self.key)
        full_answer=base+' '+qualifications+' '+amendment
        evidence=[{'source_key':self.key,'document_id':'D1' if i<3 else 'D2',
            'pdf_page':i,'quote':text,'relation':'SUPPORTS'} for i,text in enumerate((base,qualifications,amendment),1)]
        for finding in independent['assessments'][0]['findings'].values():
            finding['evidence']=copy.deepcopy(evidence[:1])
        independent['assessments'][0]['findings']['timing'].update(source_supported_answer=full_answer,evidence=evidence)
        comparison=result_for(['U1'],self.key)
        for finding in comparison['reviews'][0]['findings'].values():
            finding['evidence']=copy.deepcopy(evidence[:1])
        comparison['reviews'][0]['findings']['timing'].update(source_supported_answer=full_answer,
            verdict='MISSING',explanation='Leistungsumfang, Kältebedingung, Wiederaufnahme und geänderte Obergrenze fehlen.',
            proposed_correction=full_answer,evidence=evidence)
        provider=FakeProvider(Response(json.dumps(comparison)))
        provider.assessment_response=Response(json.dumps(independent))
        review.execute(self.output,lambda _:provider)
        sent=[json.loads(args['contents'].split(review.INPUT_DATA_MARKER,1)[1]) for kind,args in provider.calls if kind=='generate']
        self.assertEqual(len(sent),2)
        self.assertEqual([p['text'] for p in sent[0]['sources'][0]['pages']],[base,qualifications,amendment])
        self.assertEqual(sent[0]['sources'],sent[1]['sources'])
        self.assertNotIn('proposed_working_units',sent[0])
        self.assertEqual(sent[1]['independent_assessment'],independent)
        timing=review.read(self.output/'B001/comparison/result.json')['reviews'][0]['findings']['timing']
        self.assertEqual(timing['verdict'],'MISSING');self.assertEqual(timing['proposed_correction'],full_answer)
        self.assertEqual(review.read(self.output/'B001/comparison/validation.json')['findings'],[])
        self.assertEqual(originals,{str(path):review.sha(path) for path in self.run.rglob('*') if path.is_file()})

    def test_changing_proposed_answers_cannot_change_the_independent_request(self):
        self.prepare()
        first = review.read(self.output/'B001/assessment/request.json')
        def replace_answers(unit):
            unit['gemini'].update(actor='Different responsible party', performed_by='Different executor',
                                  action='An unsupported proposed action')
            unit['reviews'][0]['gemini'].update(applicability='DOES_NOT_APPLY', reason='Contradictory prior reasoning')
        self.update_unit(replace_answers)
        second_output = self.root/'different-proposals'
        review.prepare(self.run, self.plan, second_output)
        self.assertEqual(first, review.read(second_output/'B001/assessment/request.json'))
        self.assertNotEqual(review.read(self.output/'input-snapshot.json')['working_units'],
                            review.read(second_output/'input-snapshot.json')['working_units'])

    def test_comparison_is_built_from_the_saved_assessment_and_original_proposal(self):
        self.prepare()
        assessment = assessment_for(['U1'], self.key)
        original = copy.deepcopy(assessment)
        raw = json.dumps(assessment, ensure_ascii=False) + '\n'
        provider = FakeProvider(Response(json.dumps(result_for(['U1'], self.key))))
        provider.assessment_response = Response(raw)
        generate = provider.generate_content
        def inspect(**kwargs):
            self.assertEqual(set(kwargs), {'model', 'contents', 'config'})
            payload = json.loads(kwargs['contents'].split(review.INPUT_DATA_MARKER, 1)[1])
            if 'targets' in payload:
                self.assertFalse((self.output/'B001/comparison').exists())
            else:
                saved = review.read(self.output/'B001/assessment/result.json')
                self.assertEqual(payload['independent_assessment'], saved)
                self.assertEqual(saved, original)
                self.assertEqual(payload['proposed_working_units'], review.read(self.output/'input-snapshot.json')['working_units'])
            return generate(**kwargs)
        provider.generate_content = inspect
        manifest = review.execute(self.output, lambda _: provider)
        self.assertEqual((self.output/'B001/assessment/response.txt').read_text(), raw)
        self.assertEqual(review.read(self.output/'B001/assessment/result.json'), original)
        self.assertEqual(manifest['batches'][0]['comparison']['assessment_result_sha256'],
                         review.sha(self.output/'B001/assessment/result.json'))

    def test_four_question_contract_keeps_applicability_conditions_without_a_separate_conditions_field(self):
        def conditional_task(unit):
            unit['reviews'][0]['gemini'].update(applicability='CONDITIONAL',
                reason='Only when the customer selects this optional service.')
        self.update_unit(conditional_task)
        self.prepare()
        expected = {'action_and_roles', 'applicability', 'timing', 'explicit_consequences'}
        independent = assessment_for(['U1'], self.key)
        independent['assessments'][0]['findings']['applicability']['source_supported_answer'] = 'Conditional on the customer selecting the optional service.'
        comparison = result_for(['U1'], self.key)
        comparison['reviews'][0]['findings']['applicability']['source_supported_answer'] = 'Conditional on the customer selecting the optional service.'
        provider = FakeProvider(Response(json.dumps(comparison)))
        provider.assessment_response = Response(json.dumps(independent))
        review.execute(self.output, lambda _: provider)
        for stage, collection in [('assessment', 'assessments'), ('comparison', 'reviews')]:
            request = review.read(self.output/'B001'/stage/'request.json')
            fields = request['config']['response_json_schema']['properties'][collection]['items']['properties']['findings']
            self.assertEqual(set(fields['properties']), expected)
            result = review.read(self.output/'B001'/stage/'result.json')
            self.assertEqual(set(result[collection][0]['findings']), expected)
        request = review.read(self.output/'B001/comparison/request.json')
        payload = json.loads(request['contents'].split(review.INPUT_DATA_MARKER, 1)[1])
        self.assertEqual(payload['proposed_working_units'][0]['reviews'][0]['gemini']['reason'],
                         'Only when the customer selects this optional service.')
        self.assertEqual(review.read(self.output/'summary.json')['verdict_counts'], {'CORRECT': 4})

    def test_old_five_dimension_assessment_is_preserved_but_cannot_start_comparison(self):
        self.prepare()
        independent = assessment_for(['U1'], self.key)
        independent['assessments'][0]['findings']['conditions'] = copy.deepcopy(
            independent['assessments'][0]['findings']['applicability'])
        provider = FakeProvider(None)
        provider.assessment_response = Response(json.dumps(independent))
        with self.assertRaises(ValueError):
            review.execute(self.output, lambda _: provider)
        self.assertEqual(review.read(self.output/'B001/assessment/result.json'), independent)
        self.assertFalse((self.output/'B001/comparison').exists())
        self.assertEqual([name for name, _ in provider.calls], ['count', 'generate'])

    def test_incomplete_independent_response_never_sends_comparison(self):
        self.prepare()
        provider = FakeProvider(None)
        provider.assessment_response = Response('{"assessments": [', finish='MAX_TOKENS')
        with self.assertRaises(ValueError):
            review.execute(self.output, lambda _: provider)
        self.assertEqual([name for name, _ in provider.calls], ['count', 'generate'])
        self.assertEqual((self.output/'B001/assessment/response.txt').read_text(), '{"assessments": [')
        self.assertFalse((self.output/'B001/comparison').exists())

    def test_invalid_independent_schema_or_coverage_never_sends_comparison(self):
        for name, reply in [('bad-schema', {'wrong': []}),
                            ('missing-target', assessment_for([], self.key)),
                            ('wrong-target', assessment_for(['OTHER'], self.key)),
                            ('duplicate-target', assessment_for(['U1', 'U1'], self.key))]:
            with self.subTest(name=name):
                output = self.root/name
                review.prepare(self.run, self.plan, output)
                provider = FakeProvider(None)
                provider.assessment_response = Response(json.dumps(reply))
                with self.assertRaises(ValueError):
                    review.execute(output, lambda _: provider)
                self.assertEqual([kind for kind, _ in provider.calls], ['count', 'generate'])
                self.assertEqual(review.read(output/'B001/assessment/result.json'), reply)
                self.assertFalse((output/'B001/comparison').exists())

    def test_independent_citation_findings_are_preserved_and_disclosed_to_comparison(self):
        self.prepare()
        assessment = assessment_for(['U1'], self.key)
        assessment['assessments'][0]['findings']['timing']['evidence'][0]['quote'] = 'Unsupported quotation'
        provider = FakeProvider(Response(json.dumps(result_for(['U1'], self.key))))
        provider.assessment_response = Response(json.dumps(assessment))
        manifest = review.execute(self.output, lambda _: provider)
        comparison = review.read(self.output/'B001/comparison/request.json')
        payload = json.loads(comparison['contents'].split(review.INPUT_DATA_MARKER, 1)[1])
        self.assertEqual(payload['independent_assessment'], assessment)
        self.assertEqual(len(payload['independent_assessment_findings']), 1)
        self.assertEqual(manifest['batches'][0]['assessment']['validation_findings'], 1)
        self.assertEqual(manifest['status'], 'recorded_with_findings')

    def test_comparison_token_ceiling_preserves_independent_answer_without_second_generation(self):
        self.prepare()
        provider = FakeProvider(None)
        provider.comparison_tokens = 900001
        with self.assertRaises(ValueError):
            review.execute(self.output, lambda _: provider)
        self.assertEqual([kind for kind, _ in provider.calls], ['count', 'generate', 'count'])
        self.assertTrue((self.output/'B001/assessment/result.json').is_file())
        self.assertFalse((self.output/'B001/comparison/provider-response.json').exists())
        self.assertEqual(review.read(self.output/'manifest.json')['generation_requests_sent'], 1)

    def test_previous_protocols_cannot_execute_with_the_changed_question_contract(self):
        self.prepare()
        manifest = review.read(self.output/'manifest.json')
        for version in ['manual-applicability-review-v1', 'manual-applicability-review-v2', 'manual-applicability-review-v3']:
            with self.subTest(version=version):
                manifest['protocol'] = version
                review.write(self.output/'manifest.json', manifest)
                with self.assertRaises(ValueError):
                    review.execute(self.output, lambda _: self.fail('Old requests must not be sent'))

    def test_freezes_every_page_and_keeps_uncertainties_without_leaking_selection_labels(self):
        before = {str(p): review.sha(p) for p in self.run.rglob('*.json')}
        self.prepare()
        request = review.read(self.output/'B001/assessment/request.json')
        self.assertNotIn('EXPECTED_VERDICT_MUST_NOT_LEAK', request['contents'])
        self.assertIn('Der Bauherr stellt Bauwasser bereit.', request['contents'])
        self.assertIn('BOUNDARY_UNCERTAIN', request['contents'])
        self.assertIn('OCR_UNCERTAIN', request['contents'])
        snapshot = review.read(self.output/'input-snapshot.json')
        self.assertEqual(snapshot['pipeline_review_findings']['contract-01'], ['KEEP_ME'])
        self.assertEqual(before, {str(p): review.sha(p) for p in self.run.rglob('*.json')})
        with self.assertRaises(FileExistsError):
            self.prepare()

    def test_rejects_unknown_duplicate_and_oversized_batches(self):
        for ids in [['UNKNOWN'], ['U1', 'U1'], ['U1'] * 6]:
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                review.select_batches({'batches': [{'unit_ids': ids}]}, {'U1': {}})
        with self.assertRaises(ValueError):
            review.select_batches({'batches': [{'unit_ids': ['U1']}] * 4}, {'U1': {}})

    def test_missing_pages_or_boundaries_fail_before_any_output_is_created(self):
        with self.assertRaises(ValueError):
            review.build_source(self.source, {'schema_version': 6, 'page_count': 2,
                'pages': [{'pdf_page': 1, 'text': 'one'}]}, {'documents': []})
        with self.assertRaises(ValueError):
            review.prepare(self.run, self.plan, self.run/'review')

    def test_detects_changed_original_artifact_before_preparation(self):
        path = self.run/'08-applicability/contract-01/result.json'
        path.write_text('{}')
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_counts_before_generation_and_preserves_exact_request_and_raw_response(self):
        self.prepare()
        raw = json.dumps(result_for(['U1'], self.key), ensure_ascii=False) + '\n'
        provider = FakeProvider(Response(raw))
        result = review.execute(self.output, lambda _: provider)
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual([name for name, _ in provider.calls], ['count', 'generate', 'count', 'generate'])
        for index, stage in [(1, 'assessment'), (3, 'comparison')]:
            request = review.read(self.output/'B001'/stage/'request.json')
            self.assertEqual(provider.calls[index][1]['contents'], request['contents'])
            self.assertEqual(provider.calls[index][1]['config'], request['config'])
        self.assertEqual((self.output/'B001/comparison/response.txt').read_text(), raw)
        self.assertTrue((self.output/'B001/assessment/provider-response.json').is_file())
        self.assertTrue((self.output/'B001/comparison/provider-response.json').is_file())
        self.assertEqual(result['generation_requests_sent'], 2)
        self.assertTrue(provider.closed)
        self.assertEqual(review.read(self.output/'summary.json')['semantic_status'], 'unreviewed_model_opinions')
        with self.assertRaises(ValueError):
            review.execute(self.output, lambda _: self.fail('Must not call provider again'))

    def test_tampered_prepared_request_never_reaches_provider(self):
        self.prepare()
        (self.output/'B001/assessment/request.json').write_text('{}')
        with self.assertRaises(ValueError):
            review.execute(self.output, lambda _: self.fail('Provider must not be constructed'))

    def test_every_fresh_batch_gets_all_sources_without_previous_model_answers(self):
        path = self.run/'08-applicability/contract-01/result.json'
        data = review.read(path)
        second = copy.deepcopy(data['working_units'][0])
        second['id'] = 'U2'
        data['working_units'].append(second)
        review.write(path, data)
        manifest = review.read(self.run/'pipeline.json')
        manifest['steps']['08-applicability/contract-01']['sha256'] = review.sha(path)
        review.write(self.run/'pipeline.json', manifest)
        review.write(self.plan, {'batches': [{'unit_ids': ['U1']}, {'unit_ids': ['U2']}]})
        self.prepare()
        provider = FakeProvider(Response(json.dumps(result_for(['U1'], self.key))))
        def generate(**kwargs):
            provider.calls.append(('generate', kwargs))
            payload = json.loads(kwargs['contents'].split(review.INPUT_DATA_MARKER, 1)[1])
            if 'targets' in payload:
                targets = [u['unit_id'] for u in payload['targets']]
                return Response(json.dumps(assessment_for(targets, self.key)))
            targets = [u['id'] for u in payload['proposed_working_units']]
            return Response(json.dumps(result_for(targets, self.key)))
        provider.generate_content = generate
        review.execute(self.output, lambda _: provider)
        self.assertEqual([name for name, _ in provider.calls],
                         ['count', 'count', 'generate', 'count', 'generate', 'generate', 'count', 'generate'])
        payloads = [json.loads(args['contents'].split(review.INPUT_DATA_MARKER, 1)[1])
                    for name, args in provider.calls if name == 'generate']
        self.assertTrue(all(payload['sources'] == payloads[0]['sources'] for payload in payloads))
        self.assertEqual([u['unit_id'] for u in payloads[2]['targets']], ['U2'])
        self.assertEqual([u['id'] for u in payloads[3]['proposed_working_units']], ['U2'])
        self.assertNotIn('independent_assessment', payloads[2])
        self.assertEqual(review.read(self.output/'manifest.json')['generation_requests_sent'], 4)

    def test_provider_failure_is_recorded_once_without_retry_or_logging_transport_secrets(self):
        self.prepare()
        provider = FakeProvider(None)
        def fail(**kwargs):
            provider.calls.append(('generate', kwargs))
            raise RuntimeError('secret-auth-header')
        provider.generate_content = fail
        with self.assertRaises(RuntimeError):
            review.execute(self.output, lambda _: provider)
        self.assertEqual([name for name, _ in provider.calls], ['count', 'generate'])
        self.assertNotIn('secret-auth-header', (self.output/'error.json').read_text())
        self.assertEqual(review.read(self.output/'manifest.json')['status'], 'failed')

    def test_token_ceiling_stops_generation(self):
        self.prepare()
        provider = FakeProvider(Response('{}'), tokens=900001)
        with self.assertRaises(ValueError):
            review.execute(self.output, lambda _: provider)
        self.assertEqual([name for name, _ in provider.calls], ['count'])
        self.assertEqual(review.read(self.output/'manifest.json')['status'], 'failed')
        self.assertTrue(provider.closed)

    def test_incomplete_output_survives_without_becoming_a_successful_review(self):
        self.prepare()
        provider = FakeProvider(Response('{"reviews": [', finish='MAX_TOKENS'))
        with self.assertRaises(ValueError):
            review.execute(self.output, lambda _: provider)
        self.assertEqual((self.output/'B001/comparison/response.txt').read_text(), '{"reviews": [')
        self.assertEqual(review.read(self.output/'manifest.json')['status'], 'failed')
        self.assertFalse((self.output/'B001/comparison/result.json').exists())
        self.assertTrue((self.output/'B001/assessment/result.json').is_file())

    def test_unknown_targets_and_bad_citations_are_flagged_without_rewriting_answers(self):
        self.prepare()
        snapshot = review.read(self.output/'input-snapshot.json')
        reply = result_for(['OTHER'], self.key)
        reply['reviews'][0]['findings']['timing']['evidence'][0]['document_id'] = 'WRONG'
        original = copy.deepcopy(reply)
        findings = review.validate_response(reply, ['U1'], snapshot['sources'])
        self.assertEqual(len(findings), 2)
        self.assertEqual(reply, original)

    def test_malformed_schema_is_retained_and_never_reported_as_valid(self):
        self.prepare()
        provider = FakeProvider(Response('{"unrelated": []}'))
        result = review.execute(self.output, lambda _: provider)
        self.assertEqual(result['status'], 'recorded_with_findings')
        self.assertEqual(review.read(self.output/'B001/comparison/result.json'), {'unrelated': []})
        self.assertEqual(review.read(self.output/'summary.json')['verdict_counts'], {})


if __name__ == '__main__':
    unittest.main()
