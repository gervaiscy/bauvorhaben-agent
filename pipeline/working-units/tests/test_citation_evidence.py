from consequence_fixtures import assessment_answer
from historical_facts import collect_historical_facts
from clause_fixtures import decode_prompt
from clause_fixtures import index_answer
"""Evidence identity, faithful resolution, bounded repair and immutable raw records."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bauvo_working_units.citation_evidence import catalog, resolve, canonical
from bauvo_working_units.citation_repair import repair_saved_result, main as repair_main
from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.project_facts import collect_project_facts
from bauvo_working_units.fact_artifact import validate_facts
from bauvo_working_units.project_flow import Document
from bauvo_working_units.recovery import RequestRecovery
from bauvo_working_units.trace import ModelRequestFailed, RawRunRecorder, TracingModel
from bauvo_working_units.schemas import RESPONSE_SCHEMAS


def page(text='Original: Vertragsumme.\nWeitere Bedingung.', source='contract', number=1):
    return {'source_key':source,'document_id':'D1','pdf_page':number,'text':text}


def unit(citation):
    return {'working_units':[{'name':'Prüfen','action':'Bedingung prüfen',
        'actor':None,'performed_by':None,'citations':[citation]}],'carry_forward':''}


def document(source='contract', pages=None, kind='project'):
    return Document('D1','Source',source+'.pdf',schema_version=6,source_key=source,
        source_snapshot=source+'-snapshot',source_type=kind,
        pages=tuple(pages or [{'pdf_page':1,'text':'Original: Vertragsumme.\nWeitere Bedingung.'}]))


class EvidenceReferenceTests(unittest.TestCase):
    def test_ids_bind_original_text_identity_offsets_and_page_hash_and_cover_every_character(self):
        original=page(' Erster Satz. Zweiter Satz.\n\nDritte Zeile.\n'+'Langes Wort '*250)
        entries=catalog([original]);self.assertEqual(entries,catalog([original,copy.deepcopy(original)]))
        covered=set()
        for entry in entries:
            self.assertEqual(entry['quote'],original['text'][entry['start']:entry['end']])
            covered.update(range(entry['start'],entry['end']))
        self.assertTrue(all(i in covered for i,c in enumerate(original['text']) if not c.isspace()))
        for changed in ({**original,'source_key':'another'}, {**original,'document_id':'D2'},
                        {**original,'pdf_page':2}, {**original,'text':original['text']+' extra'}):
            self.assertTrue({e['evidence_id'] for e in entries}.isdisjoint(
                            {e['evidence_id'] for e in catalog([changed])}))
        with self.assertRaisesRegex(ValueError,'Conflicting evidence source'):
            catalog([original,{**original,'text':'conflicting text'}])

    def test_reference_assembles_exact_source_and_ignores_model_typographic_corrections(self):
        source=page();entry=catalog([source])[0]
        response=unit({'evidence_id':entry['evidence_id'],'quote':'Corrected Vertragssumme',
                       'source_key':'wrong','pdf_page':0})
        original=copy.deepcopy(response)
        resolved,audit,issues=resolve(response,[source],[],'contract','D1')
        self.assertEqual(issues,[])
        self.assertEqual(resolved['working_units'][0]['citations'],[canonical(entry)])
        self.assertEqual(response,original)
        self.assertEqual(audit['bindings'][0]['method'],'evidence_reference')

    def test_unique_exact_legacy_rebinding_is_separate_from_original_and_never_uses_similarity(self):
        source=page('Die Leistung ist vereinbart.','amendment',12)
        response=unit({'pdf_page':0,'quote':'Die Leistung ist vereinbart.'})
        original=copy.deepcopy(response)
        resolved,audit,issues=resolve(response,[],[source],'wrong','D1',allow_exact_rebinding=True)
        self.assertEqual(issues,[])
        self.assertEqual(resolved['working_units'][0]['citations'][0]['pdf_page'],12)
        self.assertEqual(audit['original_findings'][0]['code'],'citation_page_outside_window')
        self.assertEqual(response,original)
        for other in ([source,{**source,'source_key':'other'}], [{**source,'text':'Die Leistung war vereinbart.'}]):
            _,audit,issues=resolve(response,[],other,'wrong','D1',allow_exact_rebinding=True)
            self.assertTrue(issues);self.assertEqual(audit['bindings'],[])

    def test_one_repair_selects_known_source_ids_and_preserves_claim_and_raw_repair(self):
        source=page();response=unit({'evidence_id':'unknown'});original=copy.deepcopy(response);calls=[]
        def model(prompt):
            calls.append(prompt);data=decode_prompt(prompt)
            self.assertEqual(len(data['targets']),1)
            self.assertEqual(data['targets'][0]['claim']['action'],'Bedingung prüfen')
            return {'decisions':[{'path':data['targets'][0]['path'],'status':'SUPPORTED',
                'citations':[{'evidence_id':data['evidence_catalog'][0]['evidence_id']}],'reason':'Source passage'}]}
        resolved,audit,issues=resolve(response,[source],[],'contract','D1',model=SimpleNamespace(extract=model))
        self.assertEqual(len(calls),1);self.assertEqual(issues,[])
        self.assertEqual(audit['repair']['status'],'resolved')
        self.assertIn('gemini',audit['repair'])
        self.assertEqual(resolved['working_units'][0]['action'],original['working_units'][0]['action'])
        self.assertEqual(response,original)

    def test_unsupported_ambiguous_missing_duplicate_malformed_and_foreign_repairs_stay_unresolved(self):
        source=page();response=unit({'evidence_id':'unknown'})
        for mode in ('UNSUPPORTED','AMBIGUOUS','missing','duplicate','malformed','foreign','wrong_path'):
            calls=[]
            def model(prompt):
                calls.append(prompt);data=decode_prompt(prompt)
                row={'path':data['targets'][0]['path'],'status':mode if mode in ('UNSUPPORTED','AMBIGUOUS') else 'SUPPORTED',
                     'citations':[{'evidence_id':data['evidence_catalog'][0]['evidence_id']}],'reason':'Review'}
                if mode in ('UNSUPPORTED','AMBIGUOUS'):row['citations']=[]
                if mode=='foreign':row['citations']=[{'evidence_id':'foreign'}]
                if mode=='wrong_path':row['path']='$.working_units[999].citations'
                return {'decisions':[] if mode=='missing' else [row,row] if mode=='duplicate' else [None] if mode=='malformed' else [row]}
            with self.subTest(mode=mode):
                resolved,audit,issues=resolve(response,[source],[],'contract','D1',model=SimpleNamespace(extract=model))
                self.assertEqual(len(calls),1);self.assertTrue(issues)
                self.assertEqual(audit['repair']['status'],'unresolved')
                self.assertEqual(resolved,response)

    def test_failed_repair_is_recorded_and_remains_a_request_failure(self):
        def fail(prompt):raise ModelRequestFailed([{'finish_reason':'MAX_TOKENS'}])
        _,audit,issues=resolve(unit({'evidence_id':'bad'}),[page()],[],'contract','D1',model=SimpleNamespace(extract=fail))
        self.assertEqual(audit['repair']['status'],'request_failed')
        self.assertIn('model_request_failed',{f['code'] for f in issues})

    def test_unknown_id_cannot_pass_with_a_valid_looking_quote_or_source_coordinates(self):
        source=page()
        response=unit({'evidence_id':'unknown','pdf_page':1,'quote':source['text']})
        _,_,issues=resolve(response,[source],[],'contract','D1')
        self.assertEqual([issue['code'] for issue in issues],['unknown_evidence_id'])

    def test_positive_applicability_with_empty_evidence_remains_flagged(self):
        response={'assessments':[{'unit_id':'U1','applicability':'APPLIES',
                                  'reason':'Agreement','citations':[]}]}
        _,_,issues=resolve(response,[page()],[],'contract','D1')
        self.assertEqual([issue['code'] for issue in issues],['missing_citations'])

    def test_repair_keeps_valid_evidence_and_repairs_multiple_claims_in_one_request(self):
        source=page('Die Leistung ist vereinbart.\nSie gilt nur bei rechtzeitiger Zahlung.')
        valid=catalog([source])[0]
        response=unit({'evidence_id':valid['evidence_id']})
        response['working_units'][0]['citations'].append({'evidence_id':'unknown'})
        response['working_units'].append(copy.deepcopy(response['working_units'][0]))
        calls=[]
        def model(prompt):
            calls.append(prompt);data=decode_prompt(prompt)
            return {'decisions':[{'path':target['path'],'status':'SUPPORTED','reason':'Condition',
                'citations':[{'evidence_id':data['evidence_catalog'][-1]['evidence_id']}]} for target in data['targets']]}
        resolved,audit,issues=resolve(response,[source],[],'contract','D1',model=SimpleNamespace(extract=model))
        self.assertEqual(len(calls),1);self.assertEqual(issues,[])
        for row in resolved['working_units']:
            self.assertIn(canonical(valid),row['citations'])
            self.assertEqual(len(row['citations']),2)
        self.assertEqual(response['working_units'][0]['citations'][1],{'evidence_id':'unknown'})


class EvidenceFlowTests(unittest.TestCase):
    def test_conflicting_primary_and_context_pages_fail_before_any_request(self):
        original=document();conflict=document(pages=[{'pdf_page':1,'text':'Conflicting original source.'}])
        with self.assertRaisesRegex(ValueError,'Conflicting evidence source pages'):
            extract_page_units([original],SimpleNamespace(extract=lambda _:self.fail('No request')),
                               context_documents=[conflict],stop_after='PAGE_EXTRACTION')

    def test_fact_and_task_ids_keep_provenance_through_handoffs_and_all_assessments(self):
        contract=document();blb=document('blb',[{'pdf_page':1,'text':'Eine Aufgabe erledigen.'}],'blb')
        answers=[];calls=[]
        def model(prompt):
            phase=prompt.split('.',1)[0];data=decode_prompt(prompt);calls.append((phase,data))
            if prompt.startswith('PAGE_CONSEQUENCES.'):
                out = assessment_answer(json.loads(prompt.split('SOURCE DATA:\n', 1)[1]))
                answers.append(out)
                return out
            evidence=data['evidence_catalog'][0]['evidence_id'] if phase not in ('PAGE_OFFERS','PAGE_TIMING') else None
            if phase=='PAGE_PROJECT_FACTS':
                response=index_answer(data)
            elif phase=='PAGE_EXTRACTION':response=unit({'evidence_id':evidence})
            elif phase=='PAGE_OFFERS':response={'offers':[]}
            else:
                if phase == 'PAGE_TIMING':
                    fact = data['project_facts']['facts'][0]
                    self.assertEqual(set(fact), {'id', 'text'})
                    response = {'assessments': [{'unit_id': u['id'], 'answer': 'Regelung beachten',
                        'citations': [{'fact_id': fact['id']}]} for u in data['working_units']]}
                    answers.append(copy.deepcopy(response))
                    return response
                if phase == 'PAGE_APPLICABILITY':
                    fact=data['project_facts']['facts'][0]
                    self.assertEqual((fact['source_key'],fact['document_id']),('contract','D1'))
                    self.assertEqual(fact['citations'][0]['pdf_page'],1)
                    self.assertTrue(fact['text'])
                    evidence=fact['citations'][0]['evidence_id']
                    self.assertIn(evidence,{e['evidence_id'] for e in data['evidence_catalog']})
                response={'assessments':[{'unit_id':u['id'],'answer':'Regelung beachten',
                    'citations':[{'evidence_id':evidence}]} for u in data['working_units']]}
            answers.append(copy.deepcopy(response));return response
        provider=SimpleNamespace(extract=model)
        facts=collect_historical_facts([contract],provider)
        self.assertEqual(validate_facts(facts,[contract]),facts)
        result=extract_page_units([blb],provider,context_documents=[contract],project_facts=facts)
        self.assertEqual(result['review_findings'],[])
        self.assertEqual([r['gemini'] for r in result['gemini_responses']],answers[1:])
        task=result['working_units'][0]
        self.assertEqual(task['gemini']['citations'],answers[1]['working_units'][0]['citations'])
        self.assertEqual(task['derived']['citations'][0]['source_key'],'blb')
        self.assertEqual(task['reviews'][0]['derived']['resolved_evidence']['citations'][0]['source_key'],'contract')
        self.assertEqual(len(calls),6)

    def test_bad_clause_reference_is_preserved_without_a_repair_call(self):
        doc=document();calls=[]
        def model(prompt):
            calls.append(prompt)
            return {'verpflichtungen':[{'zitate':[{'evidence_id':'bad'}]}],
                    'nicht_als_fakt_erfasst':[]}
        facts=collect_historical_facts([doc],SimpleNamespace(extract=model))
        self.assertEqual(len(calls),1)
        self.assertEqual(facts['status'],'incomplete')
        self.assertEqual(facts['facts'][0]['gemini']['zitate'],[{'evidence_id':'bad'}])
        self.assertEqual(facts['facts'][0]['derived']['verified_citations'],[])
        self.assertEqual(validate_facts(facts,[doc]),facts)
        changed=copy.deepcopy(facts);changed['gemini_responses'][0]['coverage']['missing_ids']=[]
        with self.assertRaises(ValueError):validate_facts(changed,[doc])

    def test_invalid_offer_raw_trace_and_recovery_replay_without_a_paid_repair(self):
        doc=document();calls=[]
        class Provider:
            last_raw_response=None;last_finish_reason=None
            interrupt=True
            def extract(self,prompt):
                self.last_raw_response=None;self.last_finish_reason=None
                calls.append(prompt)
                if prompt.startswith('PAGE_CITATION_REPAIR.') and self.interrupt:
                    self.interrupt=False;raise KeyboardInterrupt()
                data=decode_prompt(prompt)
                if prompt.startswith('PAGE_CONSEQUENCES.'):
                    out = assessment_answer(json.loads(prompt.split('SOURCE DATA:\n', 1)[1]))
                    self.last_raw_response = json.dumps(out); self.last_finish_reason = 'STOP'
                    return out
                if prompt.startswith('PAGE_EXTRACTION.'):
                    value={'working_units':[],'carry_forward':''}
                elif prompt.startswith('PAGE_OFFERS.'):
                    value={'offers':[{'description':'Separate service','citations':[{'evidence_id':'bad'}]}]}
                else:
                    value={'decisions':[{'path':data['targets'][0]['path'],'status':'SUPPORTED',
                        'citations':[{'evidence_id':data['evidence_catalog'][0]['evidence_id']}],'reason':'Original'}]}
                self.last_raw_response=json.dumps(value);self.last_finish_reason='STOP';return value
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);provider=Provider();identity={'project':'unused','location':'global','model':'fixture'}
            def recorded(folder,previous):
                return TracingModel(RawRunRecorder(RequestRecovery(provider,folder,previous,identity),folder/'raw.json'),folder/'trace')
            first=extract_page_units([doc],recorded(root/'first',[]),stop_after='PAGE_OFFERS')
            self.assertEqual(first['status'],'incomplete')
            original={p:p.read_bytes() for p in (root/'first').rglob('*') if p.is_file()}
            result=extract_page_units([doc],recorded(root/'second',[root/'first']),stop_after='PAGE_OFFERS')
            self.assertEqual(sum(prompt.startswith('PAGE_EXTRACTION.') for prompt in calls),1)
            self.assertEqual(sum(prompt.startswith('PAGE_OFFERS.') for prompt in calls),1)
            self.assertEqual(result['review_findings'],first['review_findings'])
            self.assertEqual(result['status'],'incomplete')
            self.assertFalse(any(p.startswith('PAGE_CITATION_REPAIR.') for p in calls))
            self.assertEqual(json.loads((root/'second/raw.json').read_text())[-1],
                result['gemini_responses'][-1]['gemini'])
            self.assertEqual(original,{p:p.read_bytes() for p in (root/'first').rglob('*') if p.is_file()})

    def test_saved_result_correction_keeps_raw_proposals_findings_and_input_fingerprints(self):
        doc=document(pages=[{'pdf_page':12,'text':'Originale Vereinbarung.'}])
        baseline=extract_page_units([doc],SimpleNamespace(extract=lambda _:unit(
            {'pdf_page':0,'quote':'Originale Vereinbarung.'})),stop_after='PAGE_EXTRACTION')
        before=copy.deepcopy(baseline)
        result=repair_saved_result(baseline,[doc])
        self.assertEqual(result['review_findings'],[])
        self.assertEqual(result['working_units'][0]['gemini'],baseline['working_units'][0]['gemini'])
        self.assertEqual(result['working_units'][0]['derived']['citations'][0]['pdf_page'],12)
        self.assertEqual(result['citation_repair']['original_review_findings'],baseline['review_findings'])
        self.assertEqual(result['source_snapshots'],baseline['source_snapshots'])
        self.assertEqual(baseline,before)
        with self.assertRaisesRegex(ValueError,'original source'):
            repair_saved_result(baseline,[document(pages=[{'pdf_page':12,'text':'Changed.'}])])

    def test_repair_schema_is_scoped_to_paths_and_known_reference_shape(self):
        schema=RESPONSE_SCHEMAS['PAGE_CITATION_REPAIR']['properties']['decisions']['items']
        self.assertEqual(set(schema['properties']),{'path','status','citations','reason'})
        self.assertEqual(schema['properties']['citations']['items']['required'],['evidence_id'])
        for phase,key,field in (('PAGE_OFFERS','offers','citations'),):
            citations=RESPONSE_SCHEMAS[phase]['properties'][key]['items']['properties'][field]
            self.assertEqual(citations['minItems'],1)
            self.assertEqual(citations['items']['required'],['passage'])
        quote=RESPONSE_SCHEMAS['PAGE_EXTRACTION']['properties']['working_units']['items']['properties']['quote']
        self.assertEqual(quote['minLength'],1)
        self.assertEqual(quote['type'],'string')
        right = RESPONSE_SCHEMAS['PAGE_PROJECT_FACTS']['properties']['ansprueche']['items']
        self.assertEqual(right['properties']['belege']['minItems'], 1)
        self.assertEqual(right['properties']['belege']['items']['type'], 'string')

    def test_saved_run_cli_validates_sources_and_writes_a_separate_audit_without_provider_calls(self):
        from test_modular_pipeline import ModularPipelineTests, FakeModel
        fixture=ModularPipelineTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        class WrongPage(FakeModel):
            def extract(self,prompt):
                answer=super().extract(prompt)
                if prompt.startswith('PAGE_EXTRACTION.'):
                    answer['working_units'][0]['citations'][0]['pdf_page']=0
                return answer
        fixture.run_pipeline(WrongPage())
        originals={p:p.read_bytes() for p in fixture.output.rglob('*') if p.is_file()}
        output=fixture.root/'audit'
        argv=['citation-repair','--run',str(fixture.output),'--output',str(output)]
        with patch('sys.argv',argv), patch('bauvo_working_units.gemini.GeminiModel') as provider:
            repair_main()
            provider.assert_not_called()
        self.assertTrue((output/'summary.json').exists())
        self.assertEqual(originals,{p:p.read_bytes() for p in fixture.output.rglob('*') if p.is_file()})
        for source in ('contract-01','blb-01'):
            result=json.loads((output/(source+'.json')).read_text())
            self.assertEqual(result['working_units'][0]['gemini']['citations'][0]['pdf_page'],0)
        with patch('sys.argv',argv),self.assertRaises(SystemExit):repair_main()
