import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from bauvo_working_units.gemini import GeminiModel, IncompleteResponse
from bauvo_working_units.trace import RawRunRecorder, TracingModel, RetryingModel, ModelRequestFailed
from bauvo_working_units.page_flow import extract_page_units, select_assessment_chunk
from bauvo_working_units.project_flow import Document, make_page_chunks
from bauvo_working_units.cache import CachedModel


def adapter(generate):
    model = GeminiModel.__new__(GeminiModel)
    model._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
    model._model = 'gemini-3.5-flash'
    model._types = SimpleNamespace(GenerateContentConfig=lambda **kwargs: kwargs)
    return model


def response(text, finish='STOP'):
    return SimpleNamespace(text=text, candidates=[SimpleNamespace(finish_reason=finish)])


def document(start=1, end=9, identifier='D1'):
    return Document(identifier, 'Contract', 'synthetic.pdf', schema_version=6,
                    pages=tuple({'pdf_page': n, 'text': f'Task on page {n}.'} for n in range(start, end+1)))


class AssessmentContextTests(unittest.TestCase):
    def test_overlap_page_uses_window_with_following_context(self):
        chunks = make_page_chunks(document(), chunk_pages=5)
        selected, problem = select_assessment_chunk([{'pdf_page': 5}], chunks[0], chunks)
        self.assertEqual(selected.pages, (5,6,7,8,9))
        self.assertIsNone(problem)

    def test_all_cited_pages_must_remain_even_if_following_context_is_unavailable(self):
        chunks = make_page_chunks(document(), chunk_pages=5)
        selected, problem = select_assessment_chunk([{'pdf_page': 4}, {'pdf_page': 5}], chunks[0], chunks)
        self.assertEqual(selected, chunks[0]);self.assertIsNone(problem)

    def test_never_borrows_next_document_even_with_same_titles(self):
        chunks = make_page_chunks(document(end=5), chunk_pages=5)
        selected, problem = select_assessment_chunk([{'pdf_page': 5}], chunks[0], chunks)
        self.assertEqual(selected.pages, (1,2,3,4,5))
        selected, problem = select_assessment_chunk([{'pdf_page': 5}, {'pdf_page': 6}], chunks[0], chunks)
        self.assertEqual(selected, chunks[0]);self.assertIsNotNone(problem)

    def test_bad_anchors_keep_original_window_and_review_uncertainty(self):
        chunks = make_page_chunks(document(), chunk_pages=5)
        for citations in (None, [], [None], [{'pdf_page': True}], [{'pdf_page': 999}], [{'pdf_page': 1}, {'pdf_page': 9}]):
            with self.subTest(citations=citations):
                selected, problem = select_assessment_chunk(citations, chunks[0], chunks)
                self.assertEqual(selected, chunks[0]);self.assertIsNotNone(problem)

    def test_budget_limited_windows_remain_bounded_without_truncating_text(self):
        doc = document();chunks = make_page_chunks(doc, whole_document_max_chars=30)
        selected, _ = select_assessment_chunk([{'pdf_page': 2}], chunks[0], chunks)
        self.assertIn(2, selected.pages);self.assertLessEqual(len(selected.pages), 5)
        self.assertTrue(all(p in range(1,10) for p in selected.pages))

    def test_timing_and_consequences_use_following_context_but_applicability_keeps_extraction_window(self):
        doc = document(end=7);before = copy.deepcopy(doc.pages);calls = []
        def extract(prompt):
            data = json.loads(prompt.split('SOURCE DATA:\n')[1]);kind = prompt.split('.',1)[0];calls.append((kind,data))
            if kind == 'PAGE_OFFERS':return {'offers': []}
            if kind == 'PAGE_EXTRACTION':
                units = [{'action': 'Approve plan', 'citations': [{'pdf_page': 5, 'quote': 'Task on page 5.'}]}] if 5 in data['primary_pages'] else []
                return {'working_units': units, 'offers': [], 'carry_forward': ''}
            self.assertEqual([p['pdf_page'] for p in data['source_pages']], [1,2,3,4,5] if kind=='PAGE_APPLICABILITY' else [5,6,7])
            return {'assessments': [{'unit_id': u['id'], 'status': 'NOT_STATED', 'citations': []} for u in data['working_units']]}
        result = extract_page_units([doc], SimpleNamespace(extract=extract), chunk_pages=5)
        self.assertEqual([d['primary_pages'] for k,d in calls if k=='PAGE_EXTRACTION'], [[1,2,3,4,5],[6,7]])
        self.assertEqual([k for k,_ in calls if k not in ('PAGE_EXTRACTION','PAGE_OFFERS')], ['PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'])
        self.assertEqual(result['working_units'][0]['derived']['window_pages'], [1,2,3,4,5])
        self.assertEqual(result['working_units'][0]['derived']['assessment_window_pages'], [5,6,7])
        self.assertEqual(doc.pages, before)


class RetryEvidenceTests(unittest.TestCase):
    def test_one_identical_retry_preserves_failed_raw_finish_reason_and_success(self):
        calls = []
        def generate(**kwargs):
            calls.append(kwargs)
            return response('{"assessments": [', 'MAX_TOKENS') if len(calls)==1 else response('{"assessments": []}')
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            traced = RetryingModel(TracingModel(RawRunRecorder(adapter(generate), folder/'raw.json'), folder/'trace'))
            self.assertEqual(traced.extract('PAGE_TIMING. source'), {'assessments': []})
            self.assertEqual(len(calls), 2);self.assertEqual(calls[0], calls[1])
            raw = json.loads((folder/'raw.json').read_text())
            self.assertEqual(raw, ['{"assessments": [', {'assessments': []}])
            attempts = json.loads((folder/'raw.attempts.json').read_text())
            self.assertEqual([a['finish_reason'] for a in attempts], ['MAX_TOKENS','STOP'])
            self.assertEqual([a['raw_index'] for a in attempts], [0,1])
            self.assertEqual(attempts[0]['prompt_sha256'], attempts[1]['prompt_sha256'])
            self.assertEqual((folder/'trace/0001-response.json').read_text(), '{"assessments": [')
            self.assertEqual((folder/'trace/0001-prompt.txt').read_bytes(), (folder/'trace/0002-prompt.txt').read_bytes())

    def test_second_failure_is_preserved_and_never_retried_again(self):
        calls = []
        def generate(**kwargs):calls.append(kwargs);return response('{broken', 'MAX_TOKENS')
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp);model = RetryingModel(TracingModel(RawRunRecorder(adapter(generate), folder/'raw.json'),folder/'trace'))
            with self.assertRaises(ModelRequestFailed) as failure:model.extract('PAGE_TIMING. original')
            self.assertEqual(len(calls),2);self.assertEqual(len(failure.exception.attempts),2)
            self.assertEqual(json.loads((folder/'raw.json').read_text()), ['{broken','{broken'])
            self.assertEqual(len(list((folder/'trace').glob('*-attempt.json'))),2)

    def test_empty_and_invalid_json_are_retryable_but_interpretations_are_not(self):
        for first in (None, '{bad json'):
            calls=[]
            def generate(**kwargs):calls.append(kwargs);return response(first if len(calls)==1 else '{}')
            with tempfile.TemporaryDirectory() as tmp:
                model=RetryingModel(RawRunRecorder(adapter(generate),Path(tmp)/'raw.json'))
                self.assertEqual(model.extract('PAGE_TIMING. source'),{});self.assertEqual(len(calls),2)
        calls=[]
        def generate(**kwargs):calls.append(kwargs);return response('{"assessments": [], "questionable": true}')
        self.assertTrue(RetryingModel(adapter(generate)).extract('PAGE_TIMING. source')['questionable'])
        self.assertEqual(len(calls),1)

    def test_local_errors_are_not_retried_or_hidden(self):
        calls=[]
        def extract(prompt):calls.append(prompt);raise ValueError('Cache modified')
        with self.assertRaisesRegex(ValueError,'Cache modified'):RetryingModel(SimpleNamespace(extract=extract)).extract('original')
        self.assertEqual(calls,['original'])

    def test_transport_failure_has_no_fabricated_raw_response(self):
        from httpx import ReadTimeout
        calls=[]
        def generate(**kwargs):
            calls.append(kwargs)
            if len(calls)==1:raise ReadTimeout('network timeout')
            return response('{}')
        with tempfile.TemporaryDirectory() as tmp, patch('bauvo_working_units.trace.time.sleep'):
            path=Path(tmp)/'raw.json';model=RetryingModel(RawRunRecorder(adapter(generate),path))
            self.assertEqual(model.extract('PAGE_TIMING. original'),{})
            attempts=json.loads(path.with_suffix('.attempts.json').read_text())
            self.assertIsNone(attempts[0]['raw_index']);self.assertIsNone(attempts[0]['finish_reason'])
            self.assertEqual(attempts[1]['raw_index'],0);self.assertEqual(json.loads(path.read_text()),[{}])

    def test_incomplete_generation_never_enters_complete_response_cache(self):
        calls=[]
        def generate(**kwargs):calls.append(kwargs);return response('{broken','MAX_TOKENS') if len(calls)==1 else response('{}')
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);cached=CachedModel(adapter(generate),folder/'cache',{'protocol':'test'})
            model=RetryingModel(RawRunRecorder(cached,folder/'raw.json'))
            self.assertEqual(model.extract('PAGE_TIMING. source'),{})
            self.assertEqual(len(list((folder/'cache').glob('*.json'))),1)
            self.assertEqual(cached.extract('PAGE_TIMING. source'),{});self.assertEqual(len(calls),2)
            self.assertEqual(cached.last_finish_reason,'STOP')

    def test_unresolved_assessment_preserves_partial_output_and_other_passes(self):
        calls=[]
        def generate(**kwargs):
            prompt=kwargs['contents'];kind=prompt.split('.',1)[0];calls.append(kind)
            data=json.loads(prompt.split('SOURCE DATA:\n')[1])
            if kind=='PAGE_EXTRACTION':return response(json.dumps({'working_units':[{'action':'Do task','citations':[{'pdf_page':1,'quote':'Task on page 1.'}]}],'offers':[],'carry_forward':''}))
            if kind=='PAGE_OFFERS':return response('{"offers": []}')
            if kind=='PAGE_APPLICABILITY':return response('{broken','MAX_TOKENS')
            return response(json.dumps({'assessments':[{'unit_id':u['id'],'status':'NOT_STATED','citations':[]} for u in data['working_units']]}))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'raw.json';model=RetryingModel(RawRunRecorder(adapter(generate),path))
            result=extract_page_units([document(end=1)],model)
            self.assertEqual(result['status'],'request_failed')
            self.assertEqual(len(result['working_units']),1);self.assertEqual(len(result['working_units'][0]['reviews']),2)
            self.assertEqual(calls,['PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY','PAGE_APPLICABILITY'])
            failure=next(f for f in result['review_findings'] if f['code']=='model_request_failed')
            self.assertEqual(failure['pass'],'PAGE_APPLICABILITY');self.assertEqual(len(failure['attempts']),2)
            self.assertEqual(len(json.loads(path.read_text())),6)

    def test_nontransient_provider_failure_has_no_retry_but_is_recorded(self):
        class Forbidden(Exception):code=403
        calls=[]
        def extract(prompt):calls.append(prompt);raise Forbidden()
        with self.assertRaises(ModelRequestFailed) as failure:
            RetryingModel(SimpleNamespace(extract=extract)).extract('PAGE_TIMING. original')
        self.assertEqual(len(calls),1);self.assertEqual(failure.exception.attempts[0]['http_code'],403)

    def test_sdk_does_not_add_hidden_retries(self):
        with patch('google.genai.Client') as client:
            GeminiModel('p','global','gemini-3.5-flash')
            options=client.call_args.kwargs['http_options']
            self.assertEqual(options.retry_options.attempts,1)
            self.assertEqual(options.timeout,180000)


class PartialBatchEvidenceTests(unittest.TestCase):
    def test_failed_later_batch_keeps_earlier_success_and_remaining_assessments(self):
        calls=[]
        def generate(**kwargs):
            prompt=kwargs['contents'];kind=prompt.split('.',1)[0]
            data=json.loads(prompt.split('SOURCE DATA:\n')[1]);calls.append((kind,prompt))
            if kind=='PAGE_EXTRACTION':
                return response(json.dumps({'working_units':[{'action':f'Task {i}',
                    'citations':[{'pdf_page':1,'quote':'Task on page 1.'}]} for i in range(6)],
                    'offers':[],'carry_forward':''}))
            if kind=='PAGE_OFFERS':return response('{"offers": []}')
            units=data['working_units']
            if kind=='PAGE_CONSEQUENCES' and len(units)==1:
                return response('{unfinished repeating repeating','MAX_TOKENS')
            rows=[]
            for unit in units:
                row={'unit_id':unit['id']}
                if kind=='PAGE_APPLICABILITY':row.update(applicability='APPLIES',reason='W\u0004nde',
                    applicability_rules=[],missing_evidence=[])
                elif kind=='PAGE_TIMING':row.update(status='NOT_STATED',timing_rules=[],missing_evidence=[])
                else:row.update(status='NOT_STATED',consequences=[],citations=[])
                rows.append(row)
            return response(json.dumps({'assessments':rows}))
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            model=RetryingModel(TracingModel(RawRunRecorder(adapter(generate),folder/'raw.json'),folder/'trace'))
            result=extract_page_units([document(end=1)],model)
            self.assertEqual(result['status'],'request_failed')
            self.assertEqual(len(result['working_units']),6)
            self.assertEqual([len(u['reviews']) for u in result['working_units']],[3,3,3,3,3,2])
            failure=[f for f in result['review_findings'] if f['code']=='model_request_failed']
            self.assertEqual(len(failure),1);self.assertEqual(len(failure[0]['attempts']),2)
            failed_calls=[p for k,p in calls if k=='PAGE_CONSEQUENCES']
            self.assertEqual(len(failed_calls),3);self.assertEqual(failed_calls[1],failed_calls[2])
            self.assertNotIn('{unfinished repeating repeating',failed_calls[2])
            self.assertEqual(len([f for f in result['review_findings'] if f['code']=='suspicious_text_character']),6)
            raw=json.loads((folder/'raw.json').read_text())
            self.assertEqual(raw[7]['assessments'][0]['reason'],'W\u0004nde')
            self.assertEqual(raw[5:7],['{unfinished repeating repeating']*2)
            self.assertEqual((folder/'trace/0006-response.json').read_text(),'{unfinished repeating repeating')
            attempts=json.loads((folder/'raw.attempts.json').read_text())
            self.assertEqual([a['finish_reason'] for a in attempts[5:7]],['MAX_TOKENS','MAX_TOKENS'])
