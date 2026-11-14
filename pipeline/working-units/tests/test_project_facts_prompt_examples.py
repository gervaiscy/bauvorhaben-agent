from historical_facts import collect_historical_facts
from clause_fixtures import owned_answer
"""Fictional demonstrations must use the real input, evidence and output contracts.

These offline checks establish example consistency, not Gemini interpretation accuracy.
"""
import copy
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

from bauvo_working_units.citation_evidence import catalog
from bauvo_working_units.page_flow import _prompt
from bauvo_working_units.phases.project_facts.full_context import records
from bauvo_working_units.phases.project_facts.payload import expand_payload
from bauvo_working_units.phases.project_facts.schema import CLAUSE_INDEX
from bauvo_working_units.project_facts import collect_project_facts
from bauvo_working_units.shared.source_documents import full_source_documents
from clause_fixtures import decode_prompt
from test_project_facts import document
from bauvo_working_units.project_flow import Document

PROMPT = Path(__file__).resolve().parent/'fixtures/historical-clause-prompt.txt'


def examples():
    blocks = [json.loads(block) for block in re.findall(r'```json\n(.*?)\n```', PROMPT.read_text(), re.S)]
    if len(blocks) % 2:
        raise AssertionError('Each demonstration needs an input and output')
    return list(zip(blocks[::2], blocks[1::2]))


def bound_example(source, response):
    """Bind fictional short labels to the fingerprints generated from their exact text."""
    source, response = copy.deepcopy(source), copy.deepcopy(response)
    expanded = expand_payload(source)
    pages = expanded['source_pages']
    documents = [Document(scope['document_id'],scope['document_title'],'fictional.pdf',
        schema_version=6,source_key=scope['source_key'],
        pages=tuple({'pdf_page':page['pdf_page'],'text':page['text']} for page in pages
                    if (page['source_key'],page['document_id']) == (scope['source_key'],scope['document_id'])))
        for scope in source['documents']]
    return source, response, documents, expanded['reference_map']


def evaluate(source, answer, documents):
    expanded = expand_payload(source)
    return records(answer,full_source_documents(documents),
        target_evidence_ids=expanded['target_evidence_ids'],require_claims=True,
        allow_context_citations=True,derive_fact_citations=True,authoritative_claims=True,
        reference_map=expanded['reference_map'],provisions=expanded['provisions'],
        target_provision_ids=expanded['target_provision_ids'])


class ProjectFactsPromptExampleTests(unittest.TestCase):
    def test_all_demonstrations_match_live_payload_shape_schema_and_complete_source_accounting(self):
        pairs = examples()
        self.assertEqual(len(pairs),3)
        for original_source,original_answer in pairs:
            with self.subTest(targets=original_source['target_evidence_ids']):
                before = copy.deepcopy((original_source,original_answer))
                source,answer,documents,_ = bound_example(original_source,original_answer)
                expanded = expand_payload(source)
                prompt = _prompt('PAGE_PROJECT_FACTS',{
                    **source,'sources':source['documents'],
                    'source_pages':expanded['source_pages'],'evidence_catalog':expanded['evidence_catalog'],
                    'target_evidence_ids':expanded['target_evidence_ids'],'provisions':expanded['provisions']},'de')
                actual = json.loads(prompt.split('SOURCE DATA:\n', 1)[1])
                self.assertEqual(actual,source)
                self.assertEqual(set(answer),set(CLAUSE_INDEX['required']))
                row_fields = set(CLAUSE_INDEX['properties']['verpflichtungen']['items']['required'])
                for row in answer['verpflichtungen']:
                    self.assertEqual(set(row),row_fields)
                facts,exclusions,coverage,issues = evaluate(source,answer,documents)
                self.assertEqual(issues,[])
                self.assertEqual(coverage['status'],'complete')
                self.assertEqual(coverage['missing_ids'],[])
                self.assertEqual(len(facts),len(answer['verpflichtungen']))
                self.assertEqual(len(exclusions),len(answer['nicht_als_fakt_erfasst']))
                self.assertEqual([fact['gemini'] for fact in facts],answer['verpflichtungen'])
                self.assertEqual((original_source,original_answer),before)

    def test_conditional_rule_binds_all_qualifications_and_keeps_heading_as_separate_context(self):
        source,answer,documents,mapping = bound_example(*examples()[0])
        facts,_,_,_ = evaluate(source,answer,documents)
        derived = facts[0]['derived']
        self.assertEqual(len(facts),2)
        entitlement = derived['claims'][0]
        payout = facts[1]['derived']['claims'][0]
        self.assertEqual([c['evidence_id'] for c in entitlement['verified_citations']],
                         [mapping['S2']])
        self.assertEqual([c['evidence_id'] for c in payout['verified_citations']],
                         [mapping['S2'],mapping['S3']])
        self.assertEqual([c['evidence_id'] for c in derived['context_citations']],
                         [mapping['S1']])
        self.assertEqual(derived['interpretation'],'\n'.join(c['statement'] for c in answer['verpflichtungen'][0]['claims']))
        self.assertEqual(derived['uncertainty'],answer['verpflichtungen'][0]['uncertainty'])
        self.assertTrue(isinstance(derived['uncertainty'],str) and derived['uncertainty'].strip())

    def test_amendment_outside_target_batch_is_available_and_bound_to_its_own_document(self):
        source,answer,documents,mapping = bound_example(*examples()[1])
        amendment = mapping['S2']
        self.assertNotIn(amendment,source['target_evidence_ids'])
        facts,exclusions,coverage,issues = evaluate(source,answer,documents)
        self.assertEqual(issues,[])
        self.assertEqual(exclusions,[])
        self.assertEqual(coverage['supporting_ids'],[amendment])
        citations = facts[0]['derived']['claims'][0]['verified_citations']
        self.assertEqual([c['document_id'] for c in citations],['CONTRACT','AMENDMENT'])
        self.assertEqual([c['pdf_page'] for c in citations],[1,2])

    def test_mixed_passage_example_retains_independent_provisions_and_excludes_only_contact_and_promotion(self):
        source,answer,documents,mapping = bound_example(*examples()[2])
        facts,exclusions,coverage,issues = evaluate(source,answer,documents)
        self.assertEqual(issues,[])
        self.assertEqual(len(facts),2)
        self.assertEqual(coverage['indexed_ids'],[mapping['S2'],mapping['S4']])
        self.assertEqual([e['derived']['citation']['evidence_id'] for e in exclusions],
                         [mapping['S1'],mapping['S3']])

    def test_demonstration_content_is_not_added_to_actual_project_source_data(self):
        doc = document('actual-project','Ein Kontrolltermin ist vier Tage vor der Übergabe zu vereinbaren.')
        calls = []
        def provider(prompt):
            source = decode_prompt(prompt)
            calls.append(source)
            self.assertTrue(all(page['text']==doc.pages[0]['text'] for page in source['source_pages']))
            return owned_answer(source, {'verpflichtungen':[{'uncertainty':None,'claims':[{
                'statement':doc.pages[0]['text'],'evidence_ids':source['target_evidence_ids']}],
                'zitate':[]}],'nicht_als_fakt_erfasst':[]})
        result = collect_historical_facts([doc],SimpleNamespace(extract=provider))
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(len(calls),1)
        self.assertFalse(any(identifier.startswith('EXAMPLE_') for identifier in calls[0]['target_evidence_ids']))

    def test_copying_fictional_example_answers_into_a_project_still_blocks_as_unknown_evidence(self):
        doc = document('actual-project','Der Bauherr muss den Lageplan freigeben.')
        for _,answer in examples():
            before = copy.deepcopy(answer)
            result = collect_historical_facts([doc],SimpleNamespace(extract=lambda _:copy.deepcopy(answer)))
            with self.subTest(answer=answer):
                self.assertEqual(result['status'],'incomplete')
                # Reused S1 is valid for the actual source, but S2+ are unknown here.
                # Label binding cannot certify whether copied prose is true.
                self.assertIn('unknown_evidence_id', {finding['code'] for finding in result['review_findings']})
                self.assertEqual(result['gemini_responses'][0]['gemini'],before)
                self.assertEqual(answer,before)
