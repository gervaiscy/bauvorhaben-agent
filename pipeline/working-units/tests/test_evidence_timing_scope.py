"""Evidence and the activity it describes keep independent timing assessments."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.project_flow import Document

BASE=Path(__file__).resolve().parents[1]/'evaluation'
sys.path.insert(0,str(BASE))
import focused_evaluation

CASES=('evidence-describes-delivery-deadline','evidence-and-delivery-separate-deadlines',
       'evidence-backed-alternative-delivery')


class EvidenceTimingScopeTests(unittest.TestCase):
    def test_three_controls_send_both_units_and_literal_sources_without_expectations(self):
        fixtures={c['id']:c for c in json.loads((BASE/'timing-cases.json').read_text())['cases']}
        calls=[]
        class Model:
            def extract(inner,prompt):
                self.assertTrue(prompt.startswith('PAGE_TIMING.'))
                data=json.loads(prompt.split('SOURCE DATA:\n')[1]);calls.append(data)
                self.assertEqual({u['id'] for u in data['working_units']},{'DELIVERY','EVIDENCE'})
                self.assertFalse(set(data)&{'expected','forbidden','expectations'})
                case=fixtures[data['source_key']]
                self.assertEqual(data['source_pages'][0]['text'],case['text'])
                self.assertTrue(all(u['citations'][0]['quote'] in case['text'] for u in data['working_units']))
                return {'assessments':[{'unit_id':u['id'],'status':'NOT_STATED',
                        'timing_rules':[],'missing_evidence':[]} for u in data['working_units']]}
        with tempfile.TemporaryDirectory() as tmp:
            summary=focused_evaluation.run(Path(tmp)/'out','unused',model=Model(),case_ids=CASES)
            self.assertEqual(len(summary),3);self.assertEqual(len(calls),3)
            self.assertTrue(all(row['status']=='recorded' for row in summary))

    def test_alternative_delivery_rules_do_not_migrate_to_confirmation_when_rows_are_reversed(self):
        text='Lieferung acht Wochen vorher oder mit Bestätigung drei Wochen vorher.'
        citation=[{'pdf_page':1,'quote':text}]
        doc=Document('D1','Contract','synthetic.pdf',schema_version=6,pages=({'pdf_page':1,'text':text},))
        proposals=[{'action':'Bauteile liefern','citations':citation},
                   {'action':'Bestätigung vorlegen','citations':citation}]
        rules=[{'kind':'DEADLINE','scope':'Bauteile liefern','expression':expr,
                'anchor_events':['Montagebeginn'],'relation':'BEFORE','offset':expr,
                'duration':None,'date':None,'conditions':conditions,'alternative_group':'delivery',
                'citations':citation} for expr,conditions in [('acht Wochen',[]),('drei Wochen',['Verbindliche Bestätigung'])]]
        calls=[];timing=[]
        def extract(prompt):
            kind=prompt.split('.',1)[0];calls.append(kind);data=json.loads(prompt.split('SOURCE DATA:\n')[1])
            if kind=='PAGE_EXTRACTION':return {'working_units':copy.deepcopy(proposals),'offers':[],'carry_forward':''}
            if kind=='PAGE_OFFERS':return {'offers':[]}
            ids=[u['id'] for u in data['working_units']]
            if kind=='PAGE_TIMING':
                out={'assessments':[{'unit_id':ids[1],'status':'NOT_STATED','timing_rules':[],'missing_evidence':[]},
                     {'unit_id':ids[0],'status':'EXPLICIT','timing_rules':copy.deepcopy(rules),'missing_evidence':[]}]}
                timing.append(copy.deepcopy(out));return out
            return {'assessments':[{'unit_id':u,'status':'NOT_STATED','citations':[]} for u in ids]}
        result=extract_page_units([doc],SimpleNamespace(extract=extract),chunk_pages=2,overlap_pages=1)
        self.assertEqual(calls,['PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'])
        delivery,evidence=result['working_units']
        self.assertEqual(delivery['reviews'][0]['gemini']['timing_rules'],rules)
        self.assertEqual(evidence['reviews'][0]['gemini']['timing_rules'],[])
        self.assertEqual(evidence['reviews'][1]['gemini']['status'],'NOT_STATED')
        self.assertEqual(result['gemini_responses'][2]['gemini'],timing[0])
