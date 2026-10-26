import copy
import json
from types import SimpleNamespace
import unittest

from bauvo_working_units.page_flow import assessment_findings, extract_page_units
from bauvo_working_units.project_flow import Document
from bauvo_working_units.schemas import timing_findings, RESPONSE_SCHEMAS, GENERATION, generation_for
from bauvo_working_units.gemini import GeminiModel


def rule(kind='EVENT_RELATIVE', **changes):
    return {'scope':'Send report','kind':kind,'expression':'within five working days after acceptance',
            'anchor_events':['acceptance'],'relation':'WITHIN_AFTER','offset':'five working days',
            'duration':None,'date':None,'conditions':[],'alternative_group':None,
            'citations':[{'pdf_page':1,'quote':'Source text'}], **changes}


def assessment(rules, status='EXPLICIT'):
    return {'assessments':[{'unit_id':'U1','status':status,'timing_rules':rules,'missing_evidence':[]}]}


class TimingContractTests(unittest.TestCase):
    def test_relative_duration_and_prerequisite_are_distinct_valid_representations(self):
        duration=rule('DURATION',scope='Drying',anchor_events=[],relation=None,offset=None,duration='four weeks')
        prerequisite=rule('PREREQUISITE',anchor_events=['drying complete'],relation='AFTER',offset=None)
        before=rule('DEADLINE',relation='BEFORE',offset='twelve weeks',alternative_group='A')
        alternative=rule('DEADLINE',relation='BEFORE',offset='six weeks',conditions=['supplier confirmation'],alternative_group='A')
        self.assertEqual(timing_findings(assessment([duration,prerequisite,before,alternative,rule()])),[])

    def test_durations_cannot_carry_offsets_or_start_anchors(self):
        good=rule('DURATION',anchor_events=[],relation=None,offset=None,duration='two weeks')
        for changes in ({'offset':'two weeks'}, {'anchor_events':['approval']}, {'relation':'AFTER'}, {'date':'2027-01-01'}, {'duration':None}):
            with self.subTest(changes=changes):
                self.assertEqual(timing_findings(assessment([{**good,**changes}]))[0]['code'],'inconsistent_timing_rule')

    def test_prerequisite_cannot_create_waits_and_shared_targets_need_separate_rules(self):
        for r in [rule('PREREQUISITE',relation='AFTER'),rule('PREREQUISITE',relation='BEFORE',offset=None,duration='four weeks'),
                  rule('PREREQUISITE',relation='BEFORE',offset=None,anchor_events=['coordination','construction'])]:
            self.assertEqual(timing_findings(assessment([r]))[0]['code'],'inconsistent_timing_rule')
        separate=[rule('PREREQUISITE',relation='BEFORE',offset=None,anchor_events=[event]) for event in ['coordination','construction']]
        self.assertEqual(timing_findings(assessment(separate)),[])

    def test_relative_timing_cannot_also_be_a_duration(self):
        self.assertTrue(timing_findings(assessment([rule(duration='five days')])))
        self.assertEqual(timing_findings(assessment([rule('DEADLINE',anchor_events=[],relation=None,offset=None,date='2027-01-01')])),[])

    def test_not_stated_has_no_positive_rules_and_ambiguity_is_retained(self):
        self.assertEqual(timing_findings(assessment([],status='NOT_STATED')),[])
        self.assertEqual(timing_findings(assessment([rule()],status='NOT_STATED'))[0]['code'],'inconsistent_timing_status')
        self.assertEqual(timing_findings(assessment([]))[0]['code'],'inconsistent_timing_status')
        self.assertEqual(timing_findings(assessment([rule(anchor_events=[],relation='BEFORE',offset=None)],status='AMBIGUOUS')),[])

    def test_malformed_rules_flagged_without_interpreting_source_words(self):
        self.assertEqual(timing_findings(assessment([None]))[0]['code'],'invalid_timing_rule')
        self.assertTrue(timing_findings(assessment([rule(kind='invented')])))
        self.assertEqual(timing_findings('not a response'),[])
        # Contract checks cannot decide whether source language establishes a requirement.
        source_denial=assessment([rule('DEADLINE',expression='No date was agreed')])
        self.assertEqual(timing_findings(source_denial),[])

    def test_contract_findings_do_not_mutate_a_response_or_trigger_extra_passes(self):
        doc=Document('D1','Agreement','synthetic.pdf',schema_version=6,pages=({'pdf_page':1,'text':'Source text'},))
        calls=[];answers=[]
        def extract(prompt):
            kind=prompt.split('.',1)[0];data=json.loads(prompt.split('SOURCE DATA:\n')[1]);calls.append(kind)
            if kind=='PAGE_EXTRACTION':out={'working_units':[{'action':'Do work','citations':[{'pdf_page':1,'quote':'Source text'}]}],'offers':[],'carry_forward':''}
            elif kind=='PAGE_TIMING':out=assessment([rule('PREREQUISITE',offset='four weeks')]);out['assessments'][0]['unit_id']=data['working_units'][0]['id']
            else:out={'assessments':[{'unit_id':u['id'],'status':'NOT_STATED','citations':[]} for u in data['working_units']]}
            answers.append(copy.deepcopy(out));return out
        out=extract_page_units([doc],SimpleNamespace(extract=extract))
        self.assertEqual(calls,['PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'])
        self.assertEqual(out['schema_version'],17);self.assertEqual(out['status'],'needs_review')
        self.assertTrue(any(f['code']=='inconsistent_timing_rule' for f in out['review_findings']))
        self.assertEqual([r['gemini'] for r in out['gemini_responses']],answers)
        self.assertEqual(out['working_units'][0]['reviews'][1]['gemini'],answers[3]['assessments'][0])

    def test_timing_schema_sent_to_provider_keeps_simple_answers(self):
        calls=[]
        def generate(**kwargs):calls.append(kwargs);return SimpleNamespace(text='{"assessments":[]}',candidates=[SimpleNamespace(finish_reason='STOP')])
        model=GeminiModel.__new__(GeminiModel);model._client=SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        model._model='gemini-3.5-flash';model._types=SimpleNamespace(GenerateContentConfig=lambda **kw:kw)
        model.extract('PAGE_TIMING. source')
        schema=calls[0]['config']['response_json_schema'];self.assertEqual(schema,RESPONSE_SCHEMAS['PAGE_TIMING'])
        self.assertEqual(set(schema['properties']['assessments']['items']['properties']),
                         {'unit_id','answer','citations'})
        self.assertEqual(calls[0]['config']['thinking_config'],generation_for('PAGE_TIMING')['thinking_config'])
        self.assertEqual(set(RESPONSE_SCHEMAS),{'PAGE_PROJECT_FACTS','PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'})

    def test_unanchored_relative_period_is_flagged_if_claimed_explicit(self):
        result=assessment([rule(offset='six weeks',anchor_events=[])])
        original=copy.deepcopy(result)
        self.assertEqual(timing_findings(result)[0]['code'],'unresolved_timing_reference')
        self.assertEqual(result,original)

    def test_unanchored_relative_period_retains_uncertainty_and_stated_length(self):
        result=assessment([rule(offset='six weeks',anchor_events=[])],status='AMBIGUOUS')
        result['assessments'][0]['missing_evidence']=['Starting event not stated']
        original=copy.deepcopy(result)
        self.assertEqual(timing_findings(result),[])
        self.assertEqual(result,original)

    def test_known_duration_without_start_and_explicit_calendar_date_need_no_anchor(self):
        duration=rule('DURATION',anchor_events=[],relation=None,offset=None,duration='four weeks',conditions=['season'])
        dated=rule('DEADLINE',anchor_events=[],relation=None,offset=None,date='2027-01-01')
        self.assertEqual(timing_findings(assessment([duration,dated])),[])

    def test_ambiguous_empty_timing_shell_is_flagged_without_rewriting_it(self):
        response=assessment([rule('DEADLINE',expression='Work is delayed',anchor_events=[],relation=None,offset=None)],status='AMBIGUOUS')
        before=copy.deepcopy(response)
        self.assertEqual(timing_findings(response)[0]['code'],'empty_relative_timing_rule')
        self.assertEqual(response,before)
