"""Offline integration coverage for the Gemini 3.5 boundary-model switch."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'pipeline/extraction_support'),str(ROOT/'scripts')]
import document_pipeline
import grouping_client
import bauvo


class DocumentBoundaryModelTests(unittest.TestCase):
    def test_cli_uses_35_with_five_page_windows_and_last_document_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'pages.json';seen=[]
            pages=[]
            for n in range(1,13):
                sender='Northworks Ltd' if n<8 else 'Southworks Ltd'
                reference='CONTRACT-A' if n<8 else 'OFFER-B'
                value=f'{"Contract" if n<8 else "Offer"}\nTitle page {n}\n{sender}\n{reference}'
                pages.append({'pdf_page':n,'text':value,
                    'text_sha256':__import__('hashlib').sha256(value.encode()).hexdigest(),'checkboxes':[]})
            source.write_text(json.dumps({'schema_version':6,'page_count':12,'pages':pages}))
            before=source.read_bytes()
            def model(project,location,name,parts):
                data=json.loads(parts[0]['text'].split('SOURCE DATA:\n')[1]);seen.append((name,data))
                return {'pages':[{'pdf_page':p['pdf_page'],'starts_new_document':p['pdf_page'] in [1,8],
                    'document_title':'Contract' if p['pdf_page']<8 else 'Offer',
                    'title_quote':('Contract' if p['pdf_page']<8 else 'Offer') if p['pdf_page'] in (1,8) else None,
                    'sender':'Northworks Ltd' if p['pdf_page']<8 else 'Southworks Ltd',
                    'document_reference':'CONTRACT-A' if p['pdf_page']<8 else 'OFFER-B',
                    'represented_organization':None,'document_type':'contract' if p['pdf_page']<8 else 'quotation',
                    'entity_roles':[{'name':'Northworks Ltd' if p['pdf_page']<8 else 'Southworks Ltd',
                        'role':'issuer','quote':p['text'],'explanation':'Company issuing the document.'}],
                    'references':[{'value':'CONTRACT-A' if p['pdf_page']<8 else 'OFFER-B','kind':'document_identifier',
                        'quote':p['text'],'explanation':'Identifier of this document.'}],
                    'identity_evidence':[{'field':field,'quote':p['text'],'explanation':'Source supports this identity.'} for field in ('sender','document_reference','document_type')],
                    'evidence_quotes':[p['text']] if p['pdf_page'] in [1,8] else [],
                    'reason':'Visible form title','uncertain':False,'boundary_within_page':False} for p in data['pages']]}
            argv=['documents','--input',str(source),'--project','test']
            with patch.object(sys,'argv',argv),patch.object(grouping_client,'gemini_sdk',side_effect=model):
                document_pipeline.run()
            self.assertEqual([name for name,_ in seen],['gemini-3.5-flash']*3)
            self.assertEqual([len(data['pages']) for _,data in seen],[5,5,2])
            self.assertEqual([None if data['previous_document'] is None else data['previous_document']['document_title'] for _,data in seen],
                             [None,'Contract','Offer'])
            previous=[data['previous_document'] for _,data in seen]
            self.assertEqual([p['sender'] for p in previous[1:]],['Northworks Ltd','Southworks Ltd'])
            self.assertEqual([p['document_reference'] for p in previous[1:]],['CONTRACT-A','OFFER-B'])
            self.assertEqual([p['document_type'] for p in previous[1:]],['contract','quotation'])
            self.assertTrue(all(p['represented_organization'] is None for p in previous[1:]))
            self.assertTrue(all(len(p['identity_evidence'])==3 for p in previous[1:]))
            self.assertEqual([p['last_pdf_page'] for p in previous[1:]],[5,10])
            self.assertEqual([p['last_page_text'] for p in previous[1:]],[pages[4]['text'],pages[9]['text']])
            self.assertEqual(source.read_bytes(),before)
            docs=json.loads((root/'documents.json').read_text())['documents']
            self.assertEqual([d['pdf_pages'] for d in docs],[list(range(1,8)),list(range(8,13))])
            self.assertEqual([d['sender'] for d in docs],['Northworks Ltd','Southworks Ltd'])
            self.assertEqual([d['document_reference'] for d in docs],['CONTRACT-A','OFFER-B'])
            self.assertEqual([d['document_type'] for d in docs],['contract','quotation'])
            self.assertTrue(all('identity_evidence' in d for d in docs))

    def test_cli_persists_semantic_flags_and_exact_raw_response_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'pages.json';calls=[]
            pages=[{'pdf_page':n,'text':f'Report {n}\nNorthworks Ltd','checkboxes':[]} for n in (1,2)]
            for p in pages:p['text_sha256']=__import__('hashlib').sha256(p['text'].encode()).hexdigest()
            source.write_text(json.dumps({'schema_version':6,'page_count':2,'pages':pages}));before=source.read_bytes()
            answer={'pages':[{'pdf_page':p['pdf_page'],'starts_new_document':p['pdf_page']==1,
                'document_title':'Report' if p['pdf_page']==1 else 'Different title','title_quote':'Report' if p['pdf_page']==1 else None,
                'sender':'Northworks Ltd' if p['pdf_page']==1 else 'Unsupported Ltd',
                'represented_organization':None,'document_type':'other','document_reference':None,
                'entity_roles':[{'name':'Northworks Ltd','role':'issuer','quote':p['text'],
                    'explanation':'Company issuing the document.'}],'references':[],
                'identity_evidence':[{'field':field,'quote':p['text'],'explanation':'Source supports this identity.'} for field in ('sender','document_type')],
                'evidence_quotes':[p['text']] if p['pdf_page']==1 else [],
                'reason':'Visible title','uncertain':False,'boundary_within_page':False} for p in pages]}
            original=copy.deepcopy(answer)
            def model(*args):calls.append(1);return answer
            with patch.object(sys,'argv',['documents','--input',str(source),'--project','test']),patch.object(grouping_client,'gemini_sdk',side_effect=model):
                document_pipeline.run()
            result=json.loads((root/'documents.json').read_text())
            quality=json.loads((root/'quality-report.json').read_text())
            self.assertEqual(calls,[1]);self.assertEqual(result['status'],'needs_review')
            self.assertEqual(quality['status'],'needs_review')
            self.assertEqual(quality['boundary_findings'],result['review_flags'])
            self.assertIn('continuation_title_change:2',result['review_flags'])
            self.assertIn('continuation_identity_change:2:sender',result['documents'][0]['review_flags'])
            self.assertEqual(result['documents'][0]['pdf_pages'],[1,2])
            self.assertEqual(result['attempts'][0]['response'],original)
            self.assertEqual(answer,original);self.assertEqual(source.read_bytes(),before)
            self.assertIn('continuation_title_change:2',(root/'source-review.html').read_text())

    def test_cli_selects_classified_roles_and_references_and_preserves_cached_raw_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'pages.json';calls=[]
            text='Consent form\nIssued by Cedar Services Ltd\nSigner: Morgan Lake\nForm number F-18\nRelated agreement dated 2040-05-06'
            source.write_text(json.dumps({'schema_version':6,'page_count':1,'pages':[{
                'pdf_page':1,'text':text,'text_sha256':__import__('hashlib').sha256(text.encode()).hexdigest(),'checkboxes':[]}]}))
            before=source.read_bytes()
            roles=[{'name':'Cedar Services Ltd','role':'issuer','quote':'Issued by Cedar Services Ltd',
                    'explanation':'The form explicitly identifies its issuer.'},
                   {'name':'Morgan Lake','role':'signer','quote':'Signer: Morgan Lake',
                    'explanation':'The name labels an intended signer.'}]
            references=[{'value':'F-18','kind':'document_identifier','quote':'Form number F-18',
                         'explanation':'This is the form identifier.'},
                        {'value':'2040-05-06','kind':'related_document_date','quote':'Related agreement dated 2040-05-06',
                         'explanation':'This date belongs to a related agreement.'}]
            answer={'pages':[{'pdf_page':1,'starts_new_document':True,'document_title':'Consent form','title_quote':'Consent form',
                'sender':'Morgan Lake','represented_organization':None,'document_type':'consent_form',
                'document_reference':'2040-05-06','entity_roles':roles,'references':references,
                'identity_evidence':[{'field':field,'quote':text,'explanation':'Model assignment.'}
                    for field in ('sender','document_type','document_reference')],
                'evidence_quotes':['Consent form'],'reason':'Standalone form','uncertain':False,'boundary_within_page':False}]}
            original=copy.deepcopy(answer)
            def model(*args):calls.append(1);return answer
            with patch.object(sys,'argv',['documents','--input',str(source),'--project','test']),patch.object(grouping_client,'gemini_sdk',side_effect=model):
                document_pipeline.run()
                document_pipeline.run()
            result=json.loads((root/'documents.json').read_text());document=result['documents'][0]
            self.assertEqual(calls,[1]);self.assertEqual(result['status'],'needs_review')
            self.assertEqual(document['sender'],'Cedar Services Ltd');self.assertEqual(document['document_reference'],'F-18')
            for field in ('sender','document_reference'):
                self.assertIn(f'identity_classification_conflict:1:{field}',result['review_flags'])
            self.assertEqual(document['references'][1],{**references[1],'pdf_page':1})
            cached=json.loads(next((root/'grouping-cache').glob('*.json')).read_text())
            self.assertEqual(cached['response'],original);self.assertEqual(answer,original)
            self.assertEqual(source.read_bytes(),before)
            schema=cached['request']['response_schema']['properties']['pages']['items']['properties']
            self.assertIn('explanation',schema['entity_roles']['items']['required'])
            self.assertIn('related_document_date',schema['references']['items']['properties']['kind']['enum'])
            self.assertEqual(json.loads((root/'quality-report.json').read_text())['status'],'needs_review')

    def test_cli_generates_titles_from_exact_quotes_without_manual_output_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'pages.json';seen=[];answers=[]
            quote='Supplement to agreement\n dated 2043-08-19'
            pages=[{'pdf_page':n,'text':quote+'\nRequest for early design work.' if n==1 else 'Drawing attachment',
                    'checkboxes':[]} for n in range(1,7)]
            for p in pages:p['text_sha256']=__import__('hashlib').sha256(p['text'].encode()).hexdigest()
            source.write_text(json.dumps({'schema_version':6,'page_count':6,'pages':pages}));before=source.read_bytes()
            def model(project,location,name,parts):
                data=json.loads(parts[0]['text'].split('SOURCE DATA:\n')[1]);seen.append(data)
                answer={'pages':[{'pdf_page':p['pdf_page'],'starts_new_document':p['pdf_page']==1,
                    'document_title':'Supplement to agreement - Request for early design work',
                    'title_quote':quote if p['pdf_page']==1 else None,
                    'sender':None,'represented_organization':None,'document_type':'addendum',
                    'document_reference':None,'entity_roles':[],'references':[],
                    'identity_evidence':[{'field':'document_type','quote':quote,'explanation':'Supplement title.'}] if p['pdf_page']==1 else [],
                    'evidence_quotes':[quote] if p['pdf_page']==1 else [],
                    'reason':'Standalone supplement' if p['pdf_page']==1 else 'Attachment continuation',
                    'uncertain':False,'boundary_within_page':False} for p in data['pages']]}
                answers.append(copy.deepcopy(answer));return answer
            with patch.object(sys,'argv',['documents','--input',str(source),'--project','test']),patch.object(grouping_client,'gemini_sdk',side_effect=model):
                document_pipeline.run();document_pipeline.run()
            result=json.loads((root/'documents.json').read_text());doc=result['documents'][0]
            expected='Supplement to agreement dated 2043-08-19'
            self.assertEqual(len(seen),2);self.assertEqual([len(w['pages']) for w in seen],[5,1])
            self.assertEqual(doc['document_title'],expected);self.assertEqual(doc['pdf_pages'],list(range(1,7)))
            self.assertEqual(doc['title_evidence'],{'pdf_page':1,'quote':quote})
            self.assertEqual(seen[1]['previous_document']['document_title'],expected)
            self.assertEqual([a['response'] for a in result['attempts']],answers)
            self.assertIn('title_transcription_conflict:1',result['review_flags'])
            self.assertNotIn('title_corrections',result)
            self.assertEqual(source.read_bytes(),before)
            self.assertIn(expected,(root/'source-review.html').read_text())

    def test_boundary_override_is_independent_of_analysis_and_transcription(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(bauvo,'ROOT',Path(tmp)),patch.dict(os.environ,{},clear=True):
            (Path(tmp)/'.env').write_text('BAUVO_MODEL=gemini-2.5-flash\nBAUVO_DOCUMENT_BOUNDARY_MODEL=custom-boundary-model\n')
            config=bauvo.load_environment()
            self.assertEqual(config['document_boundary_model'],'custom-boundary-model')
            self.assertEqual(config['model'],'gemini-2.5-flash')
            self.assertEqual(config['ocr_model'],'gemini-3.5-flash')

    def test_cache_binds_model_and_generation_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            args=SimpleNamespace(project='p',location='global',model='gemini-3.5-flash')
            folder=Path(tmp);parts=[{'text':'Source pages'}];calls=[]
            def invoke():calls.append(1);return {'pages':[]}
            grouping_client.cached_group_call(args,parts,folder,invoke)
            grouping_client.cached_group_call(args,parts,folder,invoke)
            self.assertEqual(len(calls),1)
            generation=copy.deepcopy(grouping_client.GENERATION)
            generation['thinking_config']['thinking_level']='LOW'
            with patch.object(grouping_client,'GENERATION',generation):
                grouping_client.cached_group_call(args,parts,folder,invoke)
            self.assertEqual(len(calls),2)
            args.model='different-model'
            grouping_client.cached_group_call(args,parts,folder,invoke)
            self.assertEqual(len(calls),3)
            saved=[json.loads(p.read_text()) for p in folder.glob('*.json')]
            self.assertTrue(all('generation' in p['request'] for p in saved))
