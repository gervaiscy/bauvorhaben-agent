import copy,json,tempfile,unittest
from pathlib import Path
from customer_report import fingerprint
from test_customer_report import fixture
from timing_assessment import calculate,validate_assessments,run_batches


def sample():
 _,p,q=fixture();q['rules'][0]['id']='price_boundary'
 cs=q['units'][0]['citations']
 records=[{'unit_id':u['id'],'finding':'NO_TIMING_RULE_FOUND','reason':'No stated time relationship in supplied clause.',
     'relative_text':'Keine gesonderte Frist','citations':copy.deepcopy(cs),'rules':[]} for u in q['units']]
 def rule(anchor,relation='NO_LATER_THAN',offset=None,condition=None,group=None,option=None):
  return {'id':'r','anchor':anchor,'relation':relation,'offset':offset,'condition':condition,
      'branch_group':group,'branch_option':option,'citations':copy.deepcopy(cs),'explanation':'Explicit clause relationship'}
 def assign(i,*rules):records[i].update(finding='RULES_FOUND',rules=list(rules))
 return p,q,records,rule,assign

class AllTimingTests(unittest.TestCase):
 def test_all_units_classified_including_no_timing(self):
  p,q,a,r,put=sample();put(3,r('planned_start','BEFORE',{'unit':'weeks','min':10,'max':10}))
  put(2,r('unit:start'));put(1,r('unit:release','BEFORE',{'unit':'weeks','min':4,'max':8}))
  out=calculate(p,q,a);self.assertEqual(out['coverage'],{'expected':4,'assessed':4})
  planning=out['units'][1];self.assertEqual(planning['candidates'][0]['date'],'2026-02-27')
  self.assertTrue(planning['candidates'][0]['sequence_only'])
  self.assertEqual(out['units'][0]['classification'],'NOT_DERIVABLE_FROM_SIGNING')
  self.assertEqual(out['units'][2]['candidates'][0]['date'],'2026-04-24')

 def test_conditional_alternatives_and_event_anchor(self):
  p,q,a,r,put=sample()
  one=r('planned_start','BEFORE',{'unit':'weeks','min':12,'max':12},'Route A','utilities','available')
  two=r('planned_start','BEFORE',{'unit':'weeks','min':6,'max':6},'Route B','utilities','provider_confirmation');two['id']='b'
  put(0,one,two);put(1,r('event:Zugang der Herstellungsmitteilung','AFTER',{'unit':'days','min':10,'max':10}))
  out=calculate(p,q,a)
  self.assertEqual(out['units'][0]['classification'],'CONDITIONAL')
  self.assertEqual([x['date'] for x in out['units'][0]['candidates']],['2026-04-10','2026-05-22'])
  self.assertFalse(out['units'][1]['candidates']);self.assertIn('Zugang',out['units'][1]['missing_anchors'][0]['anchor'])

 def test_cycles_and_invented_sequence_duration_rejected(self):
  p,q,a,r,put=sample();put(0,r('unit:planning'));put(1,r('unit:electrical'))
  with self.assertRaisesRegex(ValueError,'cycle'):calculate(p,q,a)
  a[0]['rules'][0]['offset']={'unit':'days','min':0,'max':0}
  with self.assertRaisesRegex(ValueError,'Sequence-only'):calculate(p,q,a)

 def test_missing_duplicate_unknown_ids_and_bad_quotes_fail(self):
  p,q,a,r,put=sample()
  for records in [a[:-1],a+[a[0]],a[1:]+[dict(a[0],unit_id='wrong')]]:
   with self.assertRaises(ValueError):calculate(p,q,records)
  a[0]['citations'][0]['quote']='Not in the source'
  with self.assertRaises(ValueError):calculate(p,q,a)

 def test_planned_start_cannot_start_actual_completion_clock(self):
  p,q,a,r,put=sample();put(0,r('planned_start','AFTER',{'unit':'weeks','min':46,'max':46}))
  with self.assertRaisesRegex(ValueError,'actual-event'):calculate(p,q,a)

 def test_no_unconditional_dates_for_options_or_unknown_scope(self):
  p,q,a,r,put=sample();q['units'][0]['kind']='OPTION';put(0,r('planned_start'))
  put(1,r('unit:electrical'))
  result=calculate(p,q,a)['units']
  self.assertEqual(result[0]['classification'],'CONDITIONAL')
  self.assertEqual(result[1]['classification'],'CONDITIONAL')
  self.assertIn('Nur bei Wahl',result[0]['candidates'][0]['conditions'][0])
  q['units'][0]['scope']={'status':'UNKNOWN'}
  with self.assertRaises(ValueError):calculate(p,q,a)

 def test_live_events_do_not_supply_missing_anchors(self):
  p,q,a,r,put=sample();p['settings']['events']={'actual_start':{'date':'2026-01-01','verified':True,'evidence':'Diary'}}
  p['input_fingerprint']=fingerprint({k:v for k,v in p.items() if k!='input_fingerprint'});q['input_fingerprint']=p['input_fingerprint']
  put(0,r('event:actual_start','AFTER',{'unit':'weeks','min':46,'max':46}))
  self.assertFalse(calculate(p,q,a)['units'][0]['candidates'])

 def test_resumable_batches_bound_to_prompt_model_and_input(self):
  p,q,a,r,put=sample();review={'input_fingerprint':p['input_fingerprint'],'proposal_fingerprint':fingerprint(q),'units':[{'id':u['id'],'timing':[],'category':'CONTRACT_SCOPE'} for u in q['units']]}
  calls=[]
  class Fake:
   def extract(self,prompt):
    calls.append(prompt);data=json.JSONDecoder().raw_decode(prompt.split('SOURCE DATA:\n')[1])[0];ids={x['unit']['id'] for x in data['requested_units']}
    return {'assessments':[x for x in a if x['unit_id'] in ids]}
  def factory():return Fake()
  factory.identity={'model':'fake'}
  with tempfile.TemporaryDirectory() as d:
   first=run_batches(p,q,review,factory,d,2,1);self.assertEqual(len(calls),2)
   self.assertEqual(run_batches(p,q,review,factory,d,2,1),first);self.assertEqual(len(calls),2)
   factory.identity={'model':'changed'}
   with self.assertRaisesRegex(ValueError,'mismatch'):run_batches(p,q,review,factory,d,2,1)

 def test_full_assessment_replaces_selected_unit_report_dates(self):
  from test_document_report import case
  from document_report import build_document_report,render_document_report
  p,q,review=case();q['rules'][0]['id']='price_boundary';review['proposal_fingerprint']=fingerprint(q)
  _,_,a,r,put=sample();put(0,r('planned_start','BEFORE',{'unit':'weeks','min':12,'max':12}))
  timing=calculate(p,q,a);report=build_document_report(p,q,review,timing)
  rendered=render_document_report(report)
  self.assertIn('4 Positionen geprüft',rendered);self.assertIn('10.04.2026',rendered)
  self.assertIn('Nicht aus Vertragsdatum datierbar',rendered)
  self.assertIsNone(report['planning'])
  put(0,r('unit:planning'))
  relative_html=render_document_report(build_document_report(p,q,review,calculate(p,q,a)))
  self.assertIn('Arbeitsposition: '+q['units'][1]['name'],relative_html)
  timing['units'].pop()
  with self.assertRaisesRegex(ValueError,'snapshot mismatch'):build_document_report(p,q,review,timing)

 def test_resume_preserves_rejected_model_attempts(self):
  p,q,a,r,put=sample();review={'input_fingerprint':p['input_fingerprint'],'proposal_fingerprint':fingerprint(q),'units':[{'id':u['id'],'timing':[],'category':'CONTRACT_SCOPE'} for u in q['units']]}
  answer={'assessments':a[:-1]}
  class Fake:
   def extract(self,prompt):return answer
  def factory():return Fake()
  factory.identity={'model':'fake'}
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaisesRegex(ValueError,'missing='):run_batches(p,q,review,factory,d,4,1)
   original=(Path(d)/'batch-000-attempt-0.raw.json').read_bytes()
   answer={'assessments':a};run_batches(p,q,review,factory,d,4,1)
   self.assertEqual((Path(d)/'batch-000-attempt-0.raw.json').read_bytes(),original)
   self.assertTrue((Path(d)/'batch-000-attempt-3.raw.json').exists())

 def test_unidentified_event_and_zero_duration_are_rejected(self):
  p,q,a,r,put=sample();put(0,r('event: '))
  with self.assertRaisesRegex(ValueError,'name the missing event'):calculate(p,q,a)
  put(0,r('planned_start','BEFORE',{'unit':'days','min':0,'max':0}))
  with self.assertRaisesRegex(ValueError,'zero-day'):calculate(p,q,a)

 def test_event_without_duration_remains_undated(self):
  p,q,a,r,put=sample();put(0,r('event:Fertigstellung','EVENT_TRIGGER'))
  result=calculate(p,q,a)['units'][0]
  self.assertEqual(result['classification'],'NOT_DERIVABLE_FROM_SIGNING')
  self.assertFalse(result['candidates'])
  put(0,r('planned_start','EVENT_TRIGGER'))
  with self.assertRaisesRegex(ValueError,'EVENT_TRIGGER'):calculate(p,q,a)

 def test_start_alias_maps_only_backward_relationships(self):
  p,q,a,r,put=sample();put(0,r('event:Baubeginn','BEFORE',{'unit':'weeks','min':12,'max':12}))
  put(1,r('event:Baubeginn','AFTER',{'unit':'weeks','min':46,'max':46}))
  put(2,r('event:Zugang der Baubeginnmitteilung','BEFORE',{'unit':'weeks','min':2,'max':2}))
  result=calculate(p,q,a)['units']
  self.assertEqual(result[0]['candidates'][0]['date'],'2026-04-10')
  self.assertEqual(result[0]['candidates'][0]['chain'][0]['original_anchor'],'event:Baubeginn')
  self.assertFalse(result[1]['candidates']);self.assertFalse(result[2]['candidates'])

 def test_forward_range_does_not_label_earliest_day_as_due_date(self):
  from test_document_report import case
  from document_report import build_document_report,render_document_report
  p,q,review=case();q['rules'][0]['id']='price_boundary';review['proposal_fingerprint']=fingerprint(q)
  _,_,a,r,put=sample();put(0,r('contract_signed','AFTER',{'unit':'days','min':0,'max':14}))
  html=render_document_report(build_document_report(p,q,review,calculate(p,q,a)))
  self.assertIn('03.07.2025 bis 17.07.2025',html)
  self.assertNotIn('Abgeleitete Planungsfrist: 03.07.2025',html)

if __name__=='__main__':unittest.main()
