"""The compact schema preserves assessments without claims or evidence repair."""
import copy
import json
from types import SimpleNamespace
import unittest
from consequence_fixtures import baseline, assessment_answer
from bauvo_working_units.phases.consequences.stage import extract_consequences
from bauvo_working_units.phases.consequences.schema import response_schema
from bauvo_working_units.schemas import response_schema_for


class DerivedConsequencesTests(unittest.TestCase):
    def test_schema_requires_exact_batch_keys_and_two_fields(self):
        for count in (1,3,10):
            schema=response_schema([f'U{i+1}' for i in range(count)])
            self.assertEqual(len(schema['required']),count)
            self.assertFalse(schema['additionalProperties'])
            for row in schema['properties'].values():
                self.assertEqual(set(row['required']),{'classification','reasoning'})
                self.assertEqual(row['properties']['classification']['enum'],['EXPLICIT','DERIVED','NOT_STATED'])
                self.assertFalse(row['additionalProperties'])

    def test_raw_classification_and_qualified_reasoning_are_not_rewritten(self):
        saved,docs,context=baseline();before=copy.deepcopy(saved)
        for classification in ('EXPLICIT','DERIVED','NOT_STATED'):
            answer=None
            def provider(prompt):
                nonlocal answer
                data=json.loads(prompt.split('SOURCE DATA:\n')[1])
                self.assertEqual(response_schema_for(prompt)['required'],['U1'])
                answer=assessment_answer(data,classification,'Wenn weder Antrag noch gleichwertiger Nachweis vorliegt, fehlt die Freigabevoraussetzung (F1).')
                return copy.deepcopy(answer)
            result=extract_consequences(saved,docs,SimpleNamespace(extract=provider),context_documents=context)
            self.assertEqual(result['gemini_responses'][-1]['gemini'],answer)
            self.assertEqual(result['working_units'][0]['reviews'][-1]['gemini'],answer['U1'])
            self.assertEqual(result['review_findings'],saved['review_findings'])
        self.assertEqual(saved,before)
