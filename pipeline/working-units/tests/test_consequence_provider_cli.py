"""Both production providers use the same single-pass schema, preserve raw JSON and replay offline."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from consequence_fixtures import baseline, assessment_answer
from bauvo_working_units.phases.consequences.stage import main
from bauvo_working_units.phases.consequences.artifact import load_artifact
from bauvo_working_units.phases.timing.artifact import write_outputs as write_timing
from bauvo_working_units.cache import provider_request
from bauvo_working_units.phases.consequences.backend import provider_identity, PhaseRouter
from bauvo_working_units.schemas import response_schema_for


class ConsequenceProviderCliTests(unittest.TestCase):
    def test_each_provider_runs_one_call_per_batch_and_replays_without_provider_calls(self):
        for provider in ('sol','gemini'):
            with self.subTest(provider=provider), TemporaryDirectory() as tmp:
                root=Path(tmp); saved,docs,context=baseline((11,)); timing=root/'timing.json';write_timing(timing,saved)
                bodies=[];client=Mock()
                def answer(prompt):
                    bodies.append(prompt);data=json.loads(prompt.split('SOURCE DATA:\n')[1])
                    self.assertTrue(prompt.startswith('PAGE_CONSEQUENCES.'))
                    self.assertNotIn('task_actions',data)
                    return json.dumps(assessment_answer(data,'DERIVED','Fehlt die Voraussetzung, beginnt die Frist nicht (BLB-PDF-Seite 1).'))
                def sol_response(**body):
                    self.assertEqual(body['reasoning'],{'effort':'medium'});self.assertEqual(body['model'],'gpt-6.1-sol')
                    self.assertEqual(body['text']['format']['schema'],response_schema_for(body['input']))
                    text=answer(body['input'])
                    return SimpleNamespace(model_dump=lambda **_: {'model':body['model'],'id':'synthetic','status':'completed',
                        'output':[{'type':'message','content':[{'type':'output_text','text':text}]}],
                        'usage':{'input_tokens':100,'output_tokens':40,'output_tokens_details':{'reasoning_tokens':20}}})
                def gemini_response(**body):
                    self.assertEqual(body['config'].thinking_config.thinking_level.value,'MEDIUM')
                    self.assertEqual(body['config'].response_json_schema,response_schema_for(body['contents']))
                    return SimpleNamespace(text=answer(body['contents']),candidates=[SimpleNamespace(finish_reason='STOP')],
                        usage_metadata={'prompt_token_count':100,'candidates_token_count':40,'thoughts_token_count':20})
                client.responses.create.side_effect=sol_response;client.models.generate_content.side_effect=gemini_response
                cache=root/'cache';ledger=root/'spend.json'
                common=['stage','--provider',provider,'--working-units',str(timing),'--pages','blb-pages','--documents','blb-boundaries',
                    '--context-pages','contract-pages','--context-documents','contract-boundaries',
                    '--cache-dir',str(cache),'--spend-ledger',str(ledger),'--max-cost-usd','6']
                if provider=='gemini':common+=['--project','synthetic']
                def load(pages,boundaries):return docs if pages=='blb-pages' else context
                with patch.dict('os.environ',{'OPENAI_API_KEY':'synthetic-not-real'}), \
                     patch('openai.OpenAI',return_value=client),patch('google.genai.Client',return_value=client), \
                     patch('bauvo_working_units.phases.consequences.stage.load_documents',side_effect=load):
                    with patch('sys.argv',common+['--output',str(root/'first/result.json'),'--max-new-calls','2']):main()
                    self.assertEqual(len(bodies),2)
                    first=load_artifact(root/'first/result.json');self.assertNotIn('consequence_verification',first)
                    self.assertEqual([len(json.loads(p.split('SOURCE DATA:\n')[1])['working_units']) for p in bodies],[10,1])
                    before=ledger.read_bytes()
                    client.responses.create.side_effect=AssertionError('No paid replay');client.models.generate_content.side_effect=AssertionError('No paid replay')
                    with patch('sys.argv',common+['--output',str(root/'replay/result.json'),'--max-new-calls','0']):main()
                    self.assertEqual(load_artifact(root/'replay/result.json')['working_units'],first['working_units'])
                    self.assertEqual(ledger.read_bytes(),before)
                    suffix='.openai-raw.json' if provider=='sol' else '.gemini-raw.json'
                    raw=json.loads((root/('first/result'+suffix)).read_text());self.assertEqual(len(raw),2)
                    self.assertEqual(set(raw[0]),{f'U{i+1}' for i in range(10)})
                    self.assertFalse(list((root/'first').glob('*verification*')))
                    (client.responses.create if provider=='sol' else client.models.generate_content).assert_called()
                    (client.models.generate_content if provider=='sol' else client.responses.create).assert_not_called()

    def test_provider_effort_and_schema_are_part_of_request_identity(self):
        prompt='PAGE_CONSEQUENCES. SOURCE DATA:\n'+json.dumps({'working_units':{'U1':{}}})
        base=provider_request(prompt,provider_identity('sol'))
        self.assertNotEqual(base,provider_request(prompt,provider_identity('sol',reasoning='high')))
        self.assertNotEqual(base,provider_request(prompt,provider_identity('gemini',project='p')))
        self.assertEqual(base['response_schema']['required'],['U1'])

    def test_phase_router_does_not_send_other_stages_to_sol(self):
        base=Mock();consequence=Mock();router=PhaseRouter(base,consequence)
        router.extract('PAGE_TIMING. source');router.extract('PAGE_CONSEQUENCES. source')
        base.extract.assert_called_once_with('PAGE_TIMING. source')
        consequence.extract.assert_called_once_with('PAGE_CONSEQUENCES. source')
