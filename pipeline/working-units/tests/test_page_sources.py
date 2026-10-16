import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from bauvo_working_units.core import digest,read_pages
from bauvo_working_units.project_flow import load_documents,make_page_chunks,_prompt,_modality_prompt,_obligation_prompt


def fixture(number=8):
    pages=[]
    for n in range(1,number+1):
        text=f'Page {n} footer condition\n'
        pages.append({'pdf_page':n,'text':text,'text_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'checkboxes':[],'lines':[{'id':f'L{n}','pdf_page':n,'text':text.rstrip('\n'),'text_segments':[[0,len(text)]]}]})
    return {'schema_version':5,'source':'contract.pdf','page_count':number,'selected_pages':list(range(1,number+1)),'pages':pages}


def boundaries(data,uncertain=False):
    return {'schema_version':5,'pages_sha256':digest(data['pages']),'documents':[
        {'document_id':'D1','document_title':'Consent A','line_ids':['L1','L2'],'uncertain':False},
        {'document_id':'D2','document_title':'Consent B','line_ids':['L3','L4'],'uncertain':uncertain}]}

class PageSourceTests(unittest.TestCase):
    def write(self,root,data,docs=None):
        (root/'pages.json').write_text(json.dumps(data))
        if docs is not None:(root/'documents.json').write_text(json.dumps(docs))
        return load_documents(root/'pages.json',root/'documents.json' if docs is not None else None)
    def test_overlapping_windows_have_at_most_three_total_pages_and_all_primary_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs=self.write(Path(tmp),fixture());chunks=make_page_chunks(docs[0])
            self.assertEqual([c.pages for c in chunks],[(1,2,3),(3,4,5),(5,6,7),(7,8)])
            self.assertEqual([n for c in chunks for n in c.primary_pages],list(range(1,9)))
            for c in chunks:
                for prompt in (_prompt(docs[0],c,{},[],'de'),_modality_prompt(docs[0],c,[],'de'),_obligation_prompt(docs[0],c,[],'de')):
                    payload=json.loads(prompt.split('\n')[-1]);self.assertLessEqual(len(payload['source_pages']),3)
                    self.assertEqual([p['text'] for p in payload['source_pages']],[fixture()['pages'][n-1]['text'] for n in c.pages])
                    self.assertNotIn('content_blocks',payload)
            with self.assertRaises(ValueError):make_page_chunks(docs[0],chunk_pages=4)
    def test_distinct_documents_are_separated_without_dropping_footer_or_logo_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=fixture(4);docs=self.write(Path(tmp),data,boundaries(data))
            self.assertEqual([[l.id for l in d.lines] for d in docs],[['L1','L2'],['L3','L4']]);self.assertEqual(len(docs),2)
            self.assertEqual([c.pages for d in docs for c in make_page_chunks(d)],[(1,2),(3,4)])
    def test_uncertain_boundaries_conservatively_keep_cross_boundary_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=fixture(4);docs=self.write(Path(tmp),data,boundaries(data,True))
            self.assertEqual(len(docs),1);self.assertTrue(docs[0].boundary_uncertain);self.assertEqual(make_page_chunks(docs[0])[0].pages,(1,2,3))
    def test_midpage_boundaries_send_only_each_documents_exact_text_slice(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=fixture(1);p=data['pages'][0];p['text']='Form A\nForm B\n';p['text_sha256']=hashlib.sha256(p['text'].encode()).hexdigest();p['lines']=[{'id':'A','pdf_page':1,'text':'Form A','text_segments':[[0,7]]},{'id':'B','pdf_page':1,'text':'Form B','text_segments':[[7,14]]}]
            split={'schema_version':5,'pages_sha256':digest(data['pages']),'documents':[{'document_id':x,'document_title':x,'line_ids':[x],'uncertain':False} for x in ('A','B')]}
            docs=self.write(Path(tmp),data,split);self.assertEqual([d.pages[0]['text'] for d in docs],['Form A\n','Form B\n'])
    def test_tampering_gaps_duplicate_ids_or_old_schema_cannot_reach_models(self):
        for change in ('text','line','duplicate','gap','old','dropped'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as tmp:
                data=fixture(4);split=boundaries(data)
                if change=='text':data['pages'][0]['text']='Altered'
                if change=='line':data['pages'][0]['lines'][0]['text']='Altered'
                if change=='duplicate':data['pages'][1]['lines'][0]['id']='L1'
                if change=='gap':data['selected_pages']=[1,3,4]
                if change=='old':data['schema_version']=4
                if change=='dropped':split['documents'][0]['line_ids']=[]
                with self.assertRaises(ValueError):self.write(Path(tmp),data,split)

    def test_blank_interior_pages_count_toward_the_physical_window_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=fixture(6)
            for n in (2,3):
                p=data['pages'][n-1];p['text']='';p['text_sha256']=hashlib.sha256(b'').hexdigest();p['lines']=[]
            docs=self.write(Path(tmp),data);chunks=make_page_chunks(docs[0])
            self.assertEqual([c.pages for c in chunks],[(1,2,3),(3,4,5),(5,6)])
            self.assertTrue(all(max(c.pages)-min(c.pages)<=2 for c in chunks))
