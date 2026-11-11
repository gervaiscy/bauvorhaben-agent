from historical_facts import collect_historical_facts
from clause_fixtures import owned_answer
"""Exercise assembled stage input contracts, independent claims and held-out controls.

Offline transport/structure checks do not certify a model's semantic interpretation.
"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from bauvo_working_units.citation_evidence import CATALOG_PROTOCOL
from bauvo_working_units.page_flow import _prompt, RULES
from bauvo_working_units.phases.project_facts.full_context import context, target_batches, BATCHED_EVIDENCE_PROTOCOL
from bauvo_working_units.project_facts import collect_project_facts
from bauvo_working_units.shared.source_documents import full_source_documents
from clause_fixtures import decode_prompt
from test_project_facts import document

CASES = Path(__file__).resolve().parents[1]/'evaluation/project-facts-cases.json'


def payload(doc):
    sources = full_source_documents([doc])
    pages,entries,_,_ = context(sources)
    scope,_ = target_batches(sources)[0]
    return {**scope,'source_pages':pages,'evidence_catalog':entries,
            'catalog_protocol':CATALOG_PROTOCOL,'evidence_protocol':BATCHED_EVIDENCE_PROTOCOL}


class ProjectFactsPromptContractTests(unittest.TestCase):
    def test_compact_facts_prompt_uses_its_own_source_contract_without_nonexistent_catalog(self):
        source = payload(document('project','Vereinbart ist eine Bodenplatte ohne Keller.'))
        for language in ('de','en'):
            with self.subTest(language=language):
                prompt = _prompt('PAGE_PROJECT_FACTS',source,language)
                instructions,data = prompt.split('SOURCE DATA:\n',1)
                data = json.loads(data)
                self.assertNotIn('evidence_catalog',data)
                self.assertNotIn('evidence_catalog',instructions)
                self.assertNotIn(RULES,instructions)
                self.assertTrue(all('segments' in page and 'text' not in page for page in data['source_pages']))
                self.assertEqual(data['target_evidence_ids'],['S1'])
                self.assertEqual(list(decode_prompt(prompt)['reference_map'].values()),source['target_evidence_ids'])
                self.assertEqual(decode_prompt(prompt)['source_pages'][0]['text'],source['source_pages'][0]['text'])

    def test_historical_facts_catalog_requests_retain_the_matching_preface_and_format(self):
        source = payload(document('project','Eine historische Vertragsregel.'))
        source['evidence_protocol'] = 'source-backed-interpretations-v6'
        prompt = _prompt('PAGE_PROJECT_FACTS',source,'de')
        instructions,data = prompt.split('SOURCE DATA:\n',1)
        data = json.loads(data)
        self.assertIn(RULES,instructions)
        self.assertEqual(data['evidence_catalog'],source['evidence_catalog'])
        self.assertTrue(all('text' in page and 'segments' not in page for page in data['source_pages']))

    def test_downstream_passes_keep_their_original_catalog_source_contract(self):
        doc = document('project','Eine dokumentierte Projektangabe.')
        source = payload(doc)
        request = {'source_key':doc.source_key,'document':{'id':doc.id,'title':doc.title},
                   'source_pages':list(doc.pages),'evidence_catalog':source['evidence_catalog'],
                   'catalog_protocol':CATALOG_PROTOCOL,'working_units':[],
                   'project_facts':{'facts':[],'excluded_passages':[],'review_findings':[]}}
        for phase in ('PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'):
            with self.subTest(phase=phase):
                prompt = _prompt(phase,request,'de')
                instructions,data = prompt.split('SOURCE DATA:\n',1)
                self.assertIn(RULES,instructions)
                self.assertEqual(json.loads(data)['evidence_catalog'],source['evidence_catalog'])

    def test_independent_provisions_can_share_one_original_span_without_losing_either_claim(self):
        case = next(c for c in json.loads(CASES.read_text())['cases']
                    if c['id']=='multiple-provisions-in-one-span')
        doc = document(case['id'],case['text'])
        calls,answers = [],[]
        def provider(prompt):
            calls.append(prompt)
            source = decode_prompt(prompt)
            self.assertEqual(len(source['target_evidence_ids']),1)
            reference = source['target_evidence_ids'][0]
            answer = {'verpflichtungen':[
                {'uncertainty':None,'claims':[{'statement':statement,'evidence_ids':[reference]}],'zitate':[]}
                for statement in ('Der Bauherr beantragt die Entwässerungsgenehmigung.',
                                  'Der Bauherr legt vor Beginn der Erdarbeiten einen Versicherungsnachweis vor.')],
                'nicht_als_fakt_erfasst':[]}
            answer = owned_answer(source, answer)
            answers.append(copy.deepcopy(answer))
            return answer
        result = collect_historical_facts([doc],SimpleNamespace(extract=provider))
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(len(calls),1)
        self.assertEqual(len(result['facts']),2)
        self.assertEqual(result['gemini_responses'][0]['gemini'],answers[0])
        self.assertEqual(result['gemini_responses'][0]['coverage']['missing_ids'],[])
        claims = [fact['derived']['claims'][0] for fact in result['facts']]
        self.assertEqual(claims[0]['verified_citations'],claims[1]['verified_citations'])
        self.assertTrue(all(claim['verified_citations'][0]['quote']==case['text'] for claim in claims))
        self.assertEqual([claim['statement'] for claim in claims],
                         [fact['claims'][0]['statement'] for fact in answers[0]['verpflichtungen']])

    def test_project_status_and_completeness_expectations_are_held_out_of_actual_model_requests(self):
        cases = {case['id']:case for case in json.loads(CASES.read_text())['cases']}
        identifiers = ('documented-project-configuration','conditional-feature-does-not-establish-selection',
                       'offer-presence-is-not-acceptance','prerequisite-does-not-establish-completion',
                       'multiple-provisions-in-one-span')
        for identifier in identifiers:
            case = cases[identifier]
            with self.subTest(case=identifier):
                request = _prompt('PAGE_PROJECT_FACTS',payload(document(identifier,case['text'])),'de')
                self.assertEqual(decode_prompt(request)['source_pages'][0]['text'],case['text'])
                for expectation in case['expectations']:
                    self.assertNotIn(expectation,request)
                self.assertNotIn('expectations',json.loads(request.split('SOURCE DATA:\n',1)[1]))
