import copy
import json
from dataclasses import replace
from types import SimpleNamespace
import unittest

from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.project_flow import Document
from bauvo_working_units.project_facts import collect_project_facts, document_facts_view
from bauvo_working_units.timing_stage import extract_timing


class TimingFactsContextTests(unittest.TestCase):
    def setUp(self):
        self.quote = 'Die Zahlungen sind innerhalb von zehn Tagen nach Zugang der Herstellungsmitteilung zu leisten.'
        self.doc = Document('D1', 'Contract', 'contract.pdf', schema_version=6,
                   source_key='source-a', source_snapshot='snapshot', pages=tuple(
                   {'pdf_page': n, 'text': self.quote if n == 1 else
                    'Die zweite Rate folgt nach Herstellung der Bodenplatte.' if n == 5 else 'Andere Angaben.'}
                   for n in range(1, 6)))
        def earlier(prompt):
            data = json.loads(prompt.split('SOURCE DATA:\n')[1])
            if prompt.startswith('PAGE_EXTRACTION.'):
                rows = [{'name':'Zweite Rate zahlen','action':'Zweite Rate zahlen','actor':'Bauherr',
                         'performed_by':None,'citations':[{'pdf_page':5,'quote':self.doc.pages[4]['text']}]}]
                return {'working_units':rows if 5 in data['primary_pages'] else [],'carry_forward':''}
            return {'offers':[]}
        self.baseline = extract_page_units([self.doc], SimpleNamespace(extract=earlier),stop_after='PAGE_OFFERS')
        self.facts = collect_project_facts([self.doc],SimpleNamespace(extract=self.fact_response))

    def fact_response(self, prompt):
        pages = json.loads(prompt.split('SOURCE DATA:\n')[1])['source_pages']
        rows = [{'verpflichtung':p['text'],'zitate':[{'pdf_page':p['pdf_page'],'text':p['text']}]} for p in pages
                if p['pdf_page'] == 1]
        return {'verpflichtungen':rows}

    def timing_response(self, data, citation=None):
        rule = {'kind':'EVENT_RELATIVE','scope':'Zweite Rate zahlen','expression':'zehn Tage nach Zugang',
                'anchor_events':['Zugang der Herstellungsmitteilung'],'relation':'WITHIN_AFTER','offset':'zehn Tage',
                'duration':None,'date':None,'conditions':[],'alternative_group':None,'calculation':None,
                'citations':[citation or {'pdf_page':1,'quote':self.quote}]}
        return {'assessments':[{'unit_id':data['working_units'][0]['id'],'status':'EXPLICIT',
                                'timing_rules':[rule],'missing_evidence':[]}]}

    def test_same_document_fact_outside_window_is_sent_and_can_be_cited(self):
        for qualified in (False, True):
            with self.subTest(qualified=qualified):
                original = copy.deepcopy(self.baseline); facts_before = copy.deepcopy(self.facts)
                def timing(prompt):
                    self.assertTrue(prompt.startswith('PAGE_TIMING.'))
                    data = json.loads(prompt.split('SOURCE DATA:\n')[1])
                    self.assertEqual([p['pdf_page'] for p in data['source_pages']],[3,4,5])
                    self.assertEqual(len(data['project_facts']['facts']),1)
                    self.assertEqual(data['project_facts']['facts'][0]['gemini']['verpflichtung'],self.quote)
                    citation = {'pdf_page':1,'quote':self.quote}
                    if qualified: citation.update(source_key='source-a',document_id='D1')
                    return self.timing_response(data,citation)
                result = extract_timing(self.baseline,[self.doc],SimpleNamespace(extract=timing),project_facts=self.facts)
                self.assertEqual(result['review_findings'],[])
                self.assertEqual(result['project_facts'],facts_before)
                self.assertEqual(result['working_units'][0]['gemini'],original['working_units'][0]['gemini'])
                self.assertEqual(self.baseline,original);self.assertEqual(self.facts,facts_before)

    def test_filter_uses_source_and_document_identity_not_document_id_alone(self):
        other_doc = replace(self.doc,id='D2')
        other_source = replace(self.doc,source_key='source-b')
        collection = collect_project_facts([self.doc,other_doc,other_source],SimpleNamespace(extract=self.fact_response))
        before = copy.deepcopy(collection)
        view = document_facts_view(collection,self.doc)
        self.assertEqual(len(view['facts']),1)
        self.assertEqual({(f['source_key'],f['document_id']) for f in view['facts']},{('source-a','D1')})
        self.assertEqual(len(view['source_snapshots']),1)
        self.assertEqual(collection,before)

    def test_replacement_preserves_old_evidence_separately_and_keeps_current_uncertainty(self):
        old = {'fact_id':'old-fact','code':'citation_quote_not_found'}
        offer = {'code':'unresolved_offer_interpretation'}
        self.baseline['review_findings'] += [old,offer]
        issue = {'fact_id':self.facts['facts'][0]['id'],'code':'unresolved_fact_interpretation'}
        self.facts['facts'][0]['review_findings'].append(issue)
        self.facts['review_findings'].append(issue);self.facts['status']='needs_review'
        def timing(prompt):
            data=json.loads(prompt.split('SOURCE DATA:\n')[1])
            self.assertIn(issue,data['project_facts']['facts'][0]['review_findings'])
            return self.timing_response(data)
        result=extract_timing(self.baseline,[self.doc],SimpleNamespace(extract=timing),project_facts=self.facts)
        self.assertEqual(result['previous_project_facts'],self.baseline['project_facts'])
        self.assertEqual(result['previous_fact_review_findings'],[old])
        self.assertIn(issue,result['review_findings']);self.assertIn(offer,result['review_findings'])
        self.assertNotIn(old,result['review_findings']);self.assertEqual(result['status'],'needs_review')

    def test_changed_fact_sources_are_rejected_before_model_calls(self):
        def fail(prompt):self.fail('Source validation must run before any model call')
        changed=copy.deepcopy(self.facts);changed['source_snapshots'][0]['pages_sha256']='changed'
        with self.assertRaises(ValueError):
            extract_timing(self.baseline,[self.doc],SimpleNamespace(extract=fail),project_facts=changed)

    def test_another_documents_quote_does_not_become_valid_evidence(self):
        def timing(prompt):
            data=json.loads(prompt.split('SOURCE DATA:\n')[1])
            return self.timing_response(data,{'pdf_page':1,'quote':self.quote,
                                               'source_key':'source-a','document_id':'D2'})
        result=extract_timing(self.baseline,[self.doc],SimpleNamespace(extract=timing),project_facts=self.facts)
        self.assertTrue(any(f['code']=='citation_page_outside_window' for f in result['review_findings']))

    def test_contract_facts_support_blb_timing_with_source_qualified_citations(self):
        blb = replace(self.doc, source='blb.pdf', source_key='blb-source', source_type='blb',
                      pages=({'pdf_page':1,'text':'Die erste Rate ist zu zahlen.'},))
        def earlier(prompt):
            if prompt.startswith('PAGE_EXTRACTION.'):
                return {'working_units':[{'name':'Rate zahlen','action':'Rate zahlen','actor':'Bauherr',
                        'performed_by':None,'citations':[{'pdf_page':1,'quote':blb.pages[0]['text']}]}],
                        'carry_forward':''}
            return {'offers':[]}
        baseline=extract_page_units([blb],SimpleNamespace(extract=earlier),stop_after='PAGE_OFFERS')
        original=copy.deepcopy(baseline)
        for mode in ('qualified','unqualified','wrong_source'):
            with self.subTest(mode=mode):
                def timing(prompt):
                    data=json.loads(prompt.split('SOURCE DATA:\n')[1])
                    self.assertEqual(data['project_facts']['scope'],'project_wide_contract_context')
                    fact=data['project_facts']['facts'][0]
                    self.assertEqual((fact['source_key'],fact['document_id']),('source-a','D1'))
                    self.assertEqual(data['source_pages'][0]['text'],blb.pages[0]['text'])
                    citation={'pdf_page':1,'quote':self.quote}
                    if mode!='unqualified':
                        citation.update(source_key='source-a' if mode=='qualified' else 'invented',document_id='D1')
                    return self.timing_response(data,citation)
                result=extract_timing(baseline,[blb],SimpleNamespace(extract=timing),
                                      project_facts=self.facts,context_documents=[self.doc])
                codes={f['code'] for f in result['review_findings']}
                if mode=='qualified':self.assertEqual(codes,set())
                else:self.assertTrue(codes & {'citation_quote_not_found','citation_page_outside_window'})
                self.assertEqual(result['context_snapshots'][0]['source_key'],'source-a')
                self.assertEqual(result['working_units'][0]['gemini'],baseline['working_units'][0]['gemini'])
                self.assertEqual(baseline,original)

    def test_external_fact_source_must_be_supplied_and_match_before_calls(self):
        external=replace(self.doc,source_key='external')
        facts=collect_project_facts([external],SimpleNamespace(extract=self.fact_response))
        def fail(prompt):self.fail('Must reject missing or changed evidence before calling Gemini')
        for context in ([],[replace(external,source_snapshot='changed')]):
            with self.subTest(context=context),self.assertRaises(ValueError):
                extract_timing(self.baseline,[self.doc],SimpleNamespace(extract=fail),
                               project_facts=facts,context_documents=context)
