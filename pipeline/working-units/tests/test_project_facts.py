from consequence_fixtures import assessment_answer
from historical_facts import collect_historical_facts
from clause_fixtures import labeled_answer
from clause_fixtures import decode_prompt
"""Source clauses retain original wording, provenance and complete passage accounting."""
import copy
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from bauvo_working_units.project_flow import Document, extract_project_units
from bauvo_working_units.project_facts import collect_project_facts, facts_view, evidence_pages
from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.schemas import RESPONSE_SCHEMAS
from bauvo_working_units.gemini import IncompleteResponse
from bauvo_working_units.trace import RetryingModel, RawRunRecorder
from clause_fixtures import entries_for, answer_for, index_answer, interpreted


def document(key, text, number=1, identifier='D1'):
    return Document(identifier,'Contract',key+'.pdf',schema_version=6,source_key=key,
                    pages=({'pdf_page':number,'text':text},))


def proposal(doc, quote=None):
    entries = entries_for(doc)
    return interpreted([{'evidence_id': e['evidence_id']} for e in entries
                      if quote is None or quote in e['quote']] or [{'evidence_id':'unknown'}])


class ProjectFactsTests(unittest.TestCase):
    def test_schema_requires_interpretation_uncertainty_and_source_references(self):
        from bauvo_working_units.phases.project_facts.schema import CLAUSE_INDEX
        fields=CLAUSE_INDEX['properties']
        self.assertEqual(set(fields),{'verpflichtungen','nicht_als_fakt_erfasst'})
        self.assertEqual(fields['verpflichtungen']['items']['required'],['provision_id','status','related_provision_ids','uncertainty','claims','zitate'])
        self.assertEqual(fields['verpflichtungen']['items']['properties']['zitate']['type'],'array')
        self.assertNotIn('minItems', fields['verpflichtungen']['items']['properties']['zitate'])
        self.assertFalse(fields['verpflichtungen']['items']['additionalProperties'])
        self.assertEqual(set(fields['nicht_als_fakt_erfasst']['items']['required']),{'evidence_id','grund'})

    def test_current_prompt_and_exception_are_preserved_in_one_request_without_paraphrase(self):
        from bauvo_working_units.page_flow import PROMPT_DIRECTORY
        doc=document('contract','Die Frist verlängert sich bei Frost.\n\n'
                     'Für Estrich gilt dies nur ohne mögliche Beheizung.')
        calls=[];answers=[]
        def extract(prompt):
            instructions,payload=prompt.split('SOURCE DATA:\n',1)
            self.assertIn((PROMPT_DIRECTORY/'project_facts/prompt.txt').read_text(),instructions)
            data=decode_prompt(prompt);calls.append(data)
            self.assertEqual(data['source_pages'],[{**p,'source_key':doc.source_key,'document_id':doc.id} for p in doc.pages])
            row=proposal(doc);row['zitate'].reverse()
            answers.append(answer_for(doc,[row]));return copy.deepcopy(answers[-1])
        result=collect_historical_facts([doc],SimpleNamespace(extract=extract))
        self.assertEqual(len(calls),1)
        self.assertEqual(result['gemini_responses'][0]['gemini'],answers[0])
        fact=result['facts'][0]
        self.assertEqual(fact['gemini'],answers[0]['verpflichtungen'][0])
        self.assertEqual(fact['derived']['verified_citations'][0]['quote'],entries_for(doc)[0]['quote'])
        self.assertIn('nur ohne mögliche Beheizung',fact['derived']['text'])
        self.assertEqual(result['status'],'recorded')

    def test_all_documents_are_read_in_one_request_without_window_limits_or_duplicate_inputs(self):
        docs=[Document('D1','A','a.pdf',schema_version=6,source_key='contract',
                       pages=tuple({'pdf_page':n,'text':f'Clause {n}'} for n in range(1,8))),
              document('contract','Another form',8,'D2')]
        calls=[]
        def extract(prompt):
            data=decode_prompt(prompt);calls.append(data)
            self.assertEqual(set(data),{'source_pages','context_scope','documents','evidence_catalog','catalog_protocol','target_evidence_ids','evidence_protocol','reference_map','reference_protocol','provisions','provision_protocol','target_provision_ids'})
            self.assertTrue(all(set(p)=={'source_key','document_id','pdf_page','text'} for p in data['source_pages']))
            return index_answer(data)
        result=collect_historical_facts([*docs,docs[0]],SimpleNamespace(extract=extract),chunk_pages=3)
        self.assertEqual([w['primary_pages'] for w in result['windows']],[[1,2,3,4,5,6,7],[8]])
        self.assertEqual([[p['pdf_page'] for p in c['source_pages']] for c in calls],
                         [[1,2,3,4,5,6,7,8]])
        self.assertEqual(len(result['source_snapshots']),2)
        self.assertTrue(all(r['coverage']['status']=='complete' for r in result['gemini_responses']))

    def test_conflicting_snapshots_fail_before_any_request(self):
        with self.assertRaisesRegex(ValueError,'Conflicting source snapshots'):
            collect_historical_facts([document('contract','A'),document('contract','B')],
                                  SimpleNamespace(extract=lambda p:self.fail('Paid request')))

    def test_qualifications_payment_wording_and_amendments_are_never_rewritten(self):
        doc=document('contract','Die Bürgschaft gilt für Herstellung ohne erhebliche Mängel.\n\n'
            'Zahlungsbonus nur laut Nachtrag.\n\nZahlbar sofort ohne Abzug.\n\n'
            'Der Nachtrag ersetzt die ursprüngliche Sicherheit durch eine Abtretung oder Bürgschaft.')
        answer=answer_for(doc,[{'zitate':[{'evidence_id':e['evidence_id']}]} for e in entries_for(doc)])
        result=collect_historical_facts([doc],SimpleNamespace(extract=lambda p:copy.deepcopy(answer)))
        self.assertEqual([f['derived']['text'] for f in result['facts']], [e['quote'] for e in entries_for(doc)])
        self.assertEqual(result['gemini_responses'][0]['gemini'],answer)
        self.assertTrue(all(f['derived']['document_title']=='Contract' for f in result['facts']))

    def test_missing_passage_unknown_id_and_conflicting_disposition_are_incomplete_without_repair(self):
        doc=document('contract','Erste Regel.\n\nEine weitere Regel.')
        entries=entries_for(doc);a,b=[e['evidence_id'] for e in entries]
        cases=[({'verpflichtungen':[{'zitate':[{'evidence_id':a}]}], 'nicht_als_fakt_erfasst':[]},
                'unaccounted_source_passage'),
               ({'verpflichtungen':[{'zitate':[{'evidence_id':'fabricated'}]}], 'nicht_als_fakt_erfasst':[]},
                'unknown_evidence_id'),
               ({'verpflichtungen':[{'zitate':[{'evidence_id':a},{'evidence_id':b}]}],
                 'nicht_als_fakt_erfasst':[{'evidence_id':a,'grund':'Header'}]},
                'conflicting_passage_disposition')]
        for answer,code in cases:
            answer = labeled_answer([doc], answer)
            with self.subTest(code=code):
                calls=[]
                def extract(prompt):calls.append(prompt);return copy.deepcopy(answer)
                result=collect_historical_facts([doc],SimpleNamespace(extract=extract))
                self.assertEqual(len(calls),1)
                self.assertEqual(result['status'],'incomplete')
                self.assertIn(code,{f['code'] for f in result['review_findings']})
                self.assertEqual(result['gemini_responses'][0]['gemini'],answer)

    def test_excluded_passages_retain_original_evidence_and_a_reason(self):
        doc=document('contract','Eine konkrete Regel.\n\nKontakt: Beispielstraße 1.')
        answer=labeled_answer([doc], index_answer({'evidence_catalog':entries_for(doc)},lambda e:not e['quote'].startswith('Kontakt')))
        result=collect_historical_facts([doc],SimpleNamespace(extract=lambda p:answer))
        self.assertEqual(result['status'],'recorded')
        excluded=result['excluded_passages'][0]
        self.assertEqual(excluded['gemini'],answer['nicht_als_fakt_erfasst'][0])
        self.assertEqual(excluded['derived']['citation']['quote'],'Kontakt: Beispielstraße 1.')
        self.assertTrue(excluded['derived']['citation']['evidence_id'])

    def test_invalid_exclusions_duplicate_ids_empty_clauses_and_legacy_summaries_are_rejected(self):
        doc=document('contract','Eine Regel.');entry=entries_for(doc)[0]['evidence_id']
        valid=proposal(doc)
        cases=[{'verpflichtungen':[{'verpflichtung':'Rewritten',**valid}],'nicht_als_fakt_erfasst':[]},
               {'verpflichtungen':[{'zitate':[]}],'nicht_als_fakt_erfasst':[]},
               {'verpflichtungen':[{'zitate':[{'evidence_id':entry}]*2}],'nicht_als_fakt_erfasst':[]},
               {'verpflichtungen':[],'nicht_als_fakt_erfasst':[{'evidence_id':entry,'grund':''}]},
               {'verpflichtungen':[valid]}]
        for answer in cases:
            with self.subTest(answer=answer):
                result=collect_historical_facts([doc],SimpleNamespace(extract=lambda p:answer))
                self.assertEqual(result['status'],'incomplete')

    def test_ids_cannot_borrow_an_unsupplied_sources_passage_on_same_page(self):
        a=document('contract','Eine Wärmepumpe ist vereinbart.')
        b=document('offer','Eine Gasheizung wird angeboten.')
        unknown=document('missing','Nicht gelieferte Quelle.')
        def response(prompt):
            answer=index_answer(decode_prompt(prompt))
            answer['verpflichtungen'].insert(1,proposal(unknown))
            return answer
        result=collect_historical_facts([a,b],SimpleNamespace(extract=response))
        self.assertEqual(result['facts'][1]['derived']['verified_citations'],[])
        self.assertEqual([r['source_key'] for r in facts_view(result)['facts']],['contract',None,'offer'])
        self.assertEqual({p['source_key'] for p in evidence_pages(result)}, {'contract','offer'})

    def test_failed_full_context_retries_once_preserving_both_raw_attempts(self):
        doc=Document('D1','Contract','c.pdf',schema_version=6,source_key='contract',
                     pages=tuple({'pdf_page':n,'text':f'Regel {n}'} for n in range(1,5)))
        calls=[]
        class Model:
            last_raw_response=None;last_finish_reason=None
            def extract(self,prompt):
                calls.append(prompt);data=decode_prompt(prompt)
                if len(data['source_pages'])==4:
                    self.last_raw_response='{unfinished';self.last_finish_reason='MAX_TOKENS'
                    raise IncompleteResponse('unfinished_generation','MAX_TOKENS')
                result=index_answer(data);self.last_raw_response=json.dumps(result);self.last_finish_reason='STOP'
                return result
        with tempfile.TemporaryDirectory() as tmp:
            raw=Path(tmp)/'raw.json'
            result=collect_historical_facts([doc],RetryingModel(RawRunRecorder(Model(),raw)),chunk_pages=3)
            self.assertEqual(result['status'],'request_failed')
            self.assertEqual(calls[0],calls[1]);self.assertEqual(len(calls),2)
            self.assertEqual(json.loads(raw.read_text())[:2],['{unfinished','{unfinished'])
            self.assertEqual(len(result['review_findings'][0]['attempts']),2)

    def test_incomplete_index_stops_before_any_downstream_request(self):
        doc=document('contract','Eine Regel.');calls=[];saved=[]
        def extract(prompt):
            calls.append(prompt);return {'verpflichtungen':[], 'nicht_als_fakt_erfasst':[]}
        with self.assertRaisesRegex(ValueError,'incomplete'):
            extract_project_units([doc],SimpleNamespace(extract=extract),
                                  on_pass=lambda name,result:saved.append((name,result)))
        self.assertEqual(len(calls),1)
        self.assertEqual(saved[0][0],'PAGE_PROJECT_FACTS')
        self.assertEqual(saved[0][1]['status'],'incomplete')

    def test_full_collection_reaches_applicability_as_original_clauses(self):
        docs=[document('contract','Vereinbart ist Holz.'),document('amendment','Vereinbart ist Stein.'),
              document('offer','Eine Solaranlage wird angeboten.'),document('selection','Die Farbe bleibt offen.')]
        facts=collect_historical_facts(docs,SimpleNamespace(extract=lambda p:index_answer(decode_prompt(p))))
        local=document('blb','Der Kunde bestätigt die Planung.');calls=[]
        def extract(prompt):
            kind=prompt.split('.',1)[0];data=decode_prompt(prompt)
            if prompt.startswith('PAGE_CONSEQUENCES.'):
                out = assessment_answer(json.loads(prompt.split('SOURCE DATA:\n', 1)[1]))
                return out
            if kind=='PAGE_EXTRACTION':return {'working_units':[{'action':'Planung bestätigen',
                'citations':[{'pdf_page':1,'quote':local.pages[0]['text']}]}],'carry_forward':''}
            if kind=='PAGE_OFFERS':return {'offers':[]}
            if kind=='PAGE_APPLICABILITY':calls.append(data)
            return {'assessments':[{'unit_id':u['id'],'answer':'NOT_STATED','citations':[]} for u in data['working_units']]}
        result=extract_page_units([local],SimpleNamespace(extract=extract),project_facts=facts)
        received=calls[0]['project_facts']['facts']
        self.assertEqual([r['text'] for r in received],[d.pages[0]['text'] for d in docs])
        self.assertTrue(all('verpflichtung' not in r for r in received))
        self.assertEqual([(r['source_key'],r['document_id']) for r in received],[(d.source_key,d.id) for d in docs])
        self.assertEqual(result['project_facts'],facts)
