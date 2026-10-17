import copy
import json
import unittest
from types import SimpleNamespace
from document_grouping import group_documents, validate_page_answer, VERSION

ARGS=SimpleNamespace(document_boundaries='auto',project='p',location='global',model='m')

def page(n,empty=False):
    return {'pdf_page':n,'text':'' if empty else 'Document title\nContinuation\nLast visible line'}

def decision(p,start=False,title='Contract',uncertain=False,within=False):
    return {'pdf_page':p['pdf_page'],'starts_new_document':start,'document_title':title,
        'evidence_quotes':[p['text'].splitlines()[0]] if start and p['text'].strip() else [],
        'reason':'Fresh standalone form title and organization' if start else 'Continuation',
        'uncertain':uncertain,'boundary_within_page':within}

def execute(pages,model,args=ARGS):
    return group_documents(args,pages,model)

def payload(parts):return json.loads(parts[0]['text'].split('SOURCE DATA:\n')[1])

class PageBoundaryTests(unittest.TestCase):
    def test_five_page_windows_carry_active_document_and_construct_complete_ordered_spans(self):
        pages=[page(n,empty=n in [5,7]) for n in range(1,13)];before=copy.deepcopy(pages);seen=[]
        def model(project,location,name,parts):
            data=payload(parts);seen.append(data)
            return {'pages':[decision(p,start=p['pdf_page'] in [1,6],title='Offer' if p['pdf_page']>=6 else 'Contract') for p in data['pages']]}
        result=execute(pages,model)
        self.assertEqual([len(p['pages']) for p in seen],[5,5,2])
        self.assertIsNone(seen[0]['previous_document'])
        self.assertEqual(seen[1]['previous_document']['document_title'],'Contract')
        self.assertEqual(seen[2]['previous_document']['document_title'],'Offer')
        self.assertEqual(result['boundary_protocol'],VERSION);self.assertEqual(result['status'],'proposed')
        a,b=result['documents'];self.assertEqual(a['pdf_pages'],[1,2,3,4,5]);self.assertEqual(b['pdf_pages'],list(range(6,13)))
        self.assertEqual([n for d in result['documents'] for n in d['pdf_pages']],list(range(1,13)))
        self.assertTrue(all(set(p)=={'pdf_page','text'} for w in seen for p in w['pages']))
        self.assertNotIn('line_ids',json.dumps(result))
        self.assertEqual([p['pdf_page'] for p in result['page_assignments']],list(range(1,13)))
        self.assertEqual(pages,before)

    def test_blank_leading_window_and_trailing_pages_are_retained_without_ids(self):
        pages=[page(n,empty=n!=7) for n in range(1,10)];seen=[]
        def model(*args):
            data=payload(args[-1]);seen.append(data)
            return {'pages':[decision(p,start=bool(p['text']),title='Contract' if p['text'] else '') for p in data['pages']]}
        r=execute(pages,model);self.assertIsNone(seen[1]['previous_document'])
        self.assertEqual(r['documents'][0]['pdf_pages'],list(range(1,10)))
        self.assertEqual(r['documents'][0]['evidence_quotes'],['Document title'])
        self.assertEqual(len(r['page_assignments']),9)

    def test_same_title_can_start_distinct_forms_and_offer_continuations_keep_identity(self):
        pages=[page(n) for n in range(1,5)]
        r=execute(pages,lambda *a:{'pages':[decision(p,start=p['pdf_page']<=3,title='Consent' if p['pdf_page']<=2 else 'Offer') for p in pages]})
        self.assertEqual([d['pdf_pages'] for d in r['documents']],[[1],[2],[3,4]])
        self.assertEqual([d['document_title'] for d in r['documents']],['Consent','Consent','Offer'])

    def test_schema_and_page_evidence_errors_are_rejected(self):
        pages=[page(1),page(2)];valid={'pages':[decision(p,start=p['pdf_page']==1) for p in pages]}
        variants=[[],{'documents':[]},{'pages':valid['pages'][:1]}, {'pages':list(reversed(valid['pages']))}]
        for field,value in [('pdf_page',True),('starts_new_document',1),('evidence_quotes',['L9999_00001']),('evidence_quotes',[]),('reason',''),('document_title','')]:
            v=copy.deepcopy(valid);v['pages'][0][field]=value;variants.append(v)
        v=copy.deepcopy(valid);v['pages'][1]['document_title']='Different title without boundary';variants.append(v)
        v=copy.deepcopy(valid);v['pages'][0]['extra']='unsupported';variants.append(v)
        for v in variants:
            with self.subTest(answer=v),self.assertRaises(ValueError):validate_page_answer(pages,v,None)
        blank=page(1,empty=True);d=decision(blank);d['evidence_quotes']=['invented']
        with self.assertRaises(ValueError):validate_page_answer([blank],{'pages':[d]},None)

    def test_retry_includes_rejected_response_and_exact_error(self):
        pages=[page(1)];seen=[]
        def model(*args):
            seen.append(args[-1][0]['text'])
            if len(seen)==1:return {'pages':[decision(pages[0],start=True),decision(pages[0],start=True)]}
            return {'pages':[decision(pages[0],start=True)]}
        r=execute(pages,model);self.assertEqual(r['status'],'proposed')
        self.assertEqual(len(r['attempts']),2);self.assertIn('REJECTED RESPONSE:',seen[1]);self.assertIn('exactly once',seen[1])
        self.assertEqual(len(r['attempts'][0]['response']['pages']),2)

    def test_late_window_failure_falls_back_without_losing_earlier_decisions_or_empty_pages(self):
        pages=[page(n,empty=n==7) for n in range(1,8)];calls=[]
        def model(*args):
            data=payload(args[-1]) if 'REJECTED RESPONSE:' not in args[-1][0]['text'] else None
            calls.append(1)
            if len(calls)==1:return {'pages':[decision(p,start=p['pdf_page']==1) for p in data['pages']]}
            return {'pages':[]}
        r=execute(pages,model);self.assertEqual(r['status'],'failed');self.assertEqual(len(calls),4)
        self.assertEqual(len(r['page_decisions']),5);self.assertEqual(len(r['attempts']),4)
        self.assertEqual(r['documents'][0]['pdf_pages'],list(range(1,8)))
        self.assertTrue(r['documents'][0]['uncertain'])
        self.assertNotIn('line_ids',r['documents'][0])

    def test_transport_failure_and_all_blank_inputs_preserve_coverage_without_approval(self):
        pages=[page(1),page(2,empty=True)]
        def fail(*args):raise RuntimeError('service down')
        r=execute(pages,fail);self.assertEqual(r['status'],'failed');self.assertEqual(len(r['attempts']),1)
        r=execute([page(1,empty=True),page(2,empty=True)],fail)
        self.assertEqual(r['status'],'empty');self.assertEqual(r['documents'],[]);self.assertEqual(len(r['page_assignments']),2)
        self.assertTrue(all(p['document_id'] is None for p in r['page_assignments']))

    def test_midpage_and_uncertain_boundaries_remain_review_findings(self):
        pages=[page(1),page(2)]
        r=execute(pages,lambda *a:{'pages':[decision(pages[0],True),decision(pages[1],within=True,uncertain=True)]})
        self.assertEqual(r['status'],'needs_review');self.assertEqual(len(r['documents']),1)
        self.assertTrue(r['documents'][0]['uncertain']);self.assertIn('within_page_document_boundary:2',r['review_flags'])

    def test_disabled_detection_is_model_free_and_keeps_empty_pages(self):
        pages=[page(1),page(2,empty=True)]
        def fail(*a):self.fail('No model calls when disabled')
        args=SimpleNamespace(**{**vars(ARGS),'document_boundaries':'off'})
        r=execute(pages,fail,args);self.assertEqual(r['status'],'disabled');self.assertEqual(r['documents'][0]['pdf_pages'],[1,2])

    def test_boundary_evidence_must_be_exact_and_local_to_the_page(self):
        p={'pdf_page':1,'text':'Contract A\n35 €/m²  due in 10 weeks'}
        valid={'pages':[decision(p,start=True)]}
        self.assertEqual(validate_page_answer([p],valid,None),valid['pages'])
        for quotes in [['Contract B'],['35 EUR/m2'],['35 €/m² due in 10 weeks'],[' '],['Contract A','Contract A']]:
            invalid=copy.deepcopy(valid);invalid['pages'][0]['evidence_quotes']=quotes
            with self.subTest(quotes=quotes),self.assertRaises(ValueError):
                validate_page_answer([p],invalid,None)
