"""Pass ownership, context identity and raw evidence are application contracts."""
import copy
import json
from types import SimpleNamespace
import unittest

from bauvo_working_units.citation_checks import citation_findings
from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.project_flow import Document
from bauvo_working_units.project_facts import collect_project_facts, facts_view, evidence_pages
from bauvo_working_units.schemas import RESPONSE_SCHEMAS


def doc(key, text, identifier='D1', count=1):
    return Document(identifier, 'Synthetic agreement', key+'.pdf', schema_version=6, source_key=key,
                    pages=tuple({'pdf_page':n, 'text':text} for n in range(1,count+1)))


class PassResponsibilitiesTests(unittest.TestCase):
    def test_schemas_assign_only_actions_to_extraction_and_scope_to_applicability(self):
        fields=RESPONSE_SCHEMAS['PAGE_EXTRACTION']['properties']['working_units']['items']['properties']
        self.assertEqual(set(fields), {'name','action','actor','performed_by','citations'})
        offer=RESPONSE_SCHEMAS['PAGE_OFFERS']['properties']['offers']['items']['properties']
        self.assertNotIn('dedupe_key',offer)
        app=RESPONSE_SCHEMAS['PAGE_APPLICABILITY']['properties']['assessments']['items']['properties']
        self.assertEqual(set(app),{'unit_id','applicability','reason'})

    def test_task_only_pass_preserves_third_party_performer_and_uncertain_responsibility(self):
        text = 'Der Sachverständige sollte den Nachweis vor Baubeginn erstellen.'
        source = doc('source', text)
        answer = {'working_units': [{
            'name': 'Nachweis erstellen', 'action': text,
            'actor': None, 'performed_by': 'Sachverständiger',
            'citations': [{'pdf_page': 1, 'quote': text}],
        }], 'carry_forward': ''}
        original = copy.deepcopy(answer)
        calls = []

        def extract(prompt):
            calls.append(prompt.split('.', 1)[0])
            return copy.deepcopy(answer)

        result = extract_page_units([source], SimpleNamespace(extract=extract),
                                    stop_after='PAGE_EXTRACTION')
        self.assertEqual(calls, ['PAGE_EXTRACTION'])
        self.assertEqual(result['working_units'][0]['gemini'], original['working_units'][0])
        self.assertEqual(result['gemini_responses'][0]['gemini'], original)
        self.assertEqual(result['review_findings'], [])

    def test_applicability_is_last_across_all_documents_and_receives_contract_sources(self):
        blb=doc('blb','Der Kunde kann optional eine Lichtplanung beauftragen.')
        agreement=doc('contract','Eine Lichtplanung wurde nicht beauftragt.')
        other=doc('other','Der Kunde stellt Bauwasser bereit.',identifier='D2')
        calls=[];answers=[]
        def extract(prompt):
            kind=prompt.split('.',1)[0];data=json.loads(prompt.split('SOURCE DATA:\n')[1]);calls.append((kind,data))
            citation={'pdf_page':1,'quote':data['source_pages'][0]['text']}
            if kind=='PAGE_EXTRACTION':
                out={'working_units':[{'name':'Lichtplanung','action':'Lichtplanung optional beauftragen',
                     'actor':'Kunde','performed_by':None,'citations':[citation]}],'carry_forward':''}
            elif kind=='PAGE_OFFERS':out={'offers':[]}
            else:
                rows=[]
                for u in data['working_units']:
                    self.assertNotIn('modality',u);self.assertNotIn('timing_text',u)
                    row={'unit_id':u['id']}
                    if kind=='PAGE_APPLICABILITY':
                        contexts=data['project_facts']['facts']
                        self.assertTrue(any(r['source_key']=='contract' for r in contexts))
                        c={'source_key':'contract','document_id':'D1','pdf_page':1,'quote':agreement.pages[0]['text']}
                        row.update(applicability='DOES_NOT_APPLY',reason='Not commissioned',modality='OPTIONAL',
                                   modality_citations=[citation],missing_evidence=[],applicability_rules=[{
                                       'effect':'EXCLUDED_WHEN','scope':'Lichtplanung','condition':'Nicht beauftragt',
                                       'replacement_action':None,'citations':[c]}])
                    elif kind=='PAGE_TIMING':row.update(status='NOT_STATED',timing_rules=[],missing_evidence=[])
                    else:row.update(status='NOT_STATED',consequences=[],citations=[])
                    rows.append(row)
                out={'assessments':rows}
            answers.append(copy.deepcopy(out));return out
        fact={'verpflichtung':'Nicht beauftragt','zitate':[{'pdf_page':1,'text':agreement.pages[0]['text']}]}
        facts=collect_project_facts([agreement],SimpleNamespace(extract=lambda p:{'verpflichtungen':[fact]}))
        result=extract_page_units([blb,other],SimpleNamespace(extract=extract),context_documents=[agreement],project_facts=facts)
        kinds=[k for k,d in calls];first=kinds.index('PAGE_APPLICABILITY')
        self.assertTrue(all(k=='PAGE_APPLICABILITY' for k in kinds[first:]))
        self.assertEqual([r['gemini'] for r in result['gemini_responses']],answers)
        self.assertEqual(result['review_findings'],[])
        self.assertEqual(len(result['working_units']),2)  # Context is not extracted again.
        self.assertEqual(result['context_snapshots'][0]['source_key'],'contract')

    def test_project_fact_collection_covers_all_document_windows_without_word_ranking(self):
        contracts=[doc('contract','Bauwasser ist vereinbart.',count=8),
                   doc('contract','Anderes Thema.',identifier='D2',count=2)]
        calls=[]
        def extract(prompt):
            calls.append(json.loads(prompt.split('SOURCE DATA:\n')[1]))
            return {'verpflichtungen':[]}
        facts=collect_project_facts(contracts,SimpleNamespace(extract=extract))
        self.assertEqual(len(calls),5)
        self.assertTrue(all(len(c['source_pages'])<=3 for c in calls))
        self.assertEqual([p for c in facts['windows'] for p in c['primary_pages']],list(range(1,9))+[1,2])
        self.assertEqual(facts['facts'],[])

    def test_context_citation_cannot_borrow_quote_from_other_source_on_same_page(self):
        local=[{'pdf_page':1,'text':'Local evidence'}]
        context=[{'source_key':'contract','document_id':'D1','source_pages':[{'pdf_page':1,'text':'Selected agreement'}]}]
        c={'source_key':'contract','document_id':'D1','pdf_page':1,'quote':'Selected agreement'}
        def check(citation):
            return citation_findings({'citations':[citation]},local,[{**p,'source_key':w['source_key'],'document_id':w['document_id']} for w in context for p in w['source_pages']],'blb','D1')
        self.assertEqual(check(c),[])
        self.assertEqual(check({**c,'quote':'Local evidence'})[0]['code'],'citation_quote_not_found')
        self.assertEqual(check({**c,'document_id':'D2'})[0]['code'],'citation_page_outside_window')
        self.assertEqual(check({'pdf_page':1,'quote':'Selected agreement'})[0]['code'],'citation_quote_not_found')
        self.assertEqual(check({**c,'source_key':[]})[0]['code'],'citation_page_outside_window')

    def test_modality_citations_checked_and_missing_evidence_not_repaired(self):
        answer={'modality':'REQUIRED','modality_citations':[],'missing_evidence':['Project choice']}
        before=copy.deepcopy(answer)
        self.assertEqual(citation_findings(answer,[])[0]['code'],'missing_citations')
        self.assertEqual(answer,before)
        self.assertEqual(citation_findings({'modality':'UNCLEAR','modality_citations':[]},[]),[])

    def test_missing_project_context_remains_explicit_and_no_facts_are_synthesized(self):
        source=doc('blb','Bei gewähltem Paket wird eine Anlage installiert.')
        calls=[]
        def extract(prompt):
            kind=prompt.split('.',1)[0];data=json.loads(prompt.split('SOURCE DATA:\n')[1])
            if kind=='PAGE_EXTRACTION':return {'working_units':[{'action':'Anlage installieren',
                'citations':[{'pdf_page':1,'quote':source.pages[0]['text']}]}],'carry_forward':''}
            if kind=='PAGE_OFFERS':return {'offers':[]}
            if kind=='PAGE_APPLICABILITY':calls.append(data)
            return {'assessments':[{'unit_id':u['id'],'missing_evidence':['Paketwahl'],
                    'applicability':'CONDITIONAL','citations':[]} for u in data['working_units']]}
        result=extract_page_units([source],SimpleNamespace(extract=extract))
        self.assertEqual(calls[0]['project_facts']['facts'],[])
        self.assertNotIn('status',calls[0]['project_facts'])
        self.assertEqual(result['working_units'][0]['reviews'][-1]['gemini']['missing_evidence'],['Paketwahl'])


class ProjectContextCliTests(unittest.TestCase):
    def test_context_arguments_require_pairs_before_constructing_provider(self):
        import sys
        from unittest.mock import patch
        from bauvo_working_units.cli import main
        with patch.object(sys,'argv',['units','--pages','unused','--project','unused',
                         '--output','unused','--context-pages','contract.json']), \
                patch('bauvo_working_units.cli.GeminiModel') as provider:
            with self.assertRaises(SystemExit):main()
            provider.assert_not_called()

    def test_cli_loads_external_context_with_colliding_document_ids_and_preserves_raw(self):
        import sys
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from bauvo_working_units.cli import main
        from test_schema6_flow import fixture
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);local=root/'local';external=root/'contract';local.mkdir();external.mkdir()
            fixture(local,count=1);fixture(external,count=1)
            # Same page and document IDs are legitimate across distinct PDFs.
            payload=json.loads((external/'pages.json').read_text());payload['source']='contract.pdf'
            payload['sha256']='contract-pdf-fingerprint';(external/'pages.json').write_text(json.dumps(payload))
            calls=[];answers=[]
            class Model:
                def __init__(self,*args):pass
                def extract(self,prompt):
                    kind=prompt.split('.',1)[0];data=json.loads(prompt.split('SOURCE DATA:\n')[1]);calls.append((kind,data))
                    if kind=='PAGE_PROJECT_FACTS':out={'verpflichtungen':[]}
                    elif kind=='PAGE_EXTRACTION':out={'working_units':[{'action':'Owner task',
                        'citations':[{'pdf_page':1,'quote':'Owner must complete task 1.'}]}],'carry_forward':''}
                    elif kind=='PAGE_OFFERS':out={'offers':[]}
                    else:out={'assessments':[{'unit_id':u['id'],'status':'NOT_STATED','citations':[]} for u in data['working_units']]}
                    answers.append(copy.deepcopy(out));return out
            argv=['units','--pages',str(local/'pages.json'),'--documents',str(local/'documents.json'),
                  '--context-pages',str(external/'pages.json'),'--context-documents',str(external/'documents.json'),
                  '--project','unused','--output',str(root/'result.json')]
            with patch.object(sys,'argv',argv),patch('bauvo_working_units.cli.GeminiModel',Model):main()
            app=next(data for kind,data in calls if kind=='PAGE_APPLICABILITY')
            self.assertNotIn('source_snapshots',app['project_facts'])
            snapshots=json.loads((root/'result.project-facts.json').read_text())['source_snapshots']
            self.assertEqual(len(snapshots),2)
            self.assertEqual(len({s['source_key'] for s in snapshots}),2)
            self.assertTrue((root/'result.project-facts.json').is_file())
            self.assertEqual(json.loads((root/'result.gemini-raw.json').read_text()),answers)
            self.assertEqual(len(json.loads((root/'result.json').read_text())['context_snapshots']),1)


class ContextFindingTests(unittest.TestCase):
    def test_unresolved_assessment_context_is_one_finding_not_one_per_pass(self):
        source=doc('blb','Eine Aufgabe ist genannt.')
        def extract(prompt):
            kind=prompt.split('.',1)[0];data=json.loads(prompt.split('SOURCE DATA:\n')[1])
            if kind=='PAGE_EXTRACTION':return {'working_units':[{'action':'Aufgabe erledigen','citations':[]}],'carry_forward':''}
            if kind=='PAGE_OFFERS':return {'offers':[]}
            return {'assessments':[{'unit_id':u['id'],'status':'NOT_STATED','citations':[]} for u in data['working_units']]}
        result=extract_page_units([source],SimpleNamespace(extract=extract))
        self.assertEqual(len([f for f in result['review_findings'] if f['code']=='assessment_context_unresolved']),1)


class OverlapIdentityTests(unittest.TestCase):
    def test_application_ids_and_separate_known_lists_without_dedupe_keys(self):
        source=doc('source','Der Kunde plant. Ein Zusatzpaket ist gesondert beauftragbar.',count=4)
        calls=[]
        def extract(prompt):
            kind=prompt.split('.',1)[0];data=json.loads(prompt.split('SOURCE DATA:\n')[1]);calls.append((kind,data))
            if kind in ('PAGE_EXTRACTION','PAGE_OFFERS'):
                page=data['primary_pages'][0]
                c={'pdf_page':page,'quote':data['source_pages'][0]['text']}
                if kind=='PAGE_EXTRACTION':return {'working_units':[{'name':'Planen','action':'Planen',
                    'actor':'Kunde','performed_by':'Kunde','citations':[c]}],'carry_forward':''}
                return {'offers':[{'name':'Zusatzpaket','description':'Zusatzpaket','provider':None,'terms':[],'citations':[c]}]}
            return {'assessments':[{'unit_id':u['id'],'status':'NOT_STATED','citations':[]} for u in data['working_units']]}
        result=extract_page_units([source],SimpleNamespace(extract=extract))
        self.assertEqual(len(result['working_units']),2)
        self.assertEqual(len({u['id'] for u in result['working_units']}),2)
        for kind,data in calls:
            if kind=='PAGE_EXTRACTION':
                self.assertTrue(all(':working_unit:' in u['id'] for u in data['known_items']))
                self.assertTrue(all('dedupe_key' not in u for u in data['known_items']))
            if kind=='PAGE_OFFERS':self.assertTrue(all(':offer:' in u['id'] for u in data['known_items']))
        self.assertTrue(all('dedupe_key' not in u['gemini'] for u in result['working_units']))
