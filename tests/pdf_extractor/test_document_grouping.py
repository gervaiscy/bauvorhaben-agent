import copy
import json
import unittest
from types import SimpleNamespace
from document_grouping import group_documents, validate_page_answer, VERSION

ARGS=SimpleNamespace(document_boundaries='auto',project='p',location='global',model='m')

def page(n,empty=False):
    return {'pdf_page':n,'text':'' if empty else 'Document title\nContinuation\nLast visible line'}

def decision(p,start=False,title='Contract',uncertain=False,within=False,sender=None,
             document_reference=None,represented_organization=None,document_type=None,identity_evidence=None,entity_roles=None,references=None,title_quote=None):
    values={'sender':sender,'represented_organization':represented_organization,
            'document_type':document_type,'document_reference':document_reference}
    if identity_evidence is None:
        identity_evidence=[{'field':field,'quote':p['text']} for field,value in values.items()
            if value is not None and (field=='document_type' or value in p['text']) and p['text'].strip()]
    identity_evidence=[{**entry,'explanation':entry.get('explanation','The passage supports this identity assignment.')} for entry in identity_evidence]
    if entity_roles is None:
        roles={'sender':'correspondence_sender' if document_type in ('letter','covering_letter') else 'issuer',
               'represented_organization':'represented_organization'}
        entity_roles=[{'name':values[entry['field']],'role':roles[entry['field']],
                       'quote':entry['quote'],'explanation':'The passage establishes this role.'}
                      for entry in identity_evidence if entry['field'] in roles and values[entry['field']] is not None]
    if references is None:
        references=[{'value':document_reference,'kind':'document_identifier','quote':entry['quote'],
                     'explanation':'The passage identifies this document.'} for entry in identity_evidence
                    if entry['field']=='document_reference' and document_reference is not None]
    return {'pdf_page':p['pdf_page'],'starts_new_document':start,'document_title':title,'title_quote':title_quote if title_quote is not None else title if start and title and title in p['text'] else None,**values,
        'identity_evidence':identity_evidence,'entity_roles':entity_roles,'references':references,
        'evidence_quotes':[p['text'].splitlines()[0]] if start and p['text'].strip() else [],
        'reason':'Fresh standalone form title and organization' if start else 'Continuation',
        'uncertain':uncertain,'boundary_within_page':within}

def execute(pages,model,args=ARGS):
    return group_documents(args,pages,model)

def payload(parts):return json.loads(parts[0]['text'].split('SOURCE DATA:\n')[1])

class PageBoundaryTests(unittest.TestCase):
    def test_five_page_windows_carry_active_document_and_construct_complete_ordered_spans(self):
        pages=[page(n,empty=n in [5,7]) for n in range(1,13)]
        for p in pages:
            if p['text']:p['text']=('Contract' if p['pdf_page']<6 else 'Offer')+'\n'+p['text']
        before=copy.deepcopy(pages);seen=[]
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
        pages=[{'pdf_page':n,'text':('Consent' if n<=2 else 'Offer')+'\nVisible form text'} for n in range(1,5)]
        r=execute(pages,lambda *a:{'pages':[decision(p,start=p['pdf_page']<=3,title='Consent' if p['pdf_page']<=2 else 'Offer') for p in pages]})
        self.assertEqual([d['pdf_pages'] for d in r['documents']],[[1],[2],[3,4]])
        self.assertEqual([d['document_title'] for d in r['documents']],['Consent','Consent','Offer'])

    def test_schema_and_page_evidence_errors_are_rejected(self):
        pages=[page(1),page(2)];valid={'pages':[decision(p,start=p['pdf_page']==1) for p in pages]}
        variants=[[],{'documents':[]},{'pages':valid['pages'][:1]}, {'pages':list(reversed(valid['pages']))}]
        for field,value in [('pdf_page',True),('starts_new_document',1),('evidence_quotes',['L9999_00001'])]:
            v=copy.deepcopy(valid);v['pages'][0][field]=value;variants.append(v)
        v=copy.deepcopy(valid);v['pages'][0]['extra']='unsupported';variants.append(v)
        for v in variants:
            with self.subTest(answer=v),self.assertRaises(ValueError):validate_page_answer(pages,v,None)
        blank=page(1,empty=True);d=decision(blank);d['evidence_quotes']=['invented']
        with self.assertRaises(ValueError):validate_page_answer([blank],{'pages':[d]},None)

    def test_retry_includes_rejected_response_and_exact_error(self):
        pages=[{'pdf_page':1,'text':'Contract\nVisible contract text'}];seen=[]
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

    def test_visible_sketch_title_instructions_reach_model_across_window_boundary(self):
        pages=[page(n) for n in range(1,5)]+[
            {'pdf_page':5,'text':'Haustechnik\nDiele\nKundenwunsch-Skizze\nWohnen'},
            {'pdf_page':6,'text':'Bad\nKind 1\nKundenwunsch-Skizze\nSchlafen'}]
        before=copy.deepcopy(pages);seen=[]
        def model(*args):
            prompt=args[-1][0]['text'];data=payload(args[-1]);seen.append(data)
            self.assertIn('Use the visible document title',prompt)
            self.assertIn('Do not add inferred qualifiers',prompt)
            self.assertIn('visible document title',prompt)
            instructions=prompt.split('SOURCE DATA:\n')[0]
            self.assertNotIn('Kundenwunsch-Skizze',instructions)
            self.assertNotIn('Erdgeschoss',instructions)
            self.assertIn('Keep the document title stable across continuation pages',instructions)
            decisions=[]
            for p in data['pages']:
                title='Kundenwunsch-Skizze' if p['pdf_page']>=5 else 'Contract'
                item=decision(p,start=p['pdf_page'] in (1,5),title=title)
                if p['pdf_page']==5:item['evidence_quotes']=['Kundenwunsch-Skizze']
                decisions.append(item)
            return {'pages':decisions}
        result=execute(pages,model)
        self.assertEqual([len(w['pages']) for w in seen],[5,1])
        self.assertEqual(seen[1]['previous_document']['document_title'],'Kundenwunsch-Skizze')
        sketch=result['documents'][-1]
        self.assertEqual(sketch['document_title'],'Kundenwunsch-Skizze')
        self.assertEqual(sketch['pdf_pages'],[5,6])
        self.assertEqual(pages,before)

    def test_explicit_floor_in_visible_title_is_preserved(self):
        title='Kundenwunsch-Skizze Erdgeschoss'
        pages=[{'pdf_page':1,'text':title+'\nHaustechnik\nWohnen'}]
        result=execute(pages,lambda *args:{'pages':[decision(pages[0],start=True,title=title)]})
        self.assertEqual(result['documents'][0]['document_title'],title)
        self.assertEqual(result['documents'][0]['evidence_quotes'],[title])

    def test_missing_visible_title_can_remain_unknown_without_invention(self):
        pages=[{'pdf_page':1,'text':'Reference 749\nContinuation of supplied information'}]
        def model(*args):
            instructions=args[-1][0]['text'].split('SOURCE DATA:\n')[0]
            self.assertIn('document_title empty and set uncertain=true',instructions)
            return {'pages':[decision(pages[0],start=True,title='',uncertain=True)]}
        result=execute(pages,model)
        self.assertEqual(result['documents'][0]['document_title'],'')
        self.assertEqual(result['status'],'needs_review')
        self.assertEqual(result['documents'][0]['pdf_pages'],[1])

    def test_identity_boundaries_are_consistent_within_and_across_windows(self):
        # Prefixes move the same pair to pages 1/2, 4/5, 5/6, 10/11 and 25/26.
        cases=[('different_sender','Southworks Ltd','AUTH-A',True),
               ('different_reference','Northworks Ltd','AUTH-B',True),
               ('continuation','Northworks Ltd','AUTH-A',False)]
        for prefix in [0,3,4,9,24]:
            for label,second_sender,second_reference,split in cases:
                with self.subTest(prefix=prefix,case=label):
                    def form(n,sender,reference):
                        return {'pdf_page':n,'text':f'Authorization\nSender: {sender}\nReference: {reference}\nMentioned supplier: Otherworks Ltd\nSignature area'}
                    pages=[form(n,'Packaging Ltd','PACK') for n in range(1,prefix+1)]
                    pages += [form(prefix+1,'Northworks Ltd','AUTH-A'),
                              form(prefix+2,second_sender,second_reference)]
                    before=copy.deepcopy(pages);seen=[]
                    def model(*args):
                        prompt=args[-1][0]['text'];data=payload(args[-1]);seen.append(data)
                        self.assertIn('Matching titles alone do not establish continuation',prompt)
                        self.assertIn('A company merely mentioned in body text',prompt)
                        active=data['previous_document'];out=[]
                        for p in data['pages']:
                            fields=dict(line.split(': ',1) for line in p['text'].splitlines() if ': ' in line)
                            sender,reference=fields['Sender'],fields['Reference']
                            start=active is None or (sender,reference)!=(active['sender'],active['document_reference'])
                            out.append(decision(p,start=start,title='Authorization',sender=sender,document_reference=reference))
                            active={'sender':sender,'document_reference':reference}
                        return {'pages':out}
                    result=execute(pages,model)
                    self.assertEqual(result['status'],'proposed')
                    pair=[d for d in result['documents'] if d['pdf_pages'][0]>prefix]
                    expected=[[prefix+1],[prefix+2]] if split else [[prefix+1,prefix+2]]
                    self.assertEqual([d['pdf_pages'] for d in pair],expected)
                    self.assertEqual(pair[0]['sender'],'Northworks Ltd')
                    self.assertEqual(pair[0]['document_reference'],'AUTH-A')
                    for window in seen:
                        self.assertLessEqual(len(window['pages']),5)
                        previous=window['previous_document']
                        if previous:
                            n=window['pages'][0]['pdf_page']-1
                            self.assertEqual(previous['last_pdf_page'],n)
                            self.assertEqual(previous['last_page_text'],pages[n-1]['text'])
                            self.assertNotIn(n,[p['pdf_page'] for p in window['pages']])
                    if prefix in [4,9,24]:
                        previous=seen[-1]['previous_document']
                        self.assertEqual(previous['sender'],'Northworks Ltd')
                        self.assertEqual(previous['document_reference'],'AUTH-A')
                    self.assertEqual(pages,before)

    def test_identity_interpretation_is_flagged_but_invalid_field_types_are_rejected(self):
        first={'pdf_page':1,'text':'Authorization\nNorthworks Ltd\nAUTH-A'}
        second={'pdf_page':2,'text':'Authorization\nSouthworks Ltd\nAUTH-B'}
        valid={'pages':[decision(first,True,title='Authorization',sender='Northworks Ltd',document_reference='AUTH-A'),
                        decision(second,True,title='Authorization',sender='Southworks Ltd',document_reference='AUTH-B')]}
        self.assertEqual(validate_page_answer([first,second],valid,None),valid['pages'])
        bad=copy.deepcopy(valid);bad['pages'][1]['starts_new_document']=False
        reviewed=validate_page_answer([first,second],bad,None)
        self.assertIn('continuation_identity_change:2:sender',reviewed[1]['review_flags'])
        for field,value in [('sender',''),('sender',123),('document_reference',True)]:
            bad=copy.deepcopy(valid);bad['pages'][0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                validate_page_answer([first,second],bad,None)

    def test_blank_previous_page_preserves_identity_and_full_empty_context(self):
        pages=[{'pdf_page':1,'text':'Authorization\nNorthworks Ltd\nAUTH-A'}]
        pages += [{'pdf_page':n,'text':''} for n in range(2,6)]
        pages += [{'pdf_page':6,'text':'Continuation\nSupplier: Southworks Ltd'}]
        seen=[]
        def model(*args):
            data=payload(args[-1]);seen.append(data)
            return {'pages':[decision(p,start=p['pdf_page']==1,title='Authorization',
                                     sender='Northworks Ltd',document_reference='AUTH-A') for p in data['pages']]}
        result=execute(pages,model)
        previous=seen[1]['previous_document']
        self.assertEqual(previous['last_page_text'],'');self.assertEqual(previous['last_pdf_page'],5)
        self.assertEqual(previous['sender'],'Northworks Ltd');self.assertEqual(previous['document_reference'],'AUTH-A')
        self.assertEqual(len(result['documents']),1)
        self.assertEqual(result['documents'][0]['pdf_pages'],list(range(1,7)))

    def test_identity_can_be_learned_later_without_leaking_to_a_new_document(self):
        pages=[{'pdf_page':n,'text':'Authorization\nContinuation'} for n in range(1,7)]
        pages[3]['text']='Authorization\nNorthworks Ltd\nAUTH-A'
        pages[4]['text']='Separate agreement\nSignature area'
        seen=[]
        def model(*args):
            data=payload(args[-1]);seen.append(data);out=[]
            for p in data['pages']:
                n=p['pdf_page']
                out.append(decision(p,start=n in [1,5],title='Authorization' if n<5 else 'Separate agreement',
                    sender='Northworks Ltd' if n==4 else None,document_reference='AUTH-A' if n==4 else None))
            return {'pages':out}
        result=execute(pages,model)
        self.assertEqual(result['documents'][0]['sender'],'Northworks Ltd')
        self.assertEqual(result['documents'][0]['document_reference'],'AUTH-A')
        self.assertIsNone(seen[1]['previous_document']['sender'])
        self.assertIsNone(seen[1]['previous_document']['document_reference'])
        self.assertEqual(result['documents'][1]['pdf_pages'],[5,6])

    def test_changed_sender_without_boundary_is_flagged_without_retry_and_evidence_preserved(self):
        pages=[{'pdf_page':n,'text':'Authorization\nNorthworks Ltd\nAUTH-A'} for n in range(1,6)]
        pages.append({'pdf_page':6,'text':'Authorization\nSouthworks Ltd\nAUTH-B'})
        calls=[]
        def model(*args):
            prompt=args[-1][0]['text'];data=json.loads(prompt.split('SOURCE DATA:\n')[1].split('\nREJECTED RESPONSE:')[0]);calls.append(prompt)
            if len(calls)==1:
                return {'pages':[decision(p,start=p['pdf_page']==1,title='Authorization',sender='Northworks Ltd',document_reference='AUTH-A') for p in data['pages']]}
            return {'pages':[decision(data['pages'][0],start=len(calls)>2,title='Authorization',sender='Southworks Ltd',document_reference='AUTH-B')]}
        result=execute(pages,model)
        self.assertEqual([d['pdf_pages'] for d in result['documents']],[list(range(1,7))])
        self.assertEqual(len(calls),2)
        self.assertEqual(result['status'],'needs_review')
        self.assertIn('continuation_identity_change:6:sender',result['review_flags'])
        self.assertEqual(result['attempts'][1]['response']['pages'][0]['sender'],'Southworks Ltd')
        self.assertTrue(result['attempts'][1]['valid'])

    def test_full_previous_page_text_is_not_truncated(self):
        long_text='Report\n'+'Detailed source information.\n'*600+'Final source paragraph.'
        pages=[{'pdf_page':n,'text':long_text if n==5 else 'Report\nContinuation'} for n in range(1,7)]
        seen=[]
        def model(*args):
            data=payload(args[-1]);seen.append(data)
            return {'pages':[decision(p,start=p['pdf_page']==1,title='Report') for p in data['pages']]}
        result=execute(pages,model)
        self.assertEqual(result['status'],'proposed')
        self.assertEqual([len(w['pages']) for w in seen],[5,1])
        self.assertEqual(seen[1]['previous_document']['last_page_text'],long_text)
        self.assertTrue(seen[1]['previous_document']['last_page_text'].endswith('Final source paragraph.'))

    def test_covering_letter_and_quotation_split_but_quotation_pages_continue(self):
        for prefix in [0,3,4,8,9,24]:
            for same_sender in [False,True]:
                with self.subTest(prefix=prefix,same_sender=same_sender):
                    broker='Stonefield Sales' if same_sender else 'Westfield Brokerage'
                    supplier='Stonefield Sales';reference='Q-73'
                    pages=[{'pdf_page':n,'text':'Administrative notice\nRecords office\nPACK-1'} for n in range(1,prefix+1)]
                    pages += [
                        {'pdf_page':prefix+1,'text':f'Letter\nDear customer,\nPlease find the enclosed quotation {reference}.\nActing on behalf of {supplier}.\nYours sincerely,\n{broker}'},
                        {'pdf_page':prefix+2,'text':f'Quotation\n{supplier}\nReference: {reference}\nContact: {broker}\nItem 1: Work package\nCarry forward: 100.00'},
                        {'pdf_page':prefix+3,'text':f'Quotation\n{supplier}\nReference: {reference}\nContact: {broker}\nBrought forward: 100.00\nItem 2: Work package\nTotal: 200.00'}]
                    before=copy.deepcopy(pages);seen=[]
                    def model(*args):
                        prompt=args[-1][0]['text'];data=payload(args[-1]);seen.append(data)
                        self.assertIn('A covering letter and its enclosed quotation are separate documents',prompt)
                        self.assertIn('Continuing quotation pages remain together',prompt)
                        active=data['previous_document'];out=[]
                        for p in data['pages']:
                            if p['text'].startswith('Letter'):
                                kind,sender,represented,ref,title='covering_letter',broker,supplier,reference,'Letter'
                            elif p['text'].startswith('Quotation'):
                                kind,sender,represented,ref,title='quotation',supplier,None,reference,'Quotation'
                            else:
                                kind,sender,represented,ref,title='notice','Records office',None,'PACK-1','Administrative notice'
                            start=active is None or any(active[k]!=v for k,v in
                                [('sender',sender),('represented_organization',represented),('document_type',kind),('document_reference',ref)])
                            out.append(decision(p,start=start,title=title,sender=sender,
                                represented_organization=represented,document_type=kind,document_reference=ref))
                            active=out[-1]
                        return {'pages':out}
                    result=execute(pages,model)
                    self.assertEqual(result['status'],'proposed')
                    docs=[d for d in result['documents'] if d['pdf_pages'][0]>prefix]
                    self.assertEqual([d['pdf_pages'] for d in docs],[[prefix+1],[prefix+2,prefix+3]])
                    self.assertEqual([d['document_type'] for d in docs],['covering_letter','quotation'])
                    self.assertEqual(docs[0]['sender'],broker)
                    self.assertEqual(docs[0]['represented_organization'],supplier)
                    self.assertEqual(docs[1]['sender'],supplier)
                    self.assertIsNone(docs[1]['represented_organization'])
                    self.assertEqual([d['document_reference'] for d in docs],[reference,reference])
                    for window in seen:
                        self.assertLessEqual(len(window['pages']),5)
                        previous=window['previous_document']
                        if previous:
                            n=window['pages'][0]['pdf_page']-1
                            self.assertEqual(previous['last_page_text'],pages[n-1]['text'])
                            self.assertLessEqual(len(previous['identity_evidence']),4)
                    for doc in docs:
                        for evidence in doc['identity_evidence']:
                            self.assertIn(evidence['quote'],pages[evidence['pdf_page']-1]['text'])
                    self.assertEqual(pages,before)

    def test_type_change_is_flagged_even_with_same_sender_and_reference(self):
        letter={'pdf_page':1,'text':'Dear customer,\nPlease find enclosed quotation Q-73.\nYours sincerely,\nStonefield Sales'}
        quote={'pdf_page':2,'text':'Quotation\nStonefield Sales\nQ-73\nItem 1: Work package'}
        first=decision(letter,True,title='Letter',sender='Stonefield Sales',document_type='covering_letter',document_reference='Q-73')
        wrong=decision(quote,False,title='Letter',sender='Stonefield Sales',document_type='quotation',document_reference='Q-73')
        reviewed=validate_page_answer([letter,quote],{'pages':[first,wrong]},None)
        self.assertIn('continuation_identity_change:2:document_type',reviewed[1]['review_flags'])
        fixed=copy.deepcopy(wrong);fixed['starts_new_document']=True;fixed['document_title']='Quotation';fixed['evidence_quotes']=['Quotation']
        validate_page_answer([letter,quote],{'pages':[first,fixed]},None)

    def test_cross_window_type_change_preserves_proposal_and_flags_without_retry(self):
        pages=[{'pdf_page':n,'text':'Dear customer,\nPlease find enclosed quotation Q-73.\nYours sincerely,\nStonefield Sales'} for n in range(1,6)]
        pages += [{'pdf_page':n,'text':'Quotation\nStonefield Sales\nQ-73\nItemized work'} for n in [6,7]]
        calls=[]
        def model(*args):
            prompt=args[-1][0]['text'];data=json.loads(prompt.split('SOURCE DATA:\n')[1].split('\nREJECTED RESPONSE:')[0]);calls.append(prompt)
            if len(calls)==1:
                return {'pages':[decision(p,start=p['pdf_page']==1,title='Letter',sender='Stonefield Sales',document_type='covering_letter',document_reference='Q-73') for p in data['pages']]}
            fixed=len(calls)>2
            return {'pages':[decision(p,start=fixed and p['pdf_page']==6,title='Quotation' if fixed else 'Letter',
                sender='Stonefield Sales',document_type='quotation',document_reference='Q-73') for p in data['pages']]}
        result=execute(pages,model)
        self.assertEqual([d['pdf_pages'] for d in result['documents']],[list(range(1,8))])
        self.assertEqual(len(calls),2)
        self.assertIn('continuation_identity_change:6:document_type',result['review_flags'])
        self.assertIn('continuation_identity_change:7:document_type',result['review_flags'])
        self.assertFalse(result['attempts'][1]['response']['pages'][0]['starts_new_document'])
        self.assertTrue(result['attempts'][1]['valid'])

    def test_identity_evidence_is_field_specific_local_and_unknown_roles_stay_null(self):
        p={'pdf_page':1,'text':'Dear customer,\nReference: Q-73\nCooperation partner: Stonefield Sales\nYours sincerely,\nWestfield Brokerage'}
        valid=decision(p,True,title='Letter',sender='Westfield Brokerage',document_type='covering_letter',document_reference='Q-73')
        result=execute([p],lambda *a:{'pages':[valid]})
        self.assertIsNone(result['documents'][0]['represented_organization'])
        self.assertNotIn('issuer',result['documents'][0])
        variants=[]
        for field,value in [('identity_evidence',[]),('sender','Unknown Brokerage')]:
            bad=copy.deepcopy(valid);bad[field]=value
            self.assertTrue(validate_page_answer([p],{'pages':[bad]},None)[0]['review_flags'])
        bad=copy.deepcopy(valid);bad['document_type']='unknown-category';variants.append(bad)
        bad=copy.deepcopy(valid);bad['identity_evidence'][0]['quote']='Quotation from another page';variants.append(bad)
        bad=copy.deepcopy(valid);bad['identity_evidence'][0]['field']='recipient';variants.append(bad)
        bad=copy.deepcopy(valid);bad['identity_evidence'][0]['quote']='Dear customer,'
        self.assertIn('identity_evidence_insufficient:1:sender',validate_page_answer([p],{'pages':[bad]},None)[0]['review_flags'])
        for bad in variants:
            with self.subTest(answer=bad),self.assertRaises(ValueError):
                validate_page_answer([p],{'pages':[bad]},None)

    def test_identity_evidence_context_does_not_grow_with_document_history(self):
        pages=[{'pdf_page':n,'text':f'Quotation\nStonefield Sales\nQ-73\nItem {n}: Work package'} for n in range(1,17)]
        seen=[]
        def model(*args):
            data=payload(args[-1]);seen.append(data)
            return {'pages':[decision(p,start=p['pdf_page']==1,title='Quotation',sender='Stonefield Sales',
                document_type='quotation',document_reference='Q-73') for p in data['pages']]}
        result=execute(pages,model)
        self.assertEqual([len(w['pages']) for w in seen],[5,5,5,1])
        self.assertTrue(all(len(w['previous_document']['identity_evidence'])==3 for w in seen[1:]))
        self.assertEqual(len(result['documents'][0]['identity_evidence']),16*3)
        for window in seen[1:]:
            previous=window['previous_document']
            self.assertEqual(previous['last_page_text'],pages[previous['last_pdf_page']-1]['text'])
            self.assertEqual(previous['document_type'],'quotation')

    def test_quotation_title_page_is_not_treated_as_a_covering_letter(self):
        pages=[{'pdf_page':1,'text':'Quotation\nStonefield Sales\nQ-73\nPrepared for the customer'},
               {'pdf_page':2,'text':'Item 1: Work package\nTotal: 200.00'}]
        result=execute(pages,lambda *args:{'pages':[decision(p,start=p['pdf_page']==1,
            title='Quotation',sender='Stonefield Sales',document_reference='Q-73',document_type='quotation') for p in pages]})
        self.assertEqual(result['status'],'proposed')
        self.assertEqual(len(result['documents']),1)
        self.assertEqual(result['documents'][0]['pdf_pages'],[1,2])
        self.assertEqual(result['documents'][0]['document_type'],'quotation')

    def test_brand_markers_do_not_break_roles_or_cross_window_evidence(self):
        for marker in ('®','™','℠'):
            with self.subTest(marker=marker):
                pages=[{'pdf_page':n,'text':f'Quotation\nStonefield{marker}\nSales GmbH\nQ-73'} for n in range(1,7)]
                before=copy.deepcopy(pages);seen=[]
                def model(*args):
                    data=payload(args[-1]);seen.append(data)
                    return {'pages':[decision(p,start=p['pdf_page']==1,title='Quotation',
                        sender='Stonefield Sales GmbH' if p['pdf_page']<=5 else f'Stonefield{marker} Sales GmbH',
                        represented_organization='Stonefield Sales GmbH',
                        identity_evidence=[{'field':field,'quote':p['text']} for field in ('sender','represented_organization')])
                        for p in data['pages']]}
                result=execute(pages,model)
                self.assertEqual(result['status'],'proposed')
                self.assertEqual(len(result['attempts']),2)
                self.assertEqual(result['documents'][0]['pdf_pages'],list(range(1,7)))
                self.assertEqual(len(seen[1]['previous_document']['identity_evidence']),2)
                self.assertEqual(pages,before)
                for evidence in result['documents'][0]['identity_evidence']:
                    self.assertEqual(evidence['quote'],pages[evidence['pdf_page']-1]['text'])

    def test_brand_normalization_keeps_quotes_exact_and_flags_different_entities(self):
        p={'pdf_page':1,'text':'Quotation\nStonefield® Sales GmbH\nQ-73'}
        valid=decision(p,start=True,sender='Stonefield Sales GmbH',
            identity_evidence=[{'field':'sender','quote':'Stonefield® Sales GmbH'}])
        validate_page_answer([p],{'pages':[valid]},None)
        for sender in ('Stonefield Building GmbH','Stonefield Sales AG','®'):
            bad=copy.deepcopy(valid);bad['sender']=sender
            with self.subTest(sender=sender):
                self.assertIn('identity_evidence_insufficient:1:sender',validate_page_answer([p],{'pages':[bad]},None)[0]['review_flags'])
        bad=copy.deepcopy(valid);bad['identity_evidence'][0]['quote']='Stonefield Sales GmbH'
        with self.assertRaisesRegex(ValueError,'exact text'):
            validate_page_answer([p],{'pages':[bad]},None)
        following={'pdf_page':2,'text':'Continuation'}
        previous={'document_title':'Contract','sender':'Stonefield® Sales GmbH'}
        reviewed=validate_page_answer([following],{'pages':[decision(following,sender='Stonefield Building GmbH')]},previous)
        self.assertIn('continuation_identity_change:2:sender',reviewed[0]['review_flags'])

    def test_document_references_keep_trademark_markers_and_punctuation(self):
        for printed,returned in (('Q®73','Q73'),('Q-73','Q73'),('Q/73','Q-73')):
            with self.subTest(printed=printed,returned=returned):
                p={'pdf_page':1,'text':'Quotation\n'+printed}
                bad=decision(p,start=True,document_reference=returned,
                    identity_evidence=[{'field':'document_reference','quote':printed}])
                reviewed=validate_page_answer([p],{'pages':[bad]},None)
                self.assertIn('identity_evidence_insufficient:1:document_reference',reviewed[0]['review_flags'])
                previous={'document_title':'Contract','document_reference':printed}
                reviewed=validate_page_answer([p],{'pages':[decision(p,document_reference=returned)]},previous)
                self.assertIn('continuation_identity_change:1:document_reference',reviewed[0]['review_flags'])

    def test_semantic_findings_keep_other_documents_and_raw_responses_across_windows(self):
        pages=[{'pdf_page':n,'text':'Report\nNorthworks Ltd'} for n in range(1,12)]
        seen=[];answers=[];originals=[]
        def model(*args):
            data=payload(args[-1]);seen.append(data);items=[]
            for p in data['pages']:
                n=p['pdf_page']
                item=decision(p,start=n in (1,8),title='Report' if n<=5 else 'Changed title',
                              sender='Northworks Ltd' if n<=5 else 'Different Ltd')
                if n==8:item['evidence_quotes']=[];item['reason']=''
                items.append(item)
            answer={'pages':items};answers.append(answer);originals.append(copy.deepcopy(answer));return answer
        before=copy.deepcopy(pages);result=execute(pages,model)
        self.assertEqual(len(seen),3)
        self.assertEqual(result['status'],'needs_review')
        self.assertEqual([d['pdf_pages'] for d in result['documents']],[list(range(1,8)),list(range(8,12))])
        self.assertIn('continuation_title_change:6',result['review_flags'])
        self.assertIn('continuation_identity_change:6:sender',result['review_flags'])
        self.assertIn('identity_evidence_insufficient:8:sender',result['review_flags'])
        self.assertIn('boundary_justification_missing:8',result['review_flags'])
        self.assertIn('boundary_justification_missing:8',seen[2]['previous_document']['review_flags'])
        self.assertTrue(set(seen[2]['previous_document']['review_flags']).issubset(result['documents'][1]['review_flags']))
        self.assertTrue(seen[2]['previous_document']['uncertain'])
        self.assertEqual(pages,before);self.assertEqual(answers,originals)
        self.assertEqual([a['response'] for a in result['attempts']],originals)
        self.assertTrue(all(a['valid'] for a in result['attempts']))

    def test_missing_title_and_initial_boundary_and_empty_page_proposals_are_reviewed(self):
        pages=[page(1),page(2,empty=True),page(3)]
        answer={'pages':[decision(pages[0],title=''),decision(pages[1],start=True,title='Invented'),
                         decision(pages[2],title='')]}
        before=copy.deepcopy(answer)
        result=execute(pages,lambda *a:answer)
        self.assertEqual(result['status'],'needs_review')
        self.assertEqual(result['documents'][0]['pdf_pages'],[1,2,3])
        for flag in ('missing_initial_boundary:1','document_title_missing:1','empty_page_boundary:2'):
            self.assertIn(flag,result['review_flags'])
        self.assertEqual(answer,before)
        self.assertFalse(result['attempts'][0]['response']['pages'][0]['starts_new_document'])
        self.assertTrue(result['attempts'][0]['response']['pages'][1]['starts_new_document'])
        self.assertTrue(result['page_decisions'][0]['starts_new_document'])
        self.assertFalse(result['page_decisions'][1]['starts_new_document'])

    def test_forms_select_issuer_instead_of_signer_or_recipient(self):
        p={'pdf_page':1,'text':'Consent form\nIssued by Cedar Services Ltd\nAddressed to: Taylor River\nSignature: Morgan Lake'}
        roles=[{'name':name,'role':role,'quote':quote,'explanation':explanation} for name,role,quote,explanation in (
            ('Cedar Services Ltd','issuer','Issued by Cedar Services Ltd','The form identifies its issuing company.'),
            ('Taylor River','recipient','Addressed to: Taylor River','The address label identifies the recipient.'),
            ('Morgan Lake','signer','Signature: Morgan Lake','The signature field names the intended signer.'))]
        raw=decision(p,True,title='Consent form',document_type='consent_form',sender='Morgan Lake',entity_roles=roles)
        before=copy.deepcopy(raw);result=execute([p],lambda *a:{'pages':[raw]})
        self.assertEqual(result['documents'][0]['sender'],'Cedar Services Ltd')
        self.assertIn('identity_classification_conflict:1:sender',result['review_flags'])
        self.assertEqual(result['attempts'][0]['response']['pages'][0],before)
        self.assertEqual(raw,before)
        self.assertEqual({e['role'] for e in result['documents'][0]['entity_roles']},{'issuer','recipient','signer'})
        evidence=[e for e in result['documents'][0]['identity_evidence'] if e['field']=='sender']
        self.assertTrue(any(e['explanation']=='The form identifies its issuing company.' for e in evidence))

    def test_declarants_and_recipients_do_not_establish_unknown_issuer(self):
        for role in ('signer','declarant','recipient','contact','cooperation_partner'):
            p={'pdf_page':1,'text':'Declaration\nMorgan Lake\nPlease complete the form.'}
            entry={'name':'Morgan Lake','role':role,'quote':'Morgan Lake',
                   'explanation':'This names a participant; the issuing organization is not established.'}
            raw=decision(p,True,title='Declaration',document_type='form',sender='Morgan Lake',entity_roles=[entry])
            result=execute([p],lambda *a:{'pages':[raw]})
            with self.subTest(role=role):
                self.assertIsNone(result['documents'][0]['sender'])
                self.assertIn('identity_classification_conflict:1:sender',result['review_flags'])
                self.assertEqual(len(result['attempts']),1)

    def test_letters_select_author_and_cooperation_does_not_become_representation(self):
        for explicit in (False,True):
            phrase='Acting on behalf of' if explicit else 'In cooperation with'
            p={'pdf_page':1,'text':f'Letter\nFrom: Alex Brook\n{phrase} Cedar Services Ltd\nTo: Jamie Woods'}
            roles=[{'name':'Alex Brook','role':'correspondence_sender','quote':'From: Alex Brook',
                    'explanation':'The letter identifies its author.'},
                   {'name':'Cedar Services Ltd','role':'represented_organization' if explicit else 'cooperation_partner',
                    'quote':phrase+' Cedar Services Ltd','explanation':'The relationship is explicitly stated.'},
                   {'name':'Jamie Woods','role':'recipient','quote':'To: Jamie Woods','explanation':'The addressee is identified.'}]
            raw=decision(p,True,title='Letter',document_type='letter',sender='Alex Brook',
                represented_organization='Cedar Services Ltd',entity_roles=roles)
            result=execute([p],lambda *a:{'pages':[raw]})
            with self.subTest(explicit=explicit):
                self.assertEqual(result['documents'][0]['sender'],'Alex Brook')
                self.assertEqual(result['documents'][0]['represented_organization'],'Cedar Services Ltd' if explicit else None)
                self.assertEqual(result['status'],'proposed' if explicit else 'needs_review')

    def test_only_own_identifiers_populate_document_reference_without_date_pattern_rules(self):
        kinds=('related_document_identifier','issue_date','related_document_date','revision_date',
               'customer_number','account_number','page_number','other','document_identifier')
        for kind in kinds:
            # The same printed value may legitimately identify a document or be a date.
            value='2040-05-06';p={'pdf_page':1,'text':f'Notice\n{kind}: {value}'}
            reference={'value':value,'kind':kind,'quote':f'{kind}: {value}',
                       'explanation':f'The label establishes {kind}.'}
            raw=decision(p,True,title='Notice',document_type='notice',document_reference=value,references=[reference])
            result=execute([p],lambda *a:{'pages':[raw]})
            with self.subTest(kind=kind):
                self.assertEqual(result['documents'][0]['document_reference'],value if kind=='document_identifier' else None)
                self.assertEqual(result['documents'][0]['references'][0],{**reference,'pdf_page':1})
                self.assertEqual(result['status'],'proposed' if kind=='document_identifier' else 'needs_review')
                self.assertEqual(len(result['attempts']),1)

    def test_multiple_eligible_candidates_stay_unknown_and_reviewable(self):
        p={'pdf_page':1,'text':'Quotation\nCedar Services Ltd\nPine Services Ltd\nQ-71\nQ-72'}
        roles=[{'name':name,'role':'issuer','quote':name,'explanation':'Possible issuing company.'}
               for name in ('Cedar Services Ltd','Pine Services Ltd')]
        refs=[{'value':value,'kind':'document_identifier','quote':value,'explanation':'Possible primary identifier.'}
              for value in ('Q-71','Q-72')]
        raw=decision(p,True,title='Quotation',document_type='quotation',entity_roles=roles,references=refs)
        result=execute([p],lambda *a:{'pages':[raw]})
        self.assertIsNone(result['documents'][0]['sender']);self.assertIsNone(result['documents'][0]['document_reference'])
        for field in ('sender','document_reference'):
            self.assertIn(f'identity_candidates_ambiguous:1:{field}',result['review_flags'])
        self.assertEqual(len(result['attempts']),1)

    def test_classifications_and_explanations_cross_windows_without_inheriting_new_documents(self):
        pages=[{'pdf_page':n,'text':('Quotation\nIssuer: Cedar Services Ltd\nQuote no: Q-71\nRelated agreement: C-22\nAgreement date: 2040-05-06'
                                  if n==1 else 'Consent form\nSignature: Morgan Lake' if n==7 else 'Continuing items')}
               for n in range(1,8)]
        seen=[]
        def model(*args):
            data=payload(args[-1]);seen.append(data);items=[]
            for p in data['pages']:
                n=p['pdf_page'];roles=[];refs=[]
                if n==1:
                    roles=[{'name':'Cedar Services Ltd','role':'issuer','quote':'Issuer: Cedar Services Ltd',
                            'explanation':'Explicit issuer label.'}]
                    refs=[{'value':value,'kind':kind,'quote':quote,'explanation':'The label identifies its relationship.'}
                          for value,kind,quote in (('Q-71','document_identifier','Quote no: Q-71'),
                          ('C-22','related_document_identifier','Related agreement: C-22'),
                          ('2040-05-06','related_document_date','Agreement date: 2040-05-06'))]
                if n==7:roles=[{'name':'Morgan Lake','role':'signer','quote':'Signature: Morgan Lake','explanation':'Intended signer.'}]
                items.append(decision(p,start=n in (1,7),title='Consent form' if n==7 else 'Quotation',
                    document_type='consent_form' if n==7 else 'quotation',
                    sender='Cedar Services Ltd' if n<7 else None,document_reference='Q-71' if n<7 else None,
                    entity_roles=roles,references=refs))
            return {'pages':items}
        result=execute(pages,model)
        self.assertEqual([len(w['pages']) for w in seen],[5,2])
        previous=seen[1]['previous_document']
        self.assertEqual(previous['last_page_text'],pages[4]['text'])
        self.assertEqual(previous['sender'],'Cedar Services Ltd');self.assertEqual(previous['document_reference'],'Q-71')
        self.assertEqual(previous['entity_roles'][0]['pdf_page'],1)
        self.assertEqual({r['kind'] for r in previous['references']},
                         {'document_identifier','related_document_identifier','related_document_date'})
        self.assertIsNone(result['documents'][1]['sender']);self.assertIsNone(result['documents'][1]['document_reference'])
        self.assertEqual(result['documents'][1]['references'],[])
        self.assertEqual([d['pdf_pages'] for d in result['documents']],[list(range(1,7)),[7]])

    def test_classification_structure_and_quotes_are_hard_errors_but_explanations_are_reviewed(self):
        p={'pdf_page':1,'text':'Report\nIssuer: Cedar Services Ltd\nNumber: R-12'}
        raw=decision(p,True,title='Report',sender='Cedar Services Ltd',document_reference='R-12')
        for key in ('entity_roles','references'):
            for mutation in ('wrong_type','wrong_category','missing_key','foreign_quote','bad_explanation_type'):
                bad=copy.deepcopy(raw)
                entry=bad[key][0]
                if mutation=='wrong_type':bad[key]={}
                elif mutation=='wrong_category':entry['role' if key=='entity_roles' else 'kind']='unsupported'
                elif mutation=='missing_key':del entry['explanation']
                elif mutation=='foreign_quote':entry['quote']='Text from a different page'
                else:entry['explanation']=42
                with self.subTest(key=key,mutation=mutation),self.assertRaises(ValueError):
                    validate_page_answer([p],{'pages':[bad]},None)
            bad=copy.deepcopy(raw);bad[key][0]['explanation']=''
            result=execute([p],lambda *a:{'pages':[bad]})
            self.assertIn(f'classification_explanation_missing:1:{key}',result['review_flags'])
            self.assertEqual(len(result['attempts']),1)

    def test_complete_visible_title_keeps_date_without_body_topic_across_windows(self):
        title='Supplement to agreement dated 2042-03-18'
        pages=[{'pdf_page':n,'text':'Quotation\nContinuing items'} for n in range(1,5)]
        pages += [{'pdf_page':5,'text':title+'\nThe customer requests an early design meeting.\nSignature'},
                  {'pdf_page':6,'text':'Details of the requested design meeting\nContinued obligations'}]
        seen=[]
        def model(*args):
            prompt=args[-1][0]['text'];data=payload(args[-1]);seen.append(data)
            instructions=prompt.split('SOURCE DATA:\n')[0]
            self.assertIn('Treat document_title as a transcription',instructions)
            self.assertIn('including dates or identifiers that are printed as part of that title',instructions)
            self.assertIn('do not append punctuation, subtitles, topics or',instructions)
            self.assertIn('Check the final title against that passage',instructions)
            for specific in ('Widerrufsrecht','Anlage zum Bauvertrag','13.06.2025'):
                self.assertNotIn(specific,instructions)
            return {'pages':[decision(p,start=p['pdf_page'] in (1,5),
                title=title if p['pdf_page']>=5 else 'Quotation') for p in data['pages']]}
        result=execute(pages,model)
        self.assertEqual([len(w['pages']) for w in seen],[5,1])
        self.assertEqual(seen[1]['previous_document']['document_title'],title)
        self.assertEqual(result['documents'][1]['document_title'],title)
        self.assertEqual(result['documents'][1]['pdf_pages'],[5,6])
        self.assertNotIn('early design meeting',result['documents'][1]['document_title'])

    def test_title_policy_does_not_split_drawings_attached_to_quotation(self):
        pages=[{'pdf_page':n,'text':text} for n,text in enumerate(
            ['Quotation\nQ-81\nItem 1','Quotation\nQ-81\nItem 2',
             'Ground floor\nDrawing attached to quotation Q-81',
             'Upper floor\nDrawing attached to quotation Q-81',
             'Cross section\nDrawing attached to quotation Q-81',
             'Elevations\nDrawing attached to quotation Q-81'],1)]
        seen=[]
        def model(*args):
            self.assertIn('keep\nthose integral drawing attachments within the quotation',args[-1][0]['text'])
            self.assertIn('a shared project alone',args[-1][0]['text'])
            data=payload(args[-1]);seen.append(data)
            return {'pages':[decision(p,start=p['pdf_page']==1,title='Quotation',document_type='quotation',
                document_reference='Q-81',uncertain=p['pdf_page']>=3) for p in data['pages']]}
        result=execute(pages,model)
        self.assertEqual([len(w['pages']) for w in seen],[5,1])
        self.assertEqual(len(result['documents']),1)
        self.assertEqual(result['documents'][0]['pdf_pages'],list(range(1,7)))
        self.assertEqual(result['documents'][0]['document_title'],'Quotation')

    def test_application_uses_literal_title_quote_instead_of_generated_body_summary(self):
        quote='Supplement to agreement\n dated 2043-08-19'
        p={'pdf_page':1,'text':quote+'\nThe client requests early design work.\nSignature'}
        raw=decision(p,True,title='Supplement to agreement - Early design work',title_quote=quote)
        before=copy.deepcopy(raw);result=execute([p],lambda *a:{'pages':[raw]})
        doc=result['documents'][0]
        self.assertEqual(doc['document_title'],'Supplement to agreement dated 2043-08-19')
        self.assertEqual(doc['title_evidence'],{'pdf_page':1,'quote':quote})
        self.assertIn('title_transcription_conflict:1',result['review_flags'])
        self.assertEqual(result['attempts'][0]['response']['pages'][0],before)
        self.assertEqual(raw,before);self.assertEqual(len(result['attempts']),1)

    def test_no_title_quote_does_not_publish_an_invented_title(self):
        p={'pdf_page':1,'text':'I request early design work.\nSignature'}
        raw=decision(p,True,title='Request for early design work',title_quote=None)
        result=execute([p],lambda *a:{'pages':[raw]})
        self.assertEqual(result['documents'][0]['document_title'],'')
        self.assertIsNone(result['documents'][0]['title_evidence'])
        self.assertIn('document_title_missing:1',result['review_flags'])
        self.assertEqual(result['attempts'][0]['response']['pages'][0]['document_title'],'Request for early design work')

    def test_title_quote_must_be_exact_and_local_and_canonical_title_crosses_windows(self):
        visible='Quotation – Project A, revision 2'
        pages=[{'pdf_page':n,'text':visible if n==1 else 'Drawing attachment\nWindow details'} for n in range(1,7)]
        seen=[]
        def model(*args):
            data=payload(args[-1]);seen.append(data)
            return {'pages':[decision(p,start=p['pdf_page']==1,title='Quotation – Generated description',
                title_quote=visible if p['pdf_page']==1 else None) for p in data['pages']]}
        result=execute(pages,model)
        self.assertEqual(seen[1]['previous_document']['document_title'],visible)
        self.assertEqual(seen[1]['previous_document']['title_evidence'],{'pdf_page':1,'quote':visible})
        self.assertEqual(result['documents'][0]['pdf_pages'],list(range(1,7)))
        self.assertEqual(result['documents'][0]['document_title'],visible)
        for bad_quote in (42,'','Quotation - Project A, revision 2','Different page title'):
            raw=decision(pages[0],True,title=visible);raw['title_quote']=bad_quote
            with self.subTest(quote=bad_quote),self.assertRaisesRegex(ValueError,'title_quote'):
                validate_page_answer([pages[0]],{'pages':[raw]},None)
