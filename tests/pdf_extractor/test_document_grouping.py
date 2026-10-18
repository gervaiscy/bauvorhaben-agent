"""Boundary-only protocol regressions; metadata extraction is a separate concern."""
import copy
import json
import unittest
from types import SimpleNamespace
from document_grouping import group_documents,validate_page_answer,VERSION,PAGE_PROPERTIES

ARGS=SimpleNamespace(document_boundaries='auto',project='p',location='global',model='m')
def payload(parts):return json.loads(parts[0]['text'].split('SOURCE DATA:\n',1)[1])
def decision(p,start=False,title='Contract',uncertain=False):
    return {'pdf_page':p['pdf_page'],'starts_new_document':start,
            'document_title':title if start else None,
            'evidence_quote':p['text'].splitlines()[0][:240] if p['text'].strip() else None,'uncertain':uncertain}
def execute(pages,model,args=ARGS):return group_documents(args,pages,model)

class PageBoundaryTests(unittest.TestCase):
    def test_five_page_windows_have_only_active_title_start_and_full_previous_page(self):
        pages=[{'pdf_page':n,'text':'First document\nBody' if n<6 else 'Second document\nBody'} for n in range(1,13)]
        pages[4]['text']+='\n'+'Long context. '*1000;before=copy.deepcopy(pages);seen=[]
        def model(*args):
            data=payload(args[-1]);seen.append(data)
            return {'pages':[decision(p,p['pdf_page'] in (1,6),'First document' if p['pdf_page']<6 else 'Second document') for p in data['pages']]}
        result=execute(pages,model)
        self.assertEqual([len(d['pages']) for d in seen],[5,5,2]);self.assertIsNone(seen[0]['previous_page'])
        self.assertEqual(seen[1]['previous_document'],{'document_title':'First document','start_pdf_page':1})
        self.assertEqual(seen[2]['previous_document'],{'document_title':'Second document','start_pdf_page':6})
        self.assertEqual(seen[1]['previous_page'],pages[4]);self.assertEqual(seen[2]['previous_page'],pages[9])
        self.assertEqual([d['pdf_pages'] for d in result['documents']],[list(range(1,6)),list(range(6,13))])
        self.assertEqual(result['review_flags'],[]);self.assertEqual(pages,before)

    def test_covering_letters_and_consent_forms_split_across_windows_while_quotation_continues(self):
        for offset in (0,3,4,8,9):
            texts=['Notice']*offset+['Letter\nFrom: Avery Reed\nIn cooperation with Cedar Ltd\nEnclosed quotation Q-8',
                'Quotation\nCedar Ltd\nQ-8\nPage 1 of 2','Quotation\nCedar Ltd\nQ-8\nPage 2 of 2',
                'Consent\nCedar Construction Ltd\nSignature','Consent\nCedar Sales Ltd\nSignature']
            pages=[{'pdf_page':n,'text':t} for n,t in enumerate(texts,1)];seen=[]
            def model(*args):
                prompt=args[-1][0]['text'];data=payload(args[-1]);seen.append(data)
                self.assertIn('Separate standalone correspondence',prompt)
                self.assertIn('forms for different organizations',prompt)
                return {'pages':[decision(p,p['pdf_page'] in (1,offset+1,offset+2,offset+4,offset+5),p['text'].splitlines()[0]) for p in data['pages']]}
            result=execute(pages,model)
            with self.subTest(offset=offset):
                spans=[d['pdf_pages'] for d in result['documents'] if d['pdf_pages'][0]>offset]
                self.assertEqual(spans,[[offset+1],[offset+2,offset+3],[offset+4],[offset+5]])
                self.assertTrue(all(len(d['pages'])<=5 for d in seen));self.assertEqual(result['review_flags'],[])

    def test_continuous_publication_and_integral_attachments_remain_together(self):
        pages=[{'pdf_page':n,'text':'Quotation\nIncludes attached plans for model Cedar' if n==1 else 'Drawing caption\nModel Cedar' if n in (4,5) else 'Repeated header\nChapter'} for n in range(1,8)]
        def model(*args):
            prompt=args[-1][0]['text'];data=payload(args[-1])
            self.assertIn('A sign-off or total does not exclude incorporated attachments',prompt)
            self.assertIn('new caption alone is not a new source document',prompt)
            if data['previous_document']:self.assertEqual(data['previous_document']['document_title'],'Quotation')
            return {'pages':[decision(p,p['pdf_page']==1,'Quotation') for p in data['pages']]}
        result=execute(pages,model)
        self.assertEqual(result['documents'][0]['pdf_pages'],list(range(1,8)));self.assertEqual(result['review_flags'],[])

    def test_failed_answer_is_preserved_but_never_replayed_in_retry(self):
        pages=[{'pdf_page':n,'text':'Contract\nBody'} for n in range(1,5)];seen=[]
        broken={'pages':[{'nonsense':'RUNAWAY_MARKER '*10000}]}
        def model(*args):
            prompt=args[-1][0]['text'];seen.append(prompt)
            if len(seen)==1:return broken
            self.assertNotIn('RUNAWAY_MARKER',prompt);self.assertNotIn('REJECTED RESPONSE',prompt)
            self.assertIn('VALIDATION ERROR:',prompt)
            return {'pages':[decision(p,p['pdf_page']==1) for p in payload(args[-1])['pages']]}
        original=copy.deepcopy(broken);result=execute(pages,model)
        self.assertEqual(result['attempts'][0]['response'],original);self.assertEqual(broken,original)
        self.assertEqual(payload([{'text':seen[0]}]),payload([{'text':seen[1]}]))
        self.assertLess(len(seen[1])-len(seen[0]),512);self.assertEqual(result['status'],'proposed')

    def test_three_invalid_answers_fall_back_and_preserve_all_source_pages(self):
        pages=[{'pdf_page':n,'text':'Contract'} for n in range(1,8)];seen=[]
        def model(*args):
            data=payload(args[-1]);seen.append(data)
            return {'pages':[decision(p,p['pdf_page']==1) for p in data['pages']]} if data['pages'][0]['pdf_page']==1 else {'pages':[]}
        result=execute(pages,model)
        self.assertEqual(len(seen),4);self.assertEqual(result['status'],'failed')
        self.assertEqual(len(result['page_decisions']),5);self.assertEqual(result['documents'][0]['pdf_pages'],list(range(1,8)))
        self.assertEqual(result['review_flags'],['document_boundaries_unverified'])

    def test_transport_failure_retains_prior_decisions(self):
        pages=[{'pdf_page':n,'text':'Contract'} for n in range(1,7)]
        def model(*args):
            data=payload(args[-1])
            if data['pages'][0]['pdf_page']==6:raise TimeoutError('Transport failure')
            return {'pages':[decision(p,p['pdf_page']==1) for p in data['pages']]}
        result=execute(pages,model)
        self.assertEqual(result['attempts'][-1]['error'],'TimeoutError');self.assertEqual(len(result['page_decisions']),5)
        self.assertEqual(result['documents'][0]['pdf_pages'],list(range(1,7)))

    def test_empty_pages_remain_attached_and_previous_page_can_be_empty(self):
        pages=[{'pdf_page':n,'text':'Guide' if n==3 else ''} for n in range(1,8)];seen=[]
        def model(*args):
            d=payload(args[-1]);seen.append(d)
            return {'pages':[decision(p,p['pdf_page']==3,'Guide') for p in d['pages']]}
        result=execute(pages,model)
        self.assertEqual(result['documents'][0]['pdf_pages'],list(range(1,8)))
        self.assertEqual(seen[1]['previous_page'],pages[4]);self.assertEqual(result['review_flags'],[])

    def test_all_empty_or_disabled_never_call_model(self):
        def model(*a):self.fail('Unexpected model call')
        pages=[{'pdf_page':1,'text':''}]
        self.assertEqual(execute(pages,model)['status'],'empty')
        pages[0]['text']='Contract'
        args=SimpleNamespace(**{**vars(ARGS),'document_boundaries':'off'})
        self.assertEqual(execute(pages,model,args)['status'],'disabled')

    def test_first_nonempty_start_is_structural(self):
        p={'pdf_page':1,'text':'Guide'};raw=decision(p,True,'Guide');raw['starts_new_document']=False;raw['evidence_quote']=None
        result=execute([p],lambda *a:{'pages':[raw]})
        self.assertTrue(result['page_decisions'][0]['starts_new_document']);self.assertEqual(result['review_flags'],[])
        self.assertFalse(raw['starts_new_document'])

    def test_uncertain_decisions_remain_review_flags_without_extra_requests(self):
        p={'pdf_page':1,'text':'Guide'};calls=[]
        def model(*args):calls.append(1);return {'pages':[decision(p,True,'Guide',True)]}
        result=execute([p],model)
        self.assertEqual(calls,[1]);self.assertEqual(result['review_flags'],['uncertain_page_boundary:1'])
        self.assertTrue(result['documents'][0]['uncertain'])

    def test_missing_or_unsupported_titles_flag_metadata_without_guessing_boundary(self):
        p={'pdf_page':1,'text':'Visible heading'}
        for title in (None,'Invented topic'):
            result=execute([p],lambda *a:{'pages':[decision(p,True,title)]})
            self.assertEqual(result['documents'][0]['document_title'],'');self.assertTrue(result['review_flags'])
            self.assertFalse(result['documents'][0]['uncertain'])

    def test_multiline_literal_title_preserves_source_passage(self):
        p={'pdf_page':1,'text':'Consumer notice\n pursuant to Schedule 12\n\nBody paragraph'}
        result=execute([p],lambda *a:{'pages':[decision(p,True,'Consumer notice pursuant to Schedule 12')]})
        self.assertEqual(result['documents'][0]['title_evidence']['quote'],'Consumer notice\n pursuant to Schedule 12')
        self.assertEqual(result['review_flags'],[])

    def test_letterhead_is_excluded_when_model_selects_heading_without_company_rules(self):
        p={'pdf_page':1,'text':'Northwind Services Ltd\ncontact@example.org\n\nPrivacy information\nfor customers\n\nBody paragraph.'}
        calls=[]
        def model(*args):
            prompt=args[-1][0]['text'];calls.append(prompt)
            self.assertIn("Copy only the document's heading",prompt)
            self.assertIn('unless they are explicitly part of the heading',prompt)
            return {'pages':[decision(p,True,'Privacy information for customers')]}
        result=execute([p],model)
        self.assertEqual(result['documents'][0]['document_title'],'Privacy information for customers')
        self.assertEqual(result['documents'][0]['title_evidence']['quote'],'Privacy information\nfor customers')
        self.assertEqual(result['review_flags'],[]);self.assertEqual(len(calls),1)

    def test_organization_names_legitimately_inside_heading_are_preserved(self):
        for title in ('Annual report of Northwind Services Ltd', 'Agreement between Westfield Ltd and Eastfield Ltd'):
            p={'pdf_page':1,'text':'Corporate letterhead\n\n'+title+'\n\nBody paragraph.'}
            result=execute([p],lambda *a:{'pages':[decision(p,True,title)]})
            self.assertEqual(result['documents'][0]['document_title'],title)
            self.assertEqual(result['review_flags'],[])

    def test_multi_block_titles_are_reviewed_without_trimming_or_boundary_uncertainty(self):
        for separator in ('\n\n','\n \t\n','\r\n\t\r\n'):
            for prefix in ('Northwind Services Ltd','Consumer information'):
                passage=prefix+separator+'Privacy notice'
                p={'pdf_page':1,'text':passage+'\n\nBody paragraph.'}
                raw={'pages':[decision(p,True,' '.join(passage.split()))]};before=copy.deepcopy(raw);calls=[]
                def model(*args):calls.append(1);return raw
                result=execute([p],model);doc=result['documents'][0]
                with self.subTest(separator=separator,prefix=prefix):
                    self.assertEqual(result['review_flags'],['title_spans_multiple_blocks:1'])
                    self.assertEqual(doc['review_flags'],result['review_flags'])
                    self.assertEqual(result['status'],'needs_review');self.assertFalse(doc['uncertain'])
                    self.assertTrue(result['page_decisions'][0]['starts_new_document'])
                    self.assertEqual(doc['document_title'],' '.join(passage.split()))
                    self.assertEqual(doc['title_evidence']['quote'],passage)
                    self.assertEqual(raw,before);self.assertEqual(calls,[1])

    def test_bad_structure_page_order_types_and_nonlocal_quotes_are_rejected(self):
        pages=[{'pdf_page':n,'text':'Guide'} for n in (1,2)];good={'pages':[decision(p,p['pdf_page']==1,'Guide') for p in pages]}
        for kind in ('order','bool_page','missing','extra','bool_flag','nonlocal','long','empty_title'):
            raw=copy.deepcopy(good)
            if kind=='order':raw['pages'].reverse()
            if kind=='bool_page':raw['pages'][0]['pdf_page']=True
            if kind=='missing':raw['pages'][0].pop('uncertain')
            if kind=='extra':raw['pages'][0]['sender']='Unknown'
            if kind=='bool_flag':raw['pages'][0]['uncertain']='false'
            if kind=='nonlocal':raw['pages'][1]['evidence_quote']='Not on page'
            if kind=='long':raw['pages'][0]['evidence_quote']='x'*241;pages[0]['text']='x'*241
            if kind=='empty_title':raw['pages'][0]['document_title']=''
            with self.subTest(kind=kind),self.assertRaises(ValueError):validate_page_answer(pages,raw,None)
            pages[0]['text']='Guide'

    def test_missing_transition_evidence_and_continuation_title_are_reviewed(self):
        pages=[{'pdf_page':1,'text':'Guide'},{'pdf_page':2,'text':'Form'}]
        raw=[decision(pages[0],True,'Guide'),decision(pages[1],True,'Form')];raw[1]['evidence_quote']=None
        result=execute(pages,lambda *a:{'pages':raw})
        self.assertIn('boundary_evidence_missing:2',result['review_flags']);self.assertEqual(len(result['documents']),2)
        raw[1]=decision(pages[1]);raw[1]['document_title']='Form'
        result=execute(pages,lambda *a:{'pages':raw})
        self.assertIn('continuation_title_supplied:2',result['review_flags']);self.assertEqual(len(result['documents']),1)

    def test_schema_has_only_boundary_fields(self):
        self.assertEqual(set(PAGE_PROPERTIES),{'pdf_page','starts_new_document','document_title','evidence_quote','uncertain'})
