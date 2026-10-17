import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import benchmark_gemini_transcription as trial

CONFIG={'model':trial.MODEL,'project':'test','location':'global'}
def response(reason='STOP',text='35 €/m²'):
    r=Mock();r.model_dump.return_value={'candidates':[{'finish_reason':reason,'content':{'parts':[{'text':'hidden reasoning','thought':True},{'text':text}]}}],'usage_metadata':{'prompt_token_count':100,'candidates_token_count':20}}
    return r

class TranscriptionTrialTests(unittest.TestCase):
    def test_request_sends_only_constant_prompt_and_png_without_expected_answers(self):
        client=Mock();trial.generate(client,trial.MODEL,b'png')
        kw=client.models.generate_content.call_args.kwargs
        self.assertEqual(len(kw['contents']),2);self.assertEqual(kw['contents'][0].text,trial.PROMPT)
        self.assertEqual(kw['contents'][1].inline_data.data,b'png')
        self.assertEqual(kw['contents'][1].inline_data.mime_type,'image/png')
        self.assertEqual(kw['config'].thinking_config.thinking_budget,0)
        self.assertEqual(kw['config'].response_mime_type,'text/plain')
    def test_exact_cache_reuse_tampering_and_image_binding(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);image=out/'page.png';image.write_bytes(b'png')
            with patch.object(trial,'generate',return_value=response()) as call:
                a=trial.cached_transcription(None,CONFIG,image,out);self.assertEqual(a['text'],'35 €/m²')
                trial.cached_transcription(None,CONFIG,image,out);self.assertEqual(call.call_count,1)
                image.write_bytes(b'new png');trial.cached_transcription(None,CONFIG,image,out);self.assertEqual(call.call_count,2)
            path=next((out/'responses').glob('*.json'));saved=json.loads(path.read_text());saved['response']['text']='changed';path.write_text(json.dumps(saved))
            with self.assertRaises(ValueError):
                # Restore whichever image is bound to the modified cache.
                for pixels in [b'png',b'new png']:
                    image.write_bytes(pixels)
                    if trial.bench.sha(image)==saved['request']['image_sha256']:trial.cached_transcription(None,CONFIG,image,out)
    def test_failed_calls_keep_reservation_across_restart(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);image=out/'page.png';image.write_bytes(b'png')
            with patch.object(trial,'generate',side_effect=RuntimeError('unavailable')):
                with self.assertRaises(RuntimeError):trial.cached_transcription(None,CONFIG,image,out,max_calls=1)
            with patch.object(trial,'generate') as call:
                with self.assertRaisesRegex(RuntimeError,'limit'):trial.cached_transcription(None,CONFIG,image,out,max_calls=1)
                call.assert_not_called()
            self.assertEqual(json.loads((out/'request-ledger.json').read_text())[0]['status'],'failed')
    def test_budget_blocks_before_request_and_incomplete_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);image=out/'page.png';image.write_bytes(b'png')
            with patch.object(trial,'generate',return_value=response('MAX_TOKENS','partial')) as call:
                with self.assertRaisesRegex(RuntimeError,'limit'):trial.cached_transcription(None,CONFIG,image,out,budget_usd=.01)
                call.assert_not_called();r=trial.cached_transcription(None,CONFIG,image,out)
            self.assertFalse(r['usable']);self.assertEqual(r['text'],'partial');self.assertIn('raw_response',r)
