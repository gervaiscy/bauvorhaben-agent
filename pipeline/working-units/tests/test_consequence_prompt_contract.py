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
        return {'source_key':'contract','document':{'id':'TASK','title':'Aufgabe'},
            'source_pages':[{'pdf_page':1,'text':'Bauarbeiten aufnehmen.'}],
            'working_units':[{'id':'U1','action':'Bauarbeiten aufnehmen'}],
            'source_documents':[{'source_key':'terms','document_id':'CONDITIONS','pages':[
                {'pdf_page':7,'text':'Bei grob fahrlässigem oder vorsätzlichem Handeln entfällt der Anspruch.\nVertragsumme\n6'}]}]}

    def test_provider_receives_current_rules_and_original_qualifiers_spelling_and_physical_page(self):
        payload=self.payload();before=copy.deepcopy(payload)
        quote=payload['source_documents'][0]['pages'][0]['text'].split('\n6')[0]
        citation={'source_key':'terms','document_id':'CONDITIONS','pdf_page':7,'quote':quote}
        answer={'assessments':[{'unit_id':'U1','status':'DERIVED',
            'consequences':['Abgeleitet: Bei grob fahrlässigem oder vorsätzlichem Handeln entfällt der Anspruch.'],
            'citations':[citation]}]}
        sent=[]
        def generate(**kwargs):
            sent.append(kwargs)
            return SimpleNamespace(text=json.dumps(answer,ensure_ascii=False),candidates=[SimpleNamespace(finish_reason='STOP')])
        model=GeminiModel.__new__(GeminiModel)
        model._client=SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        model._types=SimpleNamespace(GenerateContentConfig=lambda **kwargs:kwargs);model._model='fixture'
        result=model.extract(_prompt('PAGE_CONSEQUENCES',payload,'de'))
        instructions,source=sent[0]['contents'].split('SOURCE DATA:\n',1)
        current=(PROMPT_DIRECTORY/'consequences/prompt.txt').read_text(encoding='utf-8')
        self.assertIn(current,instructions)
        self.assertEqual(json.loads(source),payload)
        self.assertEqual(result,answer)
        evidence=[{**page,'source_key':doc['source_key'],'document_id':doc['document_id']}
                  for doc in payload['source_documents'] for page in doc['pages']]
        self.assertEqual(citation_findings(result,[],evidence,require_qualified=True),[])
        for changed in ({**citation,'pdf_page':6},{**citation,'quote':quote.replace('Vertragsumme','Vertragssumme')}):
            with self.subTest(citation=changed):
                self.assertTrue(citation_findings({'citations':[changed]},[],evidence,require_qualified=True))
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
