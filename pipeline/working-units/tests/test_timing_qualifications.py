from clause_fixtures import decode_prompt
"""Generic timing controls preserve evidence and keep semantic judgments separate."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

from bauvo_working_units.page_flow import PASS_RULES, extract_page_units
from bauvo_working_units.project_flow import Document
from bauvo_working_units.schemas import RESPONSE_SCHEMAS

BASE = Path(__file__).resolve().parents[1]/'evaluation'
sys.path.insert(0,str(BASE))
import focused_evaluation as evaluation

CASES = ('scoped-start-weather-restart-and-cap', 'connection-or-confirmed-date-with-reference',
         'all-prerequisites-start-last-received', 'limited-restart-and-unrelated-deadline',
         'contained-rental-limit-is-not-installation-duration',
         'post-completion-inspection-does-not-recite-financing',
         'weather-qualifications-apply-only-to-named-subset',
         'direct-timing-and-alternative-paths-remain-task-specific',
         'phase-duration-without-its-own-start-event',
         'explicit-common-phase-start-is-retained',
         'explicit-specific-phase-start-is-retained',
         'composite-task-keeps-separate-phase-durations')


class TimingQualificationTests(unittest.TestCase):
    def test_controls_have_literal_evidence_fit_production_windows_and_keep_expectations_separate(self):
        cases=json.loads((BASE/'timing-cases.json').read_text())['cases']
        self.assertEqual(len(cases),len({case['id'] for case in cases}))
        selected={case['id']:case for case in cases if case['id'] in CASES}
        self.assertEqual(set(selected),set(CASES))
        for case in selected.values():
            self.assertTrue(case['expected']);self.assertTrue(case['forbidden'])
            self.assertLessEqual(len(case['pages']),10)
            pages={page['pdf_page']:page['text'] for page in case['pages']}
            for unit in case['working_units']:
                for citation in unit['citations']:
                    self.assertIn(citation['quote'],pages[citation['pdf_page']])
        self.assertGreater(len(selected[CASES[0]]['pages']),1)

    def test_focused_controls_receive_current_prompt_full_pages_and_ids_without_giving_away_expectations(self):
        fixtures={case['id']:case for case in json.loads((BASE/'timing-cases.json').read_text())['cases']}
        calls=[];answers=[]
        def model(prompt):
            instructions,source=prompt.split('SOURCE DATA:\n')
            self.assertTrue(prompt.startswith('PAGE_TIMING.'))
            self.assertIn(PASS_RULES['PAGE_TIMING'],instructions)
            data=json.loads(source);calls.append(data)
            case=fixtures[data['source_key']]
            self.assertEqual([{k:page[k] for k in ('pdf_page','text')} for page in data['source_pages']],case['pages'])
            self.assertFalse(set(data)&{'expected','forbidden','expectations'})
            self.assertNotIn('evidence_catalog', data)
            self.assertEqual(data['project_facts'], {'facts': []})
            # Deliberately incomplete: source-valid evidence must not be reported
            # as proof that the condensed timing answer preserved all conditions.
            response={'assessments':[{'unit_id':u['id'],'answer':'Eine verkürzte Zeitantwort.',
                'citations':[{'pdf_page': data['source_pages'][0]['pdf_page'], 'quote': data['source_pages'][0]['text']}]} for u in data['working_units']]}
            answers.append(copy.deepcopy(response));return response
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'timing'
            summary=evaluation.run(output,'unused',model=SimpleNamespace(extract=model),case_ids=CASES)
            self.assertEqual(len(calls),len(CASES))
            self.assertTrue(all(row['status']=='recorded' for row in summary))
            for data,answer in zip(calls,answers):
                folder=output/data['source_key']
                result=json.loads((folder/'result.json').read_text())
                self.assertTrue(result['semantic_review_required'])
                self.assertEqual(result['responses'][0]['gemini'],answer)
                self.assertEqual(json.loads((folder/'raw.json').read_text()),[answer])
                self.assertEqual(json.loads((folder/'expectations.json').read_text()),fixtures[data['source_key']])
                self.assertEqual(result['responses'][0]['citation_evidence']['findings'],[])

    def test_production_timing_keeps_full_answer_in_raw_and_review_without_a_new_schema(self):
        case=next(c for c in json.loads((BASE/'timing-cases.json').read_text())['cases'] if c['id']==CASES[0])
        doc=Document('D1','Synthetic contract','synthetic.pdf',schema_version=6,source_key=case['id'],
                     pages=tuple(case['pages']))
        original=copy.deepcopy(case);timing=[]
        def model(prompt):
            phase=prompt.split('.',1)[0];data=decode_prompt(prompt)
            citations=[{'evidence_id':entry['evidence_id']} for entry in data.get('evidence_catalog',[])]
            if phase=='PAGE_EXTRACTION':
                return {'working_units':[{**case['working_units'][0],'citations':citations}],'carry_forward':''}
            if phase=='PAGE_OFFERS':return {'offers':[]}
            self.assertEqual(phase,'PAGE_TIMING')
            self.assertEqual([p['text'] for p in data['source_pages']],[p['text'] for p in case['pages']])
            answer={'assessments':[{'unit_id':data['working_units'][0]['id'],
                'answer':'\n'.join(p['text'] for p in case['pages']),
                'citations':[{'pdf_page': p['pdf_page'], 'quote': p['text']} for p in data['source_pages']]}]}
            timing.append(copy.deepcopy(answer));return answer
        result=extract_page_units([doc],SimpleNamespace(extract=model),stop_after='PAGE_TIMING')
        self.assertEqual(result['review_findings'],[])
        self.assertEqual(result['gemini_responses'][-1]['gemini'],timing[0])
        self.assertEqual(result['working_units'][0]['reviews'][0]['gemini'],timing[0]['assessments'][0])
        self.assertEqual(case,original)
        fields=RESPONSE_SCHEMAS['PAGE_TIMING']['properties']['assessments']['items']['properties']
        self.assertEqual(set(fields),{'unit_id','answer','citations'})
