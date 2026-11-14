from historical_facts import collect_historical_facts
from clause_fixtures import decode_prompt
"""Interpretation transport, literal evidence, full context and failure boundaries."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from bauvo_working_units.cache import CachedModel
from bauvo_working_units.fact_artifact import validate_facts
from bauvo_working_units.page_flow import _prompt, extract_page_units
from bauvo_working_units.project_facts import collect_project_facts, facts_view
from bauvo_working_units.trace import RawRunRecorder
from clause_fixtures import answer_for, entries_for, interpreted
from test_project_facts import document


class SourceBackedInterpretationTests(unittest.TestCase):
    def test_interpretation_and_all_quotes_remain_separate_without_rewriting_either(self):
        doc=document('contract', 'Der Bauherr tritt die Finanzierungsansprüche ab.\n'
                     'Abtretung und Bankbestätigung sind acht Wochen vor Baubeginn vorzulegen.\n'
                     'Alternativ darf er vor Baubeginn eine Zahlungsbürgschaft stellen.')
        interpretation=('Der Bauherr kann zwischen Abtretung und Zahlungsbürgschaft wählen. '
                        'Bei Abtretung sind diese und die Bankbestätigung acht Wochen vor '
                        'Baubeginn vorzulegen; die alternative Bürgschaft ist vor Baubeginn zu stellen.')
        row=interpreted([{'evidence_id':e['evidence_id']} for e in entries_for(doc)],interpretation)
        answer=answer_for(doc,[row]);calls=[]
        def provider(prompt):
            calls.append(prompt)
            return copy.deepcopy(answer)
        result=collect_historical_facts([doc],SimpleNamespace(extract=provider))
        self.assertEqual(len(calls),1)
        self.assertEqual(result['schema_version'],13)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(result['gemini_responses'][0]['gemini'],answer)
        self.assertEqual(result['facts'][0]['gemini'],answer['verpflichtungen'][0])
        self.assertEqual(result['facts'][0]['derived']['text'],doc.pages[0]['text'])
        self.assertEqual([c['quote'] for c in result['facts'][0]['derived']['verified_citations']],
                         doc.pages[0]['text'].splitlines(keepends=True))
        view=facts_view(result)['facts'][0]
        self.assertEqual(view['interpretation'],interpretation)
        self.assertIsNone(view['uncertainty'])
        self.assertEqual(validate_facts(result,[doc]),result)

    def test_explicit_uncertainty_and_ambiguous_source_survive_projection(self):
        doc=document('sketch','Diele\n13,20 m²\n8,10 m²')
        interpretation='Die Skizze nennt eine Diele und zwei Flächen; deren Zuordnung bleibt offen.'
        uncertainty='Aus dem Text geht nicht hervor, welche Fläche zur Diele gehört.'
        answer=answer_for(doc,[interpreted([{'evidence_id':e['evidence_id']} for e in entries_for(doc)],
                                          interpretation,uncertainty)])
        facts=collect_historical_facts([doc],SimpleNamespace(extract=lambda _:copy.deepcopy(answer)))
        before=copy.deepcopy(facts)
        for phase in ('PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'):
            data=json.loads(_prompt(phase,{'source_pages':[], 'project_facts':facts_view(facts)},'de')
                            .split('SOURCE DATA:\n')[1])
            fact=data['project_facts']['facts'][0]
            self.assertEqual(fact['interpretation'],interpretation)
            self.assertEqual(fact['uncertainty'],uncertainty)
            if phase == 'PAGE_EXTRACTION':
                self.assertEqual([c['quote'] for c in fact['citations']], doc.pages[0]['text'].splitlines(keepends=True))
            else:
                self.assertEqual(fact['text'],doc.pages[0]['text'])
        self.assertEqual(facts,before)

    def test_every_downstream_pass_can_read_and_cite_unselected_original_context(self):
        contract=document('contract','Der Kunde bestätigt den Plan.\n\n'
                          'Weitere Angaben bleiben im Original erhalten.\n'
                          'Ein abweichender Nachweis ist ebenfalls zulässig.')
        entries=entries_for(contract)
        answer=answer_for(contract,[interpreted([{'evidence_id':entries[0]['evidence_id']}],
                                               'Der Kunde bestätigt den Plan.')])
        facts=collect_historical_facts([contract],SimpleNamespace(extract=lambda _:copy.deepcopy(answer)))
        local=document('blb','Plan bestätigen.')
        before=copy.deepcopy(facts);calls=[]
        def provider(prompt):
            phase=prompt.split('.',1)[0]
            self.assertNotEqual(phase,'PAGE_CITATION_REPAIR')
            data=decode_prompt(prompt);calls.append(phase)
            self.assertEqual(data['project_facts']['facts'][0]['interpretation'],'Der Kunde bestätigt den Plan.')
            if phase=='PAGE_EXTRACTION':
                self.assertNotIn('source_documents', data['project_facts'])
                self.assertNotIn('Ein abweichender Nachweis', prompt)
                ref={'evidence_id':data['source_pages'] and data['evidence_catalog'][0]['evidence_id']}
                return {'working_units':[{'action':'Plan bestätigen','citations':[ref]}],'carry_forward':''}
            source=data['project_facts']['source_documents'][0]
            self.assertEqual(source['source_key'],'contract')
            self.assertEqual(source['pages'],[{'pdf_page':1,'text':contract.pages[0]['text']}])
            ids={e['evidence_id'] for e in data['evidence_catalog']}
            self.assertTrue({e['evidence_id'] for e in entries} <= ids)
            if phase=='PAGE_OFFERS':return {'offers':[]}
            if phase=='PAGE_CONSEQUENCES':
                return {'assessments':[{'unit_id':u['id'],'status':'NOT_STATED','consequences':[]}
                                       for u in data['working_units']]}
            return {'assessments':[{'unit_id':u['id'],'answer':'Ein anderer Nachweis ist ebenfalls zulässig.',
                    'citations':[{'evidence_id':entries[-1]['evidence_id']}]} for u in data['working_units']]}
        result=extract_page_units([local],SimpleNamespace(extract=provider),project_facts=facts)
        self.assertEqual(calls,['PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'])
        self.assertEqual(result['review_findings'],[])
        self.assertEqual(facts,before)

    def test_saved_interpretation_uncertainty_and_full_source_cannot_be_changed_independently(self):
        doc=document('contract','Ein Nachweis genügt.')
        facts=collect_historical_facts([doc],SimpleNamespace(extract=lambda _:answer_for(doc)))
        for field in ('interpretation','uncertainty','source','quotation'):
            changed=copy.deepcopy(facts)
            if field in ('interpretation','uncertainty'):
                changed['facts'][0]['gemini'][field]='Changed interpretation'
            elif field=='source':changed['source_documents'][0]['pages'][0]['text']='Changed original'
            else:changed['facts'][0]['derived']['verified_citations'][0]['quote']='Changed quotation'
            with self.subTest(field=field),self.assertRaises(ValueError):
                validate_facts(changed,[doc])

    def test_missing_or_invalid_interpretation_and_uncertainty_are_preserved_as_incomplete(self):
        doc=document('contract','Eine Regel.');valid=answer_for(doc)
        for field,value in [('uncertainty',[])]:
            answer=copy.deepcopy(valid);answer['verpflichtungen'][0][field]=value
            calls=[]
            def provider(prompt):calls.append(prompt);return copy.deepcopy(answer)
            result=collect_historical_facts([doc],SimpleNamespace(extract=provider))
            with self.subTest(field=field,value=value):
                self.assertEqual(len(calls),1)
                self.assertEqual(result['status'],'incomplete')
                self.assertEqual(result['gemini_responses'][0]['gemini'],answer)
                self.assertEqual(result['facts'][0]['derived']['text'],doc.pages[0]['text'])

    def test_clause_only_response_is_saved_but_remains_incomplete_on_replay(self):
        doc=document('contract','Eine Regel.');valid=answer_for(doc)
        old={'verpflichtungen':[{'zitate':valid['verpflichtungen'][0]['zitate']}],
             'nicht_als_fakt_erfasst':[]}
        calls=[]
        def provider(prompt):calls.append(prompt);return copy.deepcopy(old if len(calls)==1 else valid)
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            model=RawRunRecorder(CachedModel(SimpleNamespace(extract=provider),root/'cache',{'model':'fixture'}),
                                 root/'raw.json')
            first=collect_historical_facts([doc],model)
            self.assertEqual(first['status'],'incomplete')
            self.assertEqual(len(list((root/'cache').glob('*.json'))),1)
            self.assertEqual(collect_historical_facts([doc],model)['status'],'incomplete')
            self.assertEqual(collect_historical_facts([doc],model)['status'],'incomplete')
            self.assertEqual(len(calls),1)
            self.assertEqual(json.loads((root/'raw.json').read_text()),[old,old,old])
