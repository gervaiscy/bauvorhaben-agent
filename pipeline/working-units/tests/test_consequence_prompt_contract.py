"""Prompt revisions reach the provider with untouched evidence and fresh cache identity."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bauvo_working_units.cache import CachedModel
from bauvo_working_units.citation_checks import citation_findings
from bauvo_working_units.gemini import GeminiModel
from bauvo_working_units.page_flow import _prompt, PASS_RULES, PROMPT_DIRECTORY


class ConsequencePromptContractTests(unittest.TestCase):
    def payload(self):
        return {'source_scope':'BLB_AND_PROJECT_FACTS',
            'working_units':{'U1':{'title':'Baubeginn','actor':'Unternehmer','quote':'Bauarbeiten aufnehmen.',
                                  'timing':{'answer':'Vor Baubeginn.','citations':[]}}},
            'project_facts':[{'id':'F1','text':'Bei grob fahrlässigem oder vorsätzlichem Handeln entfällt der Anspruch.'}],
            'source_documents':[{'document':'B1','title':'BLB','pages':[{'pdf_page':7,'text':'Vertragsumme\n6'}]}]}

    def test_provider_receives_short_prompt_timing_and_dynamic_schema(self):
        payload=self.payload();before=copy.deepcopy(payload)
        answer={'U1':{'classification':'DERIVED','reasoning':'Bedingte Folge (F1).'}}
        sent=[]
        def generate(**kwargs):
            sent.append(kwargs)
            return SimpleNamespace(text=json.dumps(answer,ensure_ascii=False),candidates=[SimpleNamespace(finish_reason='STOP')])
        model=GeminiModel.__new__(GeminiModel)
        model._client=SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        model._types=SimpleNamespace(GenerateContentConfig=lambda **kwargs:kwargs);model._model='fixture'
        self.assertEqual(model.extract(_prompt('PAGE_CONSEQUENCES',payload,'de')),answer)
        instructions,source=sent[0]['contents'].split('SOURCE DATA:\n',1)
        self.assertIn((PROMPT_DIRECTORY/'consequences/prompt.txt').read_text().rstrip(),instructions)
        self.assertEqual(json.loads(source),payload)
        self.assertEqual(sent[0]['config']['response_json_schema']['required'],['U1'])
        self.assertEqual(sent[0]['config']['thinking_config']['thinking_level'],'MEDIUM')
        self.assertEqual(payload,before)

    def test_changed_rules_do_not_replay_a_previous_consequence_answer(self):
        payload=self.payload();calls=[]
        def provider(prompt):calls.append(prompt);return {'response_number':len(calls)}
        with TemporaryDirectory() as folder:
            cached=CachedModel(SimpleNamespace(extract=provider),folder,{'model':'fixture'})
            with patch.dict(PASS_RULES,{'PAGE_CONSEQUENCES':'Previous consequence instructions.\n'}):
                previous_prompt=_prompt('PAGE_CONSEQUENCES',payload,'de')
            previous=cached.extract(previous_prompt)
            current_prompt=_prompt('PAGE_CONSEQUENCES',payload,'de')
            current=cached.extract(current_prompt)
            self.assertNotEqual(previous,current)
            self.assertEqual(cached.extract(current_prompt),current)
            self.assertEqual(len(calls),2)
            self.assertEqual(len(list(Path(folder).glob('*.json'))),2)
            self.assertEqual(previous_prompt.split('SOURCE DATA:\n',1)[1],current_prompt.split('SOURCE DATA:\n',1)[1])
