"""Only the current window and compact fact evidence cross the provider boundary."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bauvo_working_units.core import digest
from bauvo_working_units.page_flow import extract_page_units
from bauvo_working_units.phases.offers.stage import extract_offers, main
from bauvo_working_units.project_flow import Document
from test_offer_boundaries import wire, offer, citation


def document(identifier,key,pages,kind='project'):
    return Document(identifier,identifier,key+'.pdf',schema_version=6,source_key=key,
                    source_snapshot=key+'-hash',source_type=kind,
                    pages=tuple({'pdf_page':n,'text':text,'checkboxes':[]} for n,text in pages))


class OfferWindowContextTests(unittest.TestCase):
    def baseline(self,docs):
        return extract_page_units(docs,SimpleNamespace(extract=lambda _:{'working_units':[]}),
                                  stop_after='PAGE_EXTRACTION',chunk_pages=3)

    def test_every_primary_page_is_sent_once_and_context_is_never_an_extraction_source(self):
        target=document('D1','blb',[(n,f'BLB Seite {n}.') for n in range(1,14)],'blb')
        other=document('D2','blb',[(14,'Weitere Leistungen.')],'blb')
        context=document('D1','contract',[(1,'Anderer Text auf gleicher Seite.')])
        baseline=self.baseline([target,other]);before=copy.deepcopy(baseline);calls=[]
        def provider(prompt):
            data=wire(prompt);calls.append(data)
            self.assertNotIn(context.pages[0]['text'],prompt)
            self.assertEqual(set(data),{'source_pages','project_facts'})
            return {'offers':[offer([citation(data['source_pages'][0])])]}
        result=extract_offers(baseline,[target,other],SimpleNamespace(extract=provider),3,1,[context])
        self.assertEqual([p['pdf_page'] for d in calls for p in d['source_pages']],list(range(1,15)))
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(result['working_units'],baseline['working_units'])
        self.assertEqual(result['project_facts'],baseline['project_facts'])
        self.assertEqual(result['context_snapshots'],[{'source_key':'contract','document_id':'D1',
                         'source_type':'project','document_sha256':digest(list(context.pages))}])
        self.assertEqual(baseline,before)

    def test_conflicting_context_identity_stops_before_requests(self):
        target=document('D1','source',[(1,'Optionale Leistung.')])
        conflict=document('D1','source',[(1,'Anderer Inhalt.')])
        with self.assertRaisesRegex(ValueError,'Conflicting.*source'):
            extract_offers(self.baseline([target]),[target],SimpleNamespace(extract=lambda _:self.fail('No call')),3,1,[conflict])

    def test_cli_contract_request_excludes_blb_context(self):
        target=document('D1','contract',[(1,'Vertragsleistung.')])
        context=document('D1','specification',[(1,'Optionale Leistung.')],'blb')
        baseline=self.baseline([target]);calls=[]
        def provider(prompt):calls.append(wire(prompt));return {'offers':[]}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);saved=root/'tasks.json';saved.write_text(json.dumps(baseline))
            output=root/'offers.json'
            argv=['offers','--working-units',str(saved),'--pages','main','--documents','main-docs',
                  '--context-pages','extra','--context-documents','extra-docs','--context-type','blb',
                  '--project','unused','--chunk-pages','3','--output',str(output),'--max-new-calls','1']
            with patch('sys.argv',argv),patch('bauvo_working_units.phases.offers.stage.load_documents',side_effect=[[target],[context]]),\
                 patch('bauvo_working_units.gemini.GeminiModel',return_value=SimpleNamespace(extract=provider)):
                main()
            self.assertEqual(len(calls),1)
            self.assertEqual(calls[0]['source_pages'],[{'pdf_page':1,'passages':[{'passage':'P1-1','text':'Vertragsleistung.'}]}])
            self.assertNotIn('Optionale Leistung.',json.dumps(calls))
            self.assertEqual(json.loads(saved.read_text()),baseline)

    def test_cli_rejects_unpaired_context_before_provider_initialization(self):
        required=['offers','--working-units','tasks','--pages','pages','--documents','docs',
                  '--project','unused','--output','out']
        for extra in (['--context-pages','extra'],['--context-type','blb']):
            with self.subTest(extra=extra),patch('sys.argv',required+extra),patch('bauvo_working_units.gemini.GeminiModel') as provider:
                with self.assertRaises(SystemExit) as error:main()
                self.assertEqual(error.exception.code,2);provider.assert_not_called()

    def test_cli_blb_gets_interpretations_and_supporting_quotes_without_full_contract(self):
        from bauvo_working_units.project_facts import collect_project_facts
        target=document('D1','blb',[(1,'Eine Zusatzleistung ist gesondert beauftragbar.')],'blb')
        contract=document('D1','contract',[(1,'Vertraglich vereinbarte Leistung.\nNicht zitierter Vertragstext.')])
        baseline=self.baseline([target])
        baseline['project_facts']=collect_project_facts([contract],SimpleNamespace(extract=lambda _: {
            'ansprueche':[{'aussage':'Diese Leistung ist vereinbart.','belege':['S1']}]}))
        captured=[]
        def provider(prompt):
            captured.append(prompt);data=wire(prompt)
            self.assertNotIn('Nicht zitierter Vertragstext.',prompt)
            self.assertNotIn('source_documents',data)
            self.assertEqual(data['project_facts']['facts'][0]['interpretation'],'Diese Leistung ist vereinbart.')
            self.assertEqual(data['project_facts']['facts'][0]['evidence'][0]['quote'],'Vertraglich vereinbarte Leistung.\n')
            return {'offers':[]}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);saved=root/'tasks.json';saved.write_text(json.dumps(baseline));before=saved.read_bytes()
            output=root/'offers.json'
            argv=['offers','--working-units',str(saved),'--pages','blb','--documents','blb-docs',
                  '--project','unused','--chunk-pages','3','--output',str(output),'--max-new-calls','1']
            with patch('sys.argv',argv),patch('bauvo_working_units.phases.offers.stage.load_documents',return_value=[target]),\
                 patch('bauvo_working_units.gemini.GeminiModel',return_value=SimpleNamespace(extract=provider)):
                main()
            result=json.loads(output.read_text())
            self.assertEqual(len(captured),1)
            self.assertEqual(result['project_facts'],baseline['project_facts'])
            self.assertEqual(result['working_units'],baseline['working_units'])
            self.assertEqual(saved.read_bytes(),before)
