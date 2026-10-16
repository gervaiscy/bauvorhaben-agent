"""Exercise the API boundary without network calls or credentials."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from enrich_working_units import GeminiModel
from customer_report import propose


def chunk(text, reason=None):
    return NS(text=text, candidates=[NS(finish_reason=reason)])


def model(responses):
    result = GeminiModel.__new__(GeminiModel)
    result.model = 'test'
    result.max_output_tokens = 100
    result.thinking_budget = None
    result.types = NS(GenerateContentConfig=lambda **kw: kw)
    result.client = NS(models=NS(generate_content_stream=Mock(side_effect=responses)))
    return result


class GeminiBoundaryTests(unittest.TestCase):
    def test_explicit_json_schema_is_forwarded_to_api(self):
        m=model([iter([chunk('{}','STOP')])])
        m.response_json_schema={'type':'object','properties':{'ok':{'type':'boolean'}}}
        m.extract('input')
        config=m.client.models.generate_content_stream.call_args.kwargs['config']
        self.assertEqual(config['response_json_schema'],m.response_json_schema)

    def test_scope_schema_stays_small_without_mutating_timing_schema(self):
        from report_schema import patch_schema
        scope=patch_schema(['a','b'])
        updates=scope['properties']['unit_updates']
        self.assertNotIn('maxItems',updates)
        self.assertNotIn('enum',updates['items']['properties']['id'])
        self.assertEqual(scope['properties']['rules']['maxItems'],0)
        self.assertNotIn('requirements',updates['items']['properties']['changes']['properties'])
        timing=patch_schema()
        self.assertIn('requirements',timing['properties']['unit_updates']['items']['properties']['changes']['properties'])
        self.assertNotIn('maxItems',timing['properties']['unit_updates'])

    def test_complete_stream_is_parsed(self):
        m = model([iter([chunk('{"ok":'), chunk('true}', 'STOP')])])
        self.assertEqual(m.extract('input'), {'ok': True})

    def test_truncated_valid_json_is_rejected_and_preserved(self):
        m = model([iter([chunk('{}', 'MAX_TOKENS')])])
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'proposal.json'
            with self.assertRaisesRegex(ValueError, 'MAX_TOKENS'):
                from test_customer_report import fixture
                propose(fixture()[1], m, target)
            self.assertFalse(target.exists())
            self.assertEqual(target.with_suffix('.partial.txt').read_text(), '{}')

    def test_missing_finish_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'missing finish'):
            model([iter([chunk('{}')])]).extract('input')

    @patch('enrich_working_units.time.sleep')
    def test_retry_only_before_response(self, sleep):
        failure = RuntimeError('temporary'); failure.code = 503
        m = model([failure, iter([chunk('{}', 'STOP')])])
        self.assertEqual(m.extract('input'), {})
        self.assertEqual(m.client.models.generate_content_stream.call_count, 2)
        sleep.assert_called_once_with(5)

    @patch('enrich_working_units.time.sleep')
    def test_partial_rate_limit_does_not_restart_large_request(self, sleep):
        failure = RuntimeError('rate limited'); failure.code = 429
        def failed():
            yield chunk('partial text')
            raise failure
        m = model([failed()])
        with self.assertRaises(RuntimeError) as caught:
            m.extract('input')
        self.assertEqual(caught.exception.partial_response, 'partial text')
        self.assertEqual(m.client.models.generate_content_stream.call_count, 1)
        sleep.assert_not_called()

    def test_interrupted_stream_retains_partial_for_diagnostics(self):
        def interrupted():
            yield chunk('{"unfinished":')
            raise TimeoutError('stream interrupted')
        with self.assertRaises(TimeoutError) as caught:
            model([interrupted()]).extract('input')
        self.assertEqual(caught.exception.partial_response, '{"unfinished":')

    def test_nonretryable_failure_is_not_retried(self):
        m = model([TimeoutError('inactive stream')])
        with self.assertRaises(TimeoutError):
            m.extract('input')
        self.assertEqual(m.client.models.generate_content_stream.call_count, 1)

if __name__ == '__main__':
    unittest.main()
