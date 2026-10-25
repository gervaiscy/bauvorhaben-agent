import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bauvo_working_units.cache import CachedModel
from bauvo_working_units.gemini import GeminiModel
from bauvo_working_units.project_flow import load_documents, make_page_chunks
from bauvo_working_units.schemas import GENERATION, PHASE_THINKING_LEVELS, generation_for
from test_schema6_flow import fixture


class PhaseThinkingTests(unittest.TestCase):
    def test_sdk_sends_requested_level_for_each_phase_without_mutating_defaults(self):
        expected={'PAGE_PROJECT_FACTS':'MINIMAL','PAGE_EXTRACTION':'MEDIUM','PAGE_OFFERS':'MINIMAL',
                  'PAGE_TIMING':'MEDIUM','PAGE_CONSEQUENCES':'MEDIUM','PAGE_APPLICABILITY':'MEDIUM'}
        self.assertEqual(PHASE_THINKING_LEVELS,expected)
        defaults=copy.deepcopy(GENERATION);calls=[]
        def generate(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text='{}',candidates=[SimpleNamespace(finish_reason='STOP')])
        model=GeminiModel.__new__(GeminiModel)
        model._client=SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        model._types=SimpleNamespace(GenerateContentConfig=lambda **kwargs:kwargs)
        model._model='gemini-3.5-flash'
        for name,level in expected.items():
            model.extract(name+'. source')
            self.assertEqual(calls[-1]['config']['thinking_config'],{'thinking_level':level})
            self.assertEqual(calls[-1]['config']['max_output_tokens'],16384)
        changed=generation_for('PAGE_TIMING');changed['thinking_config']['thinking_level']='HIGH'
        self.assertEqual(GENERATION,defaults)
        self.assertEqual(generation_for('PAGE_TIMING')['thinking_config']['thinking_level'],'MEDIUM')

    def test_cache_does_not_reuse_response_from_a_different_thinking_level(self):
        calls=[]
        def extract(prompt):calls.append(prompt);return {'call':len(calls)}
        with tempfile.TemporaryDirectory() as tmp:
            model=CachedModel(SimpleNamespace(extract=extract),tmp,{'model':'test'})
            prompt='PAGE_EXTRACTION. source'
            first=model.extract(prompt)
            self.assertEqual(model.extract(prompt),first)
            with patch.dict(PHASE_THINKING_LEVELS,{'PAGE_EXTRACTION':'MINIMAL'}):
                second=model.extract(prompt)
                self.assertNotEqual(first,second)
            self.assertEqual(model.extract(prompt),first)
            records=[json.loads(p.read_text()) for p in Path(tmp).glob('*.json')]
            self.assertEqual({r['request']['generation']['thinking_config']['thinking_level'] for r in records},
                             {'MINIMAL','MEDIUM'})
            self.assertEqual(len(calls),2)


class BoundaryFlagTests(unittest.TestCase):
    def test_uncertain_proposals_do_not_block_or_change_source_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data,boundaries=fixture(root,count=6,spans=[[1,2,3],[4,5,6]])
            boundaries['status']='needs_review';boundaries['documents'][1]['uncertain']=True
            boundaries['review_flags']=['uncertain_page_boundary:4']
            path=root/'documents.json';path.write_text(json.dumps(boundaries));original=path.read_bytes()
            docs=load_documents(root/'pages.json',path)
            self.assertEqual(len(docs),2)
            self.assertFalse(docs[0].boundary_uncertain);self.assertTrue(docs[1].boundary_uncertain)
            self.assertEqual([c.pages for d in docs for c in make_page_chunks(d)],[(1,2,3),(4,5,6)])
            self.assertEqual(path.read_bytes(),original)
