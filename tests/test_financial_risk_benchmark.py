import copy,hashlib,importlib.util,json
from pathlib import Path
import tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('riskbench',ROOT/'scripts/benchmark_financial_risks.py');bench=importlib.util.module_from_spec(spec);spec.loader.exec_module(bench)
class FinancialRiskBenchmarkTests(unittest.TestCase):
 def setUp(self):
  self.case={'id':'secret-case-name','reference':{'exposures':[{'id':'E1','description':'HIDDEN ANSWER','critical':True,'conditions':[{'id':'K1','description':'Condition'}]}]},'sources':[{'source_key':'s','pages':[{'id':'p1','pdf_page':1,'text':'Service costs extra.'}],'documents':[{'document_id':'d','pdf_pages':[1]}]}]}
 def risk(self,refs=None):return {'title':'Extra costs','explanation':'Service is separately chargeable.','evidence_refs':refs or ['p1']}
 def test_frozen_dataset_has_all_categories_and_no_private_names(self):
  data=bench.load_dataset();self.assertEqual(12,len(data['cases']));self.assertEqual('DRAFT_UNREVIEWED',data['review_status']);self.assertEqual({'clear','conditions','exception','no-risk','clarification','derived'},{c['kind'] for c in data['cases']})
 def test_model_payload_excludes_case_id_and_reference(self):self.assertEqual({'sources':self.case['sources']},bench.model_payload(self.case))
 def test_windows_do_not_leak_labels(self):
  for w in bench.windows(self.case):self.assertNotIn('HIDDEN ANSWER',json.dumps(w));self.assertNotIn('secret-case-name',json.dumps(w))
 def test_exact_extraction_rejects_paraphrase(self):
  with self.assertRaises(ValueError):bench.validate_statements({'statements':[{'quote':'The service is expensive.','evidence_ref':'p1'}]},bench.model_payload(self.case))
 def test_invented_reference_rejected(self):
  with self.assertRaises(ValueError):bench.validate_risks({'financial_risks':[self.risk(['invented'])]},bench.model_payload(self.case))
 def test_reviews_require_complete_unique_coverage(self):
  a={'candidate_id':'C1','decision':'KEEP','reason':'Source supports','evidence_refs':['p1'],'risk':self.risk()}
  for assessments in [[],[a,a]]:
   with self.assertRaises(ValueError):bench.reviewed_risks({'assessments':assessments},[{'id':'C1'}],bench.model_payload(self.case))
 def test_discovery_pipeline_with_fake_boundary(self):
  requests=[]
  def call(stage,prompt,payload,schema):
   requests.append(payload)
   if stage.startswith('01'):return {'financial_risks':[self.risk()]}
   return {'assessments':[{'candidate_id':'C0001','decision':'REVISE','reason':'Qualify scope','evidence_refs':['p1'],'risk':self.risk()}]}
  self.assertEqual([self.risk()],bench.execute_pipeline(self.case,'discovery_review',call));self.assertNotIn('HIDDEN ANSWER',json.dumps(requests))
 def test_extract_pipeline_with_fake_boundary(self):
  stages=[]
  def call(stage,prompt,payload,schema):
   stages.append(stage)
   if stage.startswith('01'):return {'statements':[{'quote':'Service costs extra.','evidence_ref':'p1'}]}
   self.assertEqual(1,len(payload['statements']));return {'financial_risks':[self.risk()]}
  self.assertEqual([self.risk()],bench.execute_pipeline(self.case,'extract_interpret',call));self.assertEqual(2,len(stages))
 def setup_scoring(self,path):
  case=self.case;dataset={'cases':[case]};bench.write(path/'frozen-dataset.json',dataset)
  jobs=[{'id':'J1','case_id':case['id'],'model':'test-model','approach':'discovery_review','repeat':1,'status':'complete','risk_count':2},{'id':'J2','case_id':case['id'],'model':'test-model','approach':'discovery_review','repeat':2,'status':'failed'}]
  bench.write(path/'manifest.json',{'binding':{'dataset_sha256':'frozen','models':['test-model'],'approaches':['discovery_review'],'repeats':2},'jobs':jobs});bench.write(path/'private-unblinding-map.json',{'R1':'J1','R2':'J2'})
  approval={'status':'APPROVED','reviewer':'Synthetic test reviewer','independent':True,'dataset_sha256':'frozen','case_decisions':[{'case_id':case['id'],'decision':'APPROVE'}]};bench.write(path/'approval.json',approval)
  grading={'reviewer':'Synthetic test grader','independent':True,'reference_approval':bench.sha(path/'approval.json'),'grades':[{'record_id':'R1','risk_labels':[{'risk_index':0,'exposure_id':'E1','unsupported':False,'missing_conditions':['K1']},{'risk_index':1,'exposure_id':None,'unsupported':True,'missing_conditions':[]}]},{'record_id':'R2','risk_labels':[]}]};bench.write(path/'grading.json',grading)
 def test_scoring_counts_miss_failure_unsupported_and_lost_condition(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);self.setup_scoring(p);m=bench.score(p,p/'grading.json',p/'approval.json')[0]
   self.assertEqual((1,1,1,1),(m['critical_misses'],m['unsupported_warnings'],m['conditions_lost'],m['failed_jobs']))
 def test_unreviewed_reference_cannot_produce_scores(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);self.setup_scoring(p);a=bench.read(p/'approval.json');a['status']='PENDING';bench.write(p/'approval.json',a)
   with self.assertRaises(ValueError):bench.score(p,p/'grading.json',p/'approval.json')
 def test_unfinished_grading_cannot_produce_scores(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);self.setup_scoring(p);g=bench.read(p/'grading.json');g['grades'][0]['risk_labels'][0]['unsupported']=None;bench.write(p/'grading.json',g)
   with self.assertRaises(ValueError):bench.score(p,p/'grading.json',p/'approval.json')
 def test_missing_condition_id_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);self.setup_scoring(p);g=bench.read(p/'grading.json');g['grades'][0]['risk_labels'][0]['missing_conditions']=['invented'];bench.write(p/'grading.json',g)
   with self.assertRaises(ValueError):bench.score(p,p/'grading.json',p/'approval.json')
if __name__=='__main__':unittest.main()
