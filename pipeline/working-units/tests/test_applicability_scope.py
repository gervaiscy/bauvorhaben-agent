"""Scope decisions stay provider-owned; timing/progress are not application filters."""
import copy
import json
from types import SimpleNamespace
import unittest

from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.project_flow import Document


class ApplicabilityScopeTests(unittest.TestCase):
    def test_unknown_space_and_known_progress_survive_four_independent_passes(self):
        text=('Der Anbieter installiert das Gerät. Bei Platzmangel vereinbaren die Parteien einen Ersatz. '
              'Die Stellfläche ist unbekannt. Der Eigentümer legt die Nachweise vor Baubeginn vor. '
              'Die Nachweise hat er bereits vorgelegt.')
        citations=[{'pdf_page':1,'quote':text}]
        units=[{'action':'Gerät installieren','actor':'Anbieter','citations':citations},
               {'action':'Nachweise vorlegen','actor':'Eigentümer','citations':citations}]
        doc=Document('D1','Agreement','synthetic.pdf',schema_version=6,pages=({'pdf_page':1,'text':text},))
        answers=[];calls=[]
        def extract(prompt):
            kind=prompt.split('.',1)[0];calls.append(kind)
            data=json.loads(prompt.split('SOURCE DATA:\n')[1])
            if kind=='PAGE_EXTRACTION':
                out={'working_units':copy.deepcopy(units),'offers':[],'carry_forward':''}
            elif kind=='PAGE_OFFERS':out={'offers':[]}
            else:
                self.assertEqual([u['action'] for u in data['working_units']], [u['action'] for u in units])
                # Applicability results must not bias timing or consequences inputs.
                self.assertTrue(all('applicability' not in u for u in data['working_units']))
                rows=[{'unit_id':u['id']} for u in data['working_units']]
                if kind=='PAGE_APPLICABILITY':
                    rows[0].update(applicability='CONDITIONAL',reason='Available space unknown',
                        applicability_rules=[{'effect':'REPLACED_WHEN','scope':'Gerät installieren',
                            'condition':'Platzmangel','replacement_action':'Ersatz vereinbaren','citations':citations}],
                        missing_evidence=['Stellfläche'])
                    rows[1].update(applicability='APPLIES',reason='Contractual scope despite completed submission',
                        applicability_rules=[],missing_evidence=[])
                elif kind=='PAGE_TIMING':
                    for row in rows:row.update(status='NOT_STATED',timing_rules=[],missing_evidence=[])
                else:
                    for row in rows:row.update(status='NOT_STATED',consequences=[],citations=[])
                out={'assessments':rows}
            answers.append(copy.deepcopy(out));return out
        result=extract_page_units([doc],SimpleNamespace(extract=extract))
        self.assertEqual(calls,['PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'])
        self.assertEqual([r['gemini'] for r in result['gemini_responses']],answers)
        self.assertEqual(len(result['working_units']),2)
        for unit,row in zip(result['working_units'],answers[4]['assessments']):
            self.assertEqual(unit['reviews'][2]['gemini'],row)

    def test_combined_components_keep_null_conditions_citations_and_raw_rules(self):
        for effect,overall in [('APPLIES_WHEN','CONDITIONAL'),('EXCLUDED_WHEN','PARTIALLY_APPLIES')]:
            with self.subTest(effect=effect):
                pages=({'pdf_page':1,'text':'Der Kunde trägt die Prüfgebühren.'},
                       {'pdf_page':2,'text':'Die Lieferkosten hängen von der Paketwahl ab.'})
                doc=Document('D1','Agreement','synthetic.pdf',schema_version=6,pages=pages)
                rules=[{'effect':'APPLIES_WHEN','scope':'Prüfgebühren','condition':None,
                        'replacement_action':None,'citations':[{'pdf_page':1,'quote':pages[0]['text']}]},
                       {'effect':effect,'scope':'Lieferkosten','condition':'Paketwahl',
                        'replacement_action':None,'citations':[{'pdf_page':2,'quote':pages[1]['text']}]}]
                calls=[];app_response=[]
                def extract(prompt):
                    kind=prompt.split('.',1)[0];calls.append(kind)
                    data=json.loads(prompt.split('SOURCE DATA:\n')[1])
                    if kind=='PAGE_EXTRACTION':
                        return {'working_units':[{'action':'Prüfgebühren und Lieferkosten tragen',
                            'actor':'Kunde','citations':[{'pdf_page':1,'quote':pages[0]['text']}]}],
                            'offers':[],'carry_forward':''}
                    if kind=='PAGE_OFFERS':return {'offers':[]}
                    row={'unit_id':data['working_units'][0]['id']}
                    if kind=='PAGE_APPLICABILITY':
                        row.update(applicability=overall,reason='Components assessed separately',
                            applicability_rules=copy.deepcopy(rules),missing_evidence=['Paketwahl'])
                    elif kind=='PAGE_TIMING':row.update(status='NOT_STATED',timing_rules=[],missing_evidence=[])
                    else:row.update(status='NOT_STATED',consequences=[],citations=[])
                    out={'assessments':[row]}
                    if kind=='PAGE_APPLICABILITY':app_response.append(copy.deepcopy(out))
                    return out
                result=extract_page_units([doc],SimpleNamespace(extract=extract))
                self.assertEqual(calls,['PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'])
                row=result['working_units'][0]['reviews'][2]['gemini']
                self.assertEqual(row['applicability_rules'],rules)
                self.assertIsNone(row['applicability_rules'][0]['condition'])
                self.assertEqual(result['gemini_responses'][4]['gemini'],app_response[0])
                self.assertEqual(result['review_findings'],[])

    def test_provider_uses_simple_applicability_schema_without_changing_raw_response(self):
        from bauvo_working_units.gemini import GeminiModel
        from bauvo_working_units.schemas import RESPONSE_SCHEMAS
        response={'assessments':[{'unit_id':'U1','applicability':'CONDITIONAL',
                  'reason':'Applies only when the delivery option is selected.'}]}
        calls=[]
        def generate(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text=json.dumps(response),candidates=[SimpleNamespace(finish_reason='STOP')])
        model=GeminiModel.__new__(GeminiModel)
        model._client=SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        model._types=SimpleNamespace(GenerateContentConfig=lambda **kwargs:kwargs)
        model._model='gemini-3.5-flash'
        self.assertEqual(model.extract('PAGE_APPLICABILITY. source'),response)
        schema=calls[0]['config']['response_json_schema']
        self.assertEqual(schema,RESPONSE_SCHEMAS['PAGE_APPLICABILITY'])
        fields=schema['properties']['assessments']['items']
        self.assertEqual(set(fields['properties']),{'unit_id','applicability','reason'})
        self.assertEqual(set(fields['required']),{'unit_id','applicability','reason'})
        self.assertFalse(fields['additionalProperties'])

    def test_applicability_and_modality_remain_separate_answers_with_raw_evidence(self):
        cases = [
            ('Eine jährliche Prüfung wird empfohlen.', 'APPLIES', 'RECOMMENDED', []),
            ('Das Zusatzpaket kann bestellt werden. Die Auswahl ist offen.',
             'CONDITIONAL', 'OPTIONAL', ['Auswahl des Zusatzpakets']),
            ('Der Eigentümer muss den Nachweis vorlegen. Er wurde bereits vorgelegt.',
             'APPLIES', 'REQUIRED', []),
        ]
        for text, applicability, modality, missing in cases:
            with self.subTest(modality=modality):
                citation={'pdf_page':1,'quote':text}
                doc=Document('D1','Agreement','synthetic.pdf',schema_version=6,
                             pages=({'pdf_page':1,'text':text},))
                supplied=[]
                def extract(prompt):
                    data=json.loads(prompt.split('SOURCE DATA:\n')[1])
                    if prompt.startswith('PAGE_EXTRACTION.'):
                        return {'working_units':[{'action':'Beschriebene Aufgabe', 'actor':None,
                                'performed_by':None, 'citations':[citation]}],'carry_forward':''}
                    if prompt.startswith('PAGE_OFFERS.'): return {'offers':[]}
                    row={'unit_id':data['working_units'][0]['id']}
                    if prompt.startswith('PAGE_APPLICABILITY.'):
                        row.update(applicability=applicability,modality=modality,
                            reason='Belegte Einordnung',modality_citations=[citation],
                            applicability_rules=[{'effect':'APPLIES_WHEN','scope':'Beschriebene Aufgabe',
                                'condition':missing[0] if missing else None,
                                'replacement_action':None,'citations':[citation]}],missing_evidence=missing)
                        supplied.append(copy.deepcopy(row))
                    elif prompt.startswith('PAGE_TIMING.'):
                        row.update(status='NOT_STATED',timing_rules=[],missing_evidence=[])
                    else: row.update(status='NOT_STATED',consequences=[],citations=[])
                    return {'assessments':[row]}
                result=extract_page_units([doc],SimpleNamespace(extract=extract))
                review=next(r for r in result['working_units'][0]['reviews'] if r['kind']=='PAGE_APPLICABILITY')
                self.assertEqual(review['gemini'],supplied[0])
                self.assertEqual(review['gemini']['applicability'],applicability)
                self.assertEqual(review['gemini']['modality'],modality)
                self.assertEqual(result['review_findings'],[])
