"""Checkpoint, model-boundary and global-graph regression tests."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from batched_report import (apply_global_patch, batch_context, canonical_registry,
                            run_batched, validate_batch, review_scope_chunks)
from customer_report import compile_report, fingerprint
from test_customer_report import fixture


def make_unit(entry, payload):
    return dict(entry, actor=entry['actor'] or 'Bauherr', action=entry['name'], kind='OBLIGATION',
        citations=[{'source_key':'blb','line_ids':['L1'],
                    'quote':payload['source_registry']['blb']['L1']['text']}],
        scope={'status':'APPLIES','condition':None,'condition_citations':[],'reason':''},
        requirements=[],reconciliation_notes=[])


def empty_patch():
    return {k:[] for k in ('unit_updates','additional_units','merges','rules','unresolved_questions')}


class Factory:
    def __init__(self,payload): self.payload=payload; self.calls=[]; self.fail_name=None
    def __call__(self): return self
    def extract(self,prompt):
        data=json.JSONDecoder().raw_decode(prompt.split('\nDATA:\n',1)[1])[0]
        self.calls.append(data)
        if 'requested_units' in data:
            if any(u['name']==self.fail_name for u in data['requested_units']):
                raise RuntimeError('simulated rate limit')
            return {'units':[make_unit(u,self.payload) for u in data['requested_units']],
                    'unresolved_questions':[]}
        result=empty_patch()
        if data.get('review_stage')=='timing':
            result['rules']=[{'id':'fixture_price_boundary','target':'anchor:price','anchor':'contract_signed',
                'offset':{'unit':'months','min':12,'max':12},'kind':'PLANNING_TARGET','requires_selection':None,
                'citations':[{'source_key':'blb','line_ids':['L1'],'quote':self.payload['source_registry']['blb']['L1']['text']}],
                'assumptions':[]}]
        return result


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.payload=fixture()[1]
        self.registry=canonical_registry(self.payload)

    def test_global_timing_cannot_succeed_by_dropping_all_rules(self):
        class DropsRules(Factory):
            def extract(inner,prompt):
                result=super(DropsRules,inner).extract(prompt)
                if inner.calls[-1].get('review_stage')=='timing': result['rules']=[]
                return result
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'MISSING_TIMING_RULES'):
                run_batched(self.payload,DropsRules(self.payload),tmp,batch_size=4,workers=1)
            self.assertFalse((Path(tmp)/'final.checkpoint.json').exists())

    def test_registry_is_stable_and_covers_every_input(self):
        reordered=copy.deepcopy(self.payload); reordered['units'].reverse()
        self.assertEqual({u['member_refs'][0]:u['id'] for u in self.registry},
                         {u['member_refs'][0]:u['id'] for u in canonical_registry(reordered)})
        self.assertEqual(len({u['id'] for u in self.registry}),4)

    def test_page_context_context_and_other_document_contract_are_retained(self):
        self.payload['source_registry']['contract']={'C1':{'text':'Signed exclusion'}}
        result=batch_context(self.payload,self.registry,self.registry[:1])
        self.assertEqual(result['source_registry']['contract'],self.payload['source_registry']['contract'])
        self.assertIn('L1',result['source_registry']['blb'])
        self.assertEqual(len(result['units']),1)
        self.assertEqual(len(result['registry']),4)

    def test_missing_member_and_bad_quote_are_rejected(self):
        result={'units':[],'unresolved_questions':[]}
        with self.assertRaisesRegex(ValueError,'omitted'):
            validate_batch(result,self.registry[:1],self.registry,self.payload)
        unit=make_unit(self.registry[0],self.payload);unit['citations'][0]['quote']='invented'
        result['units']=[unit]
        with self.assertRaisesRegex(ValueError,'quote'):
            validate_batch(result,self.registry[:1],self.registry,self.payload)

    def test_successful_run_replays_without_model_calls(self):
        model=Factory(self.payload)
        with tempfile.TemporaryDirectory() as tmp:
            first=run_batched(self.payload,model,tmp,batch_size=2,workers=1)
            self.assertEqual(len(model.calls),4)
            second=run_batched(self.payload,model,tmp,batch_size=2,workers=1)
            self.assertEqual(first,second);self.assertEqual(len(model.calls),4)
            self.assertEqual(compile_report(self.payload,first)['status'],'DRAFT')

    def test_feedback_collects_citation_and_reversed_deadline_errors(self):
        unit=make_unit(self.registry[0],self.payload)
        unit['citations']=[]
        unit['requirements']=[{'mode':'ALL','members':[{'unit_id':self.registry[1]['id'],
            'lag_days':{'min':-84,'max':-84},'citations':[]}]}]
        with self.assertRaises(ValueError) as caught:
            validate_batch({'units':[unit],'unresolved_questions':[]},self.registry[:1],self.registry,self.payload)
        message=str(caught.exception)
        self.assertIn(': citations:',message)
        self.assertIn(': dependency ',message)
        self.assertIn('Remove that reversed dependency',message)
        self.assertIn('Do not change a negative lag to zero',message)

    def test_resume_supplies_rejected_answer_with_fresh_diagnostics(self):
        payload=self.payload
        class RepairModel(Factory):
            broken=True
            prompts=[]
            def extract(self,prompt):
                self.prompts.append(prompt)
                response=super().extract(prompt)
                if self.broken and 'units' in response: response['units'][0]['citations']=[]
                return response
        model=RepairModel(payload)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):run_batched(payload,model,tmp,batch_size=4,workers=1)
            model.broken=False
            run_batched(payload,model,tmp,batch_size=4,workers=1)
            self.assertIn('INVALID_RESPONSE:',model.prompts[2])
            self.assertIn(self.registry[0]['id']+': citations:',model.prompts[2])

    def test_failure_retains_checkpoints_and_only_failed_batch_resumes(self):
        model=Factory(self.payload);model.fail_name=self.registry[2]['name']
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'checkpoints retained'):
                run_batched(self.payload,model,tmp,batch_size=2,workers=1)
            self.assertTrue((Path(tmp)/'batch-000.checkpoint.json').exists())
            self.assertFalse((Path(tmp)/'final.checkpoint.json').exists())
            model.fail_name=None
            run_batched(self.payload,model,tmp,batch_size=2,workers=1)
            self.assertEqual(len(model.calls),5) # first success, failure, resumed batch, scope, timing

    def test_first_failure_stops_submitting_more_batches(self):
        model=Factory(self.payload);model.fail_name=self.registry[0]['name']
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):run_batched(self.payload,model,tmp,batch_size=1,workers=1)
            self.assertEqual(len(model.calls),1)

    def test_changed_input_and_tampered_checkpoint_are_rejected(self):
        model=Factory(self.payload)
        with tempfile.TemporaryDirectory() as tmp:
            run_batched(self.payload,model,tmp,batch_size=2,workers=1)
            changed=copy.deepcopy(self.payload);changed['input_fingerprint']='different'
            with self.assertRaisesRegex(ValueError,'another input'):
                run_batched(changed,model,tmp,batch_size=2,workers=1)
            path=Path(tmp)/'batch-000.checkpoint.json';saved=json.loads(path.read_text())
            saved['response']['units'][0]['name']='tampered';path.write_text(json.dumps(saved))
            with self.assertRaisesRegex(ValueError,'modification'):
                run_batched(self.payload,model,tmp,batch_size=2,workers=1)

    def test_source_failure_prevents_any_model_call(self):
        self.payload['source_integrity'][self.payload['units'][0]['id']]['status']='UNRESOLVED'
        model=Factory(self.payload)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'source identity'):
                run_batched(self.payload,model,tmp)
            self.assertFalse(model.calls)

    def test_batch_model_has_own_client_per_request(self):
        created=[];payload=self.payload
        def factory():
            instance=Factory(payload);created.append(instance);return instance
        with tempfile.TemporaryDirectory() as tmp:
            run_batched(payload,factory,tmp,batch_size=2,workers=2)
        self.assertEqual(len(created),4)
        self.assertTrue(all(len(m.calls)==1 for m in created))

    def test_unique_citation_repair_keeps_raw_response_and_audit(self):
        class ShiftedCitation(Factory):
            def extract(self,prompt):
                response=super().extract(prompt)
                if 'units' in response: response['units'][0]['citations'][0]['line_ids']=['missing']
                return response
        model=ShiftedCitation(self.payload)
        with tempfile.TemporaryDirectory() as tmp:
            run_batched(self.payload,model,tmp,batch_size=4,workers=1)
            raw=json.loads((Path(tmp)/'batch-000-001.json').read_text())
            saved=json.loads((Path(tmp)/'batch-000.checkpoint.json').read_text())
            audit=json.loads((Path(tmp)/'batch-000-001.citation-alignments.json').read_text())
            self.assertEqual(raw['units'][0]['citations'][0]['line_ids'],['missing'])
            self.assertEqual(saved['response']['units'][0]['citations'][0]['line_ids'],['L1'])
            self.assertEqual(audit['raw_response_fingerprint'],fingerprint(raw))
            self.assertEqual(len(model.calls),3)


class GlobalTests(unittest.TestCase):
    def setUp(self):
        self.payload=fixture()[1];self.registry=canonical_registry(self.payload)
        self.proposal={'input_fingerprint':self.payload['input_fingerprint'],
                      'units':[make_unit(u,self.payload) for u in self.registry],
                      'rules':[],'unresolved_questions':[]}
        self.citations=copy.deepcopy(self.proposal['units'][0]['citations'])

    def test_merge_preserves_members_and_redirects_cross_batch_edge(self):
        a,b,c,d=[u['id'] for u in self.registry]
        self.proposal['units'][2]['requirements']=[{'mode':'ALL','selected':None,'members':[
            {'unit_id':b,'lag_days':{'min':0,'max':0},'citations':self.citations}]}]
        changes=empty_patch();changes['merges']=[{'target_id':a,'source_ids':[b],
                                                'reason':'same completion','citations':self.citations}]
        result=apply_global_patch(self.proposal,changes,self.payload)
        self.assertEqual(len(result['units']),3)
        self.assertEqual(len(result['units'][0]['member_refs']),2)
        self.assertEqual(result['units'][1]['requirements'][0]['members'][0]['unit_id'],a)
        self.assertEqual(compile_report(self.payload,result)['status'],'DRAFT')
        self.assertEqual(len(self.proposal['units']),4)

    def test_scope_mismatch_and_membership_edits_are_rejected(self):
        a,b=[u['id'] for u in self.registry[:2]]
        self.proposal['units'][1]['scope']['status']='UNKNOWN'
        changes=empty_patch();changes['merges']=[{'target_id':a,'source_ids':[b],
                                                'reason':'same','citations':self.citations}]
        with self.assertRaisesRegex(ValueError,'scope'):apply_global_patch(self.proposal,changes,self.payload)
        changes=empty_patch();changes['unit_updates']=[{'id':a,'changes':{'member_refs':[]}}]
        with self.assertRaisesRegex(ValueError,'membership'):apply_global_patch(self.proposal,changes,self.payload)

    def test_final_graph_still_rejects_cross_batch_cycles(self):
        a,b=[u['id'] for u in self.registry[:2]]
        changes=empty_patch()
        for target,source in [(a,b),(b,a)]:
            changes['unit_updates'].append({'id':target,'changes':{'requirements':[
                {'mode':'ALL','selected':None,'members':[{'unit_id':source,
                    'lag_days':{'min':0,'max':0},'citations':self.citations}]}]}})
        result=compile_report(self.payload,apply_global_patch(self.proposal,changes,self.payload))
        self.assertEqual(result['status'],'BLOCKED');self.assertEqual(result['schedules'],{})
        self.assertTrue(any(e['code']=='DEPENDENCY_CYCLE' for e in result['errors']))

    def test_global_failure_never_writes_final_checkpoint(self):
        class InvalidGlobal(Factory):
            def extract(self,prompt):
                result=super().extract(prompt)
                if 'units' not in result: result['unit_updates']=[{'id':'missing','changes':{}}]
                return result
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'Global review remains blocked'):
                run_batched(self.payload,InvalidGlobal(self.payload),tmp,batch_size=4,workers=1)
            self.assertFalse((Path(tmp)/'final.checkpoint.json').exists())
            self.assertEqual(len(list(Path(tmp).glob('scope-part-000-[0-9][0-9][0-9]*.json'))),6) # raw + validation

    def test_global_resume_replays_prior_corrections_before_repair(self):
        uid=self.registry[0]['id']
        class InterruptedGlobal(Factory):
            def extract(inner,prompt):
                result=super(InterruptedGlobal,inner).extract(prompt)
                if inner.calls[-1].get('review_stage')=='timing':
                    if len(inner.calls)>3: raise RuntimeError('interrupted review')
                    result['unit_updates']=[{'id':uid,'changes':{'name':'Preserved correction'}}]
                    result['rules']=[{'id':'bad','target':'unit:'+uid,'anchor':'anchor:missing',
                        'offset':{'unit':'days','min':0,'max':0},'kind':'PLANNING_TARGET',
                        'citations':self.citations,'assumptions':[]}]
                return result
        model=InterruptedGlobal(self.payload)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):run_batched(self.payload,model,tmp,batch_size=4,workers=1)
            repair=Factory(self.payload)
            result=run_batched(self.payload,repair,tmp,batch_size=4,workers=1)
            self.assertEqual(len(repair.calls),1)
            self.assertEqual(repair.calls[0]['proposal']['units'][0]['name'],'Preserved correction')
            self.assertTrue(repair.calls[0]['VALIDATION_ERRORS'])
            self.assertEqual(result['units'][0]['name'],'Preserved correction')

    def test_scope_stage_cannot_silently_change_dependency_graph(self):
        uid=self.registry[0]['id']
        class WrongStage(Factory):
            def extract(inner,prompt):
                response=super(WrongStage,inner).extract(prompt)
                if inner.calls[-1].get('review_stage')=='scope':
                    response['unit_updates']=[{'id':uid,'changes':{'requirements':[]}}]
                return response
        model=WrongStage(self.payload)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'Scope stage cannot modify'):
                run_batched(self.payload,model,tmp,batch_size=4,workers=1)
            self.assertFalse((Path(tmp)/'scope.checkpoint.json').exists())
            self.assertFalse(any(c.get('review_stage')=='timing' for c in model.calls))

    def test_scope_review_bounds_output_targets_but_keeps_full_context(self):
        proposal=copy.deepcopy(self.proposal)
        proposal['units']=[dict(copy.deepcopy(proposal['units'][0]),id=f'u{i}') for i in range(50)]
        model=Factory(self.payload)
        with tempfile.TemporaryDirectory() as tmp:
            review_scope_chunks(self.payload,proposal,model,Path(tmp),'binding',2,lambda p:p)
            self.assertEqual(sorted(len(c['requested_ids']) for c in model.calls),[2,24,24])
            self.assertTrue(all(len(c['proposal']['units'])==50 for c in model.calls))
            review_scope_chunks(self.payload,proposal,model,Path(tmp),'binding',2,lambda p:p)
            self.assertEqual(len(model.calls),3)

    def test_unassigned_scope_updates_are_audited_and_reviewed_by_their_owner(self):
        proposal=copy.deepcopy(self.proposal)
        proposal['units']=[dict(copy.deepcopy(proposal['units'][0]),id=f'u{i}') for i in range(25)]
        class ExtraUpdates(Factory):
            def extract(inner,prompt):
                response=super(ExtraUpdates,inner).extract(prompt)
                response['unit_updates']=[{'id':'u0','changes':{'name':'Reviewed zero'}},
                                          {'id':'u24','changes':{'name':'Reviewed twenty-four'}}]
                return response
        model=ExtraUpdates(self.payload)
        with tempfile.TemporaryDirectory() as tmp:
            result=review_scope_chunks(self.payload,proposal,model,Path(tmp),'binding',1,lambda p:p)
            self.assertEqual(result['units'][0]['name'],'Reviewed zero')
            self.assertEqual(result['units'][24]['name'],'Reviewed twenty-four')
            audit=json.loads((Path(tmp)/'scope-part-000-001.unassigned-updates.json').read_text())
            self.assertEqual(audit['deferred_updates'][0]['id'],'u24')
            saved=json.loads((Path(tmp)/'scope-part-000.checkpoint.json').read_text())
            self.assertEqual([u['id'] for u in saved['response']['unit_updates']],['u0'])

if __name__=='__main__':unittest.main()
