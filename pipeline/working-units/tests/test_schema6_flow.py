import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from bauvo_working_units.core import digest
from bauvo_working_units.project_flow import load_documents,make_page_chunks,extract_project_units
from bauvo_working_units.gemini import GeminiModel
from bauvo_working_units.schemas import GENERATION,RESPONSE_SCHEMAS


def fixture(root,count=12,spans=None):
    pages=[{'pdf_page':n,'text':f'Owner must complete task {n}.\n','checkboxes':[]} for n in range(1,count+1)]
    for p in pages:p['text_sha256']=hashlib.sha256(p['text'].encode()).hexdigest()
    data={'schema_version':6,'source':'house.pdf','sha256':'pdf-fingerprint','page_count':count,'pages':pages}
    spans=spans or [list(range(1,count+1))]
    docs=[{'document_id':f'D{i}','document_title':'Document','pdf_pages':span,'uncertain':False} for i,span in enumerate(spans,1)]
    boundaries={'schema_version':6,'pages_sha256':digest(pages),'status':'proposed','documents':docs,
                'page_assignments':[{'pdf_page':n,'document_id':d['document_id']} for d in docs for n in d['pdf_pages']]}
    (root/'pages.json').write_text(json.dumps(data));(root/'documents.json').write_text(json.dumps(boundaries))
    return data,boundaries

def load(root):return load_documents(root/'pages.json',root/'documents.json')
def payload(prompt):return json.loads(prompt.split('SOURCE DATA:\n',1)[1])
def unit(page,quote=None):
    return {'dedupe_key':f'task-{page["pdf_page"]}','name':'Prepare site','actor':'Owner','action':'Prepare site',
            'citations':[{'pdf_page':page['pdf_page'],'quote':quote or page['text'].rstrip('\n')}],
            'tasks':[],'depends_on':[],'unexpected':'preserve'}

class Schema6SourceTests(unittest.TestCase):
    def test_three_total_pages_overlap_once_and_primary_coverage_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root);doc=load(root)[0];chunks=make_page_chunks(doc)
            self.assertEqual([c.pages for c in chunks],[(1,2,3),(3,4,5),(5,6,7),(7,8,9),(9,10,11),(11,12)])
            self.assertEqual([n for c in chunks for n in c.primary_pages],list(range(1,13)))
            self.assertEqual(doc.lines,());self.assertTrue(all('lines' not in p for p in doc.pages))

    def test_never_crosses_distinct_forms_cover_letter_or_offer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root,19,[[1],[2],[3],[4],[5,6],[7],[8],[9],list(range(10,16)),[16],[17,18],[19]])
            docs=load(root);chunks=[(d.id,c.pages) for d in docs for c in make_page_chunks(d)]
            self.assertIn(('D9',(10,11,12)),chunks);self.assertIn(('D9',(14,15)),chunks)
            self.assertIn(('D10',(16,)),chunks);self.assertIn(('D11',(17,18)),chunks)
            for d in docs:
                for c in make_page_chunks(d):self.assertTrue(set(c.pages)<={p['pdf_page'] for p in d.pages})

    def test_blank_pages_and_large_page_are_preserved_without_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data,docs=fixture(root,7)
            data['pages'][1]['text']='';data['pages'][2]['text']='large text '*2000
            for p in data['pages']:p['text_sha256']=hashlib.sha256(p['text'].encode()).hexdigest()
            docs['pages_sha256']=digest(data['pages']);(root/'pages.json').write_text(json.dumps(data));(root/'documents.json').write_text(json.dumps(docs))
            doc=load(root)[0];chunks=make_page_chunks(doc,whole_document_max_chars=50)
            self.assertEqual(doc.pages[2]['text'],data['pages'][2]['text'])
            self.assertEqual([n for c in chunks for n in c.primary_pages],list(range(1,8)))
            self.assertTrue(any(2 in c.pages for c in chunks));self.assertTrue(all(len(c.pages)<=5 for c in chunks))

    def test_invalid_or_unverified_partitions_fail_before_any_request(self):
        for change in ('hash','overlap','gap','order','failed','disabled','assignments','no_boundaries','page_hash','duplicate_id'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);data,docs=fixture(root,6,[[1,2,3],[4,5,6]])
                if change=='hash':docs['pages_sha256']='changed'
                if change=='overlap':docs['documents'][1]['pdf_pages']=[3,4,5,6]
                if change=='gap':docs['documents'][1]['pdf_pages']=[5,6]
                if change=='order':docs['documents'].reverse()
                if change in ('failed','disabled'):docs['status']=change
                if change=='assignments':docs['page_assignments'][0]['document_id']='D2'
                if change=='page_hash':data['pages'][0]['text']='changed'
                if change=='duplicate_id':docs['documents'][1]['document_id']='D1'
                (root/'pages.json').write_text(json.dumps(data));(root/'documents.json').write_text(json.dumps(docs))
                with self.assertRaises(ValueError):load_documents(root/'pages.json',None if change=='no_boundaries' else root/'documents.json')

    def test_title_review_flags_do_not_merge_documents_or_block_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data,docs=fixture(root,2,[[1],[2]])
            docs['status']='needs_review';docs['review_flags']=['title_spans_multiple_blocks:1']
            (root/'documents.json').write_text(json.dumps(docs));self.assertEqual(len(load(root)),2)

class Schema6SDKTests(unittest.TestCase):
    def test_sdk_uses_phase_reasoning_and_pass_specific_json_schema(self):
        recorded=[]
        class Client:
            class models:
                @staticmethod
                def generate_content(**kwargs):recorded.append(kwargs);return SimpleNamespace(text='{"working_units":[],"offers":[],"carry_forward":""}',candidates=[SimpleNamespace(finish_reason='STOP')])
        model=GeminiModel.__new__(GeminiModel);model._client=Client();model._model='gemini-3.5-flash';model._types=SimpleNamespace(GenerateContentConfig=lambda **kw:kw)
        model.extract('PAGE_EXTRACTION. source')
        self.assertEqual(recorded[0]['model'],'gemini-3.5-flash')
        self.assertEqual(recorded[0]['config']['thinking_config'],{'thinking_level':'MEDIUM'})
        self.assertEqual(recorded[0]['config']['response_json_schema'],RESPONSE_SCHEMAS['PAGE_EXTRACTION'])
        self.assertEqual(recorded[0]['config']['max_output_tokens'],GENERATION['max_output_tokens'])
