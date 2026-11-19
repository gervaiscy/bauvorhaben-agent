"""Consequence batching, strict timing preconditions, raw preservation and recovery."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from bauvo_working_units.phases.consequences.stage import extract_consequences
from bauvo_working_units.phases.consequences.validation import response_findings
from bauvo_working_units.phases.consequences.artifact import review_view, write_outputs, load_artifact
from bauvo_working_units.cache import CachedModel
from bauvo_working_units.call_budget import BudgetedModel, CallBudget, CallLimitExceeded
from bauvo_working_units.trace import ModelRequestFailed, RawRunRecorder
from consequence_fixtures import baseline, assessment_answer


class ConsequenceSinglePassTests(unittest.TestCase):
    def test_110_tasks_use_11_calls_across_documents_with_timing_and_full_blb_once(self):
        saved, docs, context = baseline((55,55)); before = copy.deepcopy(saved); calls = []
        def provider(prompt):
            self.assertTrue(prompt.startswith('PAGE_CONSEQUENCES.'))
            data = json.loads(prompt.split('SOURCE DATA:\n')[1]); calls.append(data)
            self.assertEqual(len(data['working_units']), 10)
            self.assertEqual(list(data['working_units']), [f'U{i+1}' for i in range(10)])
            self.assertEqual(sum(len(d['pages']) for d in data['source_documents']), 26)
            self.assertEqual([p['text'] for d in data['source_documents'] for p in d['pages']],
                             [p['text'] for d in docs for p in d.pages])
            self.assertNotIn(context[0].pages[0]['text'], [p['text'] for d in data['source_documents'] for p in d['pages']])
            self.assertTrue(data['project_facts'])
            for fact in data['project_facts']: self.assertEqual(set(fact), {'id','text'})
            self.assertEqual(set(data), {'working_units','source_documents','project_facts','source_scope'})
            for row in data['working_units'].values():
                self.assertEqual(set(row), {'title','actor','quote','timing'})
                self.assertEqual(row['timing'], {'answer':'NOT_STATED','citations':[]})
            return assessment_answer(data)
        result = extract_consequences(saved, docs, SimpleNamespace(extract=provider), context_documents=context)
        self.assertEqual(len(calls), 11); self.assertEqual(result['status'], 'recorded')
        self.assertEqual(saved, before)
        for prior, after in zip(saved['working_units'], result['working_units']):
            self.assertEqual(prior['reviews'], after['reviews'][:-1])
            self.assertEqual(prior['gemini'], after['gemini'])
            self.assertEqual(after['reviews'][-1]['gemini']['classification'], 'NOT_STATED')
        self.assertEqual({r['pass'] for r in result['gemini_responses'][len(saved['gemini_responses']):]}, {'PAGE_CONSEQUENCES'})

    def test_missing_empty_or_duplicate_timing_anywhere_blocks_all_calls(self):
        saved, docs, context = baseline((11,))
        for mode in ('missing','empty','duplicate'):
            with self.subTest(mode=mode):
                broken = copy.deepcopy(saved); unit = broken['working_units'][-1]
                timing = next(r for r in unit['reviews'] if r['kind']=='PAGE_TIMING')
                if mode=='missing': unit['reviews'].remove(timing)
                elif mode=='duplicate': unit['reviews'].append(copy.deepcopy(timing))
                else: timing['gemini']['answer']='  '
                with self.assertRaisesRegex(ValueError, 'requires exactly one timing'):
                    extract_consequences(broken, docs, SimpleNamespace(extract=lambda _: self.fail('No provider calls')),
                                         context_documents=context)

    def test_existing_timing_answer_and_citations_are_carried_into_each_task(self):
        saved, docs, context = baseline(); timing=next(r for r in saved['working_units'][0]['reviews'] if r['kind']=='PAGE_TIMING')
        timing['gemini']['answer']='Vor Baubeginn.'
        def provider(prompt):
            data=json.loads(prompt.split('SOURCE DATA:\n')[1]);self.assertEqual(data['working_units']['U1']['timing']['answer'],'Vor Baubeginn.')
            return assessment_answer(data,'DERIVED','Fehlt das Bauwasser, bleibt die Startvoraussetzung unerfüllt (BLB-PDF-Seite 1).')
        result=extract_consequences(saved,docs,SimpleNamespace(extract=provider),context_documents=context)
        with TemporaryDirectory() as tmp:
            output=Path(tmp)/'result.json';write_outputs(output,result)
            view=json.loads(output.read_text());self.assertEqual(set(view['working_units'][0]['consequences']),{'classification','reasoning'})
            self.assertEqual(load_artifact(output),result)
            view['working_units'][0]['consequences']['reasoning']='Modified';output.write_text(json.dumps(view))
            with self.assertRaisesRegex(ValueError,'does not match'):load_artifact(output)

    def test_invalid_completed_answer_is_preserved_and_replayed_without_regeneration(self):
        saved, docs, context=baseline(); calls=[]
        answer={'U1':{'classification':'EXPLICIT','reasoning':'Supported wording','extra':'not allowed'}}
        def provider(prompt):calls.append(prompt);return copy.deepcopy(answer)
        with TemporaryDirectory() as tmp:
            cached=CachedModel(SimpleNamespace(extract=provider),tmp,{'model':'fixture'})
            for _ in range(2):
                result=extract_consequences(saved,docs,cached,context_documents=context)
                self.assertEqual(result['status'],'incomplete')
                self.assertEqual(result['gemini_responses'][-1]['gemini'],answer)
                self.assertEqual(result['working_units'],saved['working_units'])
            self.assertEqual(len(calls),1)

    def test_wrong_keys_classification_and_empty_reasoning_are_blocking(self):
        for answer in ([],{}, {'U2':{'classification':'EXPLICIT','reasoning':'X'}},
                       {'U1':{'classification':'MIXED','reasoning':'X'}},
                       {'U1':{'classification':'DERIVED','reasoning':'  '}}):
            self.assertTrue(response_findings(answer,{'U1':'original-task'}))

    def test_standalone_downstream_cannot_bypass_incomplete_consequences(self):
        from bauvo_working_units.phases.applicability.stage import extract_applicability
        saved,docs,context=baseline()
        result=extract_consequences(saved,docs,SimpleNamespace(extract=lambda _: {}),context_documents=context)
        self.assertEqual(result['status'],'incomplete')
        with self.assertRaisesRegex(ValueError,'Complete consequences'):
            extract_applicability(result,docs,SimpleNamespace(extract=lambda _: self.fail('No downstream call')),
                                  context_documents=context)

    def test_entire_plan_is_checked_before_provider_calls(self):
        saved, docs, context=baseline((11,))
        with TemporaryDirectory() as tmp:
            budget=CallBudget(1); backend=CachedModel(BudgetedModel(lambda:self.fail('No provider creation'),budget),tmp,{'model':'fixture'},budget=budget)
            with self.assertRaises(CallLimitExceeded):extract_consequences(saved,docs,backend,context_documents=context)
            self.assertEqual(budget.used,0)

    def test_failed_batch_does_not_discard_later_success_or_trigger_other_passes(self):
        saved,docs,context=baseline((11,));calls=[]
        def provider(prompt):
            calls.append(prompt)
            if len(calls)==1:raise ModelRequestFailed([{'reason':'timeout'}])
            return assessment_answer(json.loads(prompt.split('SOURCE DATA:\n')[1]))
        result=extract_consequences(saved,docs,SimpleNamespace(extract=provider),context_documents=context)
        self.assertEqual(len(calls),2);self.assertEqual(result['status'],'request_failed')
        self.assertEqual(sum(any(r['kind']=='PAGE_CONSEQUENCES' for r in u['reviews']) for u in result['working_units']),1)
        self.assertEqual(result['gemini_responses'][-2]['status'],'request_failed')

    def test_recovery_reuses_completed_batch_after_interruption_with_exact_raw_json(self):
        saved,docs,context=baseline((11,));calls=[]
        class Provider:
            interrupt=True
            def extract(self,prompt):
                calls.append(prompt);self.last_raw_response=None;self.last_finish_reason=None
                data=json.loads(prompt.split('SOURCE DATA:\n')[1])
                if len(data['working_units'])==1 and self.interrupt:raise KeyboardInterrupt()
                value=assessment_answer(data);self.last_raw_response=json.dumps(value);self.last_finish_reason='STOP';return value
        with TemporaryDirectory() as tmp:
            provider=Provider();cached=CachedModel(provider,Path(tmp)/'cache',{'model':'fixture'})
            with self.assertRaises(KeyboardInterrupt):extract_consequences(saved,docs,RawRunRecorder(cached,Path(tmp)/'old.json'),context_documents=context)
            provider.interrupt=False
            result=extract_consequences(saved,docs,RawRunRecorder(cached,Path(tmp)/'new.json'),context_documents=context)
            self.assertEqual(result['status'],'recorded');self.assertEqual(len(calls),3)
            raw=json.loads((Path(tmp)/'new.json').read_text());self.assertEqual(len(raw),2)
            self.assertEqual(raw[0],assessment_answer({'working_units':{f'U{i+1}':{} for i in range(10)}}))
