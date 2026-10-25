import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from bauvo_working_units.project_flow import Document
from bauvo_working_units.date_calculation import validate_anchors, calculate_dates
from bauvo_working_units.page_flow import extract_page_units, PASS_FILES, PROMPT_DIRECTORY


class DateCalculationTests(unittest.TestCase):
    def setUp(self):
        self.text=('Die Festpreisgarantie endet am 30.11.2026. Die Freigabe muss sechs Wochen vor '
                   'Festpreisgarantie vorliegen. Die Montage erfolgt am 01.12.2026.')
        self.doc=Document('D1','Vertrag','synthetic.pdf',schema_version=6,pages=({'pdf_page':1,'text':self.text},))
        self.citation={'pdf_page':1,'quote':self.text}
        self.anchors=[{'id':'guarantee-end','document_id':'D1','event':'Festpreisgarantie',
                       'date':'2026-11-30','citations':[self.citation]}]
        self.rule={'kind':'EVENT_RELATIVE','scope':'Freigabe','expression':'sechs Wochen vor Festpreisgarantie',
                   'anchor_events':['Festpreisgarantie'],'relation':'BEFORE','offset':'sechs Wochen',
                   'duration':None,'date':None,'conditions':[],'citations':[self.citation],
                   'calculation':{'anchor_id':'guarantee-end','offset_amount':6,'offset_unit':'WEEKS'}}

    def calc(self, rule=None, anchors=None, status='EXPLICIT'):
        return calculate_dates({'status':status,'timing_rules':[rule or self.rule]},self.doc,
                               self.anchors if anchors is None else anchors)[0]

    def test_cited_expiry_anchor_and_exact_six_week_offset(self):
        before=copy.deepcopy(self.rule)
        self.assertEqual(validate_anchors(self.anchors,[self.doc]),self.anchors)
        result=self.calc()
        self.assertEqual(result['date'],'2026-10-19');self.assertEqual(result['status'],'CALCULATED')
        self.assertEqual(result['anchor'],self.anchors[0]);self.assertEqual(self.rule,before)

    def test_expiry_cannot_substitute_for_construction_start(self):
        rule={**self.rule,'anchor_events':['Baubeginn']}
        self.assertEqual(self.calc(rule)['status'],'UNRESOLVED')
        self.assertIsNone(self.calc(anchors=[])['date'])

    def test_fabricated_or_unquoted_anchor_rejected(self):
        for changes in ({'date':'2026-11-29'},{'event':'Baubeginn'},{'document_id':'D2'},
                        {'citations':[{'pdf_page':1,'quote':'Invented date'}]}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                validate_anchors([{**self.anchors[0],**changes}],[self.doc])
        with self.assertRaises(ValueError):validate_anchors(self.anchors*2,[self.doc])

    def test_offset_number_unit_and_source_must_agree(self):
        variants=[{**self.rule,'calculation':{**self.rule['calculation'],'offset_amount':12}},
                  {**self.rule,'calculation':{**self.rule['calculation'],'offset_unit':'MONTHS'}},
                  {**self.rule,'offset':'ca. sechs Wochen'},
                  {**self.rule,'offset':'sechs Arbeitstage'},
                  {**self.rule,'offset':'sieben Wochen','calculation':{**self.rule['calculation'],'offset_amount':7}}]
        for rule in variants:
            with self.subTest(rule=rule):self.assertIsNone(self.calc(rule)['date'])

    def test_ambiguity_duration_prerequisite_and_unknown_relation_do_not_create_dates(self):
        self.assertIsNone(self.calc(status='AMBIGUOUS')['date'])
        for changes in ({'kind':'DURATION'},{'kind':'PREREQUISITE'},{'duration':'sechs Wochen'},{'relation':None}):
            self.assertIsNone(self.calc({**self.rule,**changes})['date'])

    def test_absolute_date_must_be_in_source(self):
        rule={**self.rule,'kind':'DEADLINE','date':'2026-12-01','offset':None,'anchor_events':[], 'calculation':None}
        self.assertEqual(self.calc(rule)['status'],'SOURCE_DATE')
        self.assertIsNone(self.calc({**rule,'date':'2026-12-02'})['date'])

    def test_number_boundaries_and_approximate_source_are_not_exact_periods(self):
        original=self.doc
        for replacement in ('26 Wochen', 'ca. sechs Wochen'):
            self.doc=Document('D1','A','a.pdf',schema_version=6,pages=({'pdf_page':1,'text':self.text.replace('sechs Wochen',replacement)},))
            rule={**self.rule,'offset':'6 Wochen' if replacement=='26 Wochen' else 'sechs Wochen',
                  'citations':[{'pdf_page':1,'quote':self.doc.pages[0]['text']}]}
            self.assertIsNone(self.calc(rule)['date'])
        self.doc=original

    def test_calculation_is_derived_without_modifying_raw_timing_response(self):
        answers=[]
        def extract(prompt):
            kind=prompt.split('.',1)[0];data=json.loads(prompt.split('SOURCE DATA:\n')[1])
            if kind=='PAGE_EXTRACTION':out={'working_units':[{'action':'Freigabe vorlegen','citations':[self.citation]}],'carry_forward':''}
            elif kind=='PAGE_OFFERS':out={'offers':[]}
            elif kind=='PAGE_TIMING':out={'assessments':[{'unit_id':data['working_units'][0]['id'],'status':'EXPLICIT',
                'timing_rules':[copy.deepcopy(self.rule)],'missing_evidence':[]}]}
            else:out={'assessments':[{'unit_id':u['id'],'status':'NOT_STATED','citations':[]} for u in data['working_units']]}
            answers.append(copy.deepcopy(out));return out
        result=extract_page_units([self.doc],SimpleNamespace(extract=extract),supported_anchors=self.anchors)
        review=next(r for r in result['working_units'][0]['reviews'] if r['kind']=='PAGE_TIMING')
        self.assertEqual(review['derived']['date_calculations'][0]['date'],'2026-10-19')
        self.assertEqual([r['gemini'] for r in result['gemini_responses']],answers)
        self.assertNotIn('date_calculations',review['gemini'])

    def test_deadline_at_supported_anchor_needs_no_invented_zero_day_offset(self):
        text='Die Festpreisgarantie endet am 30.11.2026. Die Freigabe muss bis zum Ende der Festpreisgarantie vorliegen.'
        self.doc=Document('D1','Vertrag','synthetic.pdf',schema_version=6,pages=({'pdf_page':1,'text':text},))
        citation={'pdf_page':1,'quote':text}
        self.anchors[0]['citations']=[citation]
        validate_anchors(self.anchors,[self.doc])
        rule={**self.rule,'relation':'BY','offset':None,'calculation':None,
              'expression':'bis zum Ende der Festpreisgarantie','citations':[citation]}
        before=copy.deepcopy(rule);result=self.calc(rule)
        self.assertEqual(result['date'],'2026-11-30')
        self.assertEqual(result['calculation'],{'relation':'BY','method':'same_event_date'})
        self.assertEqual(rule,before)
        self.assertIsNone(self.calc(rule,anchors=[])['date'])

    def test_absolute_date_retains_conditions_and_malformed_dates_stay_unresolved(self):
        rule={**self.rule,'kind':'DEADLINE','date':'2026-12-01','offset':None,'anchor_events':[],
              'calculation':None,'conditions':['Nur bei gewählter Option']}
        result=self.calc(rule)
        self.assertEqual(result['status'],'SOURCE_DATE_CONDITIONAL')
        self.assertEqual(result['conditions'],rule['conditions'])
        self.assertIsNone(self.calc({**rule,'date':{'unsupported':'shape'}})['date'])

    def test_conditions_remain_on_calculated_deadline(self):
        result=self.calc({**self.rule,'conditions':['Wenn diese Option gewählt ist']})
        self.assertEqual(result['status'],'CALCULATED_CONDITIONAL')
        self.assertEqual(result['conditions'],['Wenn diese Option gewählt ist'])

    def test_calendar_month_boundary_is_not_silently_clamped(self):
        from bauvo_working_units.date_calculation import shifted
        from datetime import date
        self.assertEqual(shifted(date(2026,6,13),12,'MONTHS'),date(2027,6,13))
        with self.assertRaises(ValueError):shifted(date(2026,1,31),1,'MONTHS')
        with self.assertRaises(ValueError):shifted(date(2024,2,29),1,'YEARS')


class FivePassTests(unittest.TestCase):
    def test_five_separate_prompts_and_three_page_document_local_windows(self):
        self.assertEqual(len(PASS_FILES),5)
        self.assertEqual(len(set(PASS_FILES.values())),5)
        self.assertTrue(all((PROMPT_DIRECTORY/name).is_file() for name in PASS_FILES.values()))
        text='Der Kunde bestätigt die Planung. Eine Zusatzplanung wird für 100 Euro angeboten.'
        docs=[Document('D1','A','a.pdf',schema_version=6,pages=tuple({'pdf_page':n,'text':text} for n in range(1,7))),
              Document('D2','B','a.pdf',schema_version=6,pages=({'pdf_page':7,'text':text},))]
        calls=[];answers=[]
        def extract(prompt):
            kind=prompt.split('.',1)[0];data=json.loads(prompt.split('SOURCE DATA:\n')[1]);calls.append((kind,data))
            page=data.get('primary_pages',[data['source_pages'][0]['pdf_page']])[0];citation={'pdf_page':page,'quote':text}
            if kind=='PAGE_EXTRACTION':
                out={'working_units':[{'action':'Planung bestätigen','actor':'Kunde','performed_by':'Kunde',
                    'modality':'REQUIRED','timing_text':None,'citations':[citation]}],'carry_forward':''}
            elif kind=='PAGE_OFFERS':out={'offers':[{'provider':'Anbieter','description':'Zusatzplanung',
                  'terms':['100 Euro'],'citations':[citation]}]}
            else:out={'assessments':[{'unit_id':u['id'],'status':'NOT_STATED','timing_rules':[],
                                      'consequences':[],'citations':[]} for u in data['working_units']]}
            answers.append(copy.deepcopy(out));return out
        result=extract_page_units(docs,SimpleNamespace(extract=extract))
        self.assertEqual([w['pages'] for w in result['windows']],[[1,2,3],[3,4,5],[5,6],[7]])
        self.assertEqual(set(k for k,d in calls),set(PASS_FILES))
        self.assertEqual(len(result['offers']),4);self.assertEqual(len(result['working_units']),4)
        self.assertTrue(all(len(u['reviews'])==3 for u in result['working_units']))
        self.assertEqual([r['gemini'] for r in result['gemini_responses']],answers)
        for kind,data in calls:
            self.assertLessEqual(len(data['source_pages']),3)
            if data['document']['id']=='D2':self.assertEqual([p['pdf_page'] for p in data['source_pages']],[7])
            if kind in ('PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'):
                self.assertTrue(all('reviews' not in u for u in data['working_units']))

    def test_unsupported_anchor_fails_before_paid_requests(self):
        doc=Document('D1','A','a.pdf',schema_version=6,pages=({'pdf_page':1,'text':'Keine Daten.'},))
        def extract(prompt):raise AssertionError('No model call should be made')
        with self.assertRaises(ValueError):extract_page_units([doc],SimpleNamespace(extract=extract),supported_anchors=[{}])
