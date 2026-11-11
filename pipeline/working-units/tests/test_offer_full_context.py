"""Offers see complete project sources without extracting context as extra windows."""
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
from bauvo_working_units.shared.source_documents import full_source_documents


def document(identifier, key, pages, kind='project'):
    return Document(identifier, identifier, key+'.pdf', schema_version=6, source_key=key,
                    source_snapshot=key+'-hash', source_type=kind,
                    pages=tuple({'pdf_page':n, 'text':text, 'checkboxes':[]} for n,text in pages))


class OfferFullContextTests(unittest.TestCase):
    def baseline(self, docs):
        return extract_page_units(docs, SimpleNamespace(extract=lambda _: {
            'working_units':[], 'carry_forward':'Original extraction context'}),
            stop_after='PAGE_EXTRACTION', chunk_pages=3)

    def test_full_documents_reach_every_window_and_qualified_context_citations_are_checked(self):
        target=document('D1','specification',[(n,f'Quelltext der Leistungsbeschreibung Seite {n}.')
                                            for n in range(1,14)],'blb')
        other=document('D2','specification',[(14,'Weitere Leistungen.')],'blb')
        amendment=document('D1','amendment',[(1,'Die optionale Leistung ist bereits verbindlich beauftragt.')])
        context=[amendment];docs=[target,other]
        baseline=self.baseline(docs);original=copy.deepcopy((docs,context,baseline))
        calls=[];responses=[]
        def model(prompt):
            self.assertTrue(prompt.startswith('PAGE_OFFERS.'))
            data=json.loads(prompt.split('SOURCE DATA:\n')[1]);calls.append(data)
            self.assertEqual(data['source_documents'],full_source_documents([*docs,*context]))
            self.assertEqual(sum(len(d['pages']) for d in data['source_documents']),15)
            self.assertEqual(data['prior_context'],'Original extraction context')
            self.assertEqual(data['working_units'],[])
            page=next(p for p in data['source_pages'] if p['primary'])
            # Preserve provider answers and validate their evidence, including colliding IDs/pages.
            citation={'source_key':'amendment','document_id':'D1','pdf_page':1,
                      'quote':amendment.pages[0]['text']}
            if len(calls)==2:
                citation['source_key']='specification'
            response={'offers':[{'name':'Zusatzleistung','provider':None,'description':'Angebot',
                       'terms':[], 'citations':[{'pdf_page':page['pdf_page'],'quote':page['text']},citation]}]}
            responses.append(copy.deepcopy(response));return response
        result=extract_offers(baseline,docs,SimpleNamespace(extract=model),3,1,context)
        self.assertEqual([([p['pdf_page'] for p in c['source_pages']]) for c in calls],
                         [[1,2,3],[3,4,5],[5,6,7],[7,8,9],[9,10,11],[11,12,13],[14]])
        self.assertEqual([r['gemini'] for r in result['gemini_responses'][-7:]],responses)
        self.assertEqual([f['code'] for f in result['review_findings']],['citation_quote_not_found'])
        self.assertEqual(result['working_units'],baseline['working_units'])
        self.assertEqual(result['project_facts'],baseline['project_facts'])
        self.assertEqual(result['context_snapshots'],[{'source_key':'amendment','document_id':'D1',
                         'source_type':'project','document_sha256':digest(list(amendment.pages))}])
        self.assertEqual((docs,context,baseline),original)

    def test_conflicting_context_identity_is_rejected_before_offer_requests(self):
        target=document('D1','source',[(1,'Optionale Leistung.')])
        conflict=document('D1','source',[(1,'Anderer Inhalt.')])
        baseline=self.baseline([target])
        with self.assertRaisesRegex(ValueError,'Conflicting.*source'):
            extract_offers(baseline,[target],SimpleNamespace(extract=lambda _:self.fail('No call')),
                           3,1,[conflict])

    def test_standalone_cli_loads_context_and_explicit_source_types(self):
        target=document('D1','contract',[(1,'Vertragsleistung.')])
        context=document('D1','specification',[(1,'Optionale Leistung.')])
        baseline=self.baseline([target]);calls=[]
        def model(prompt):
            calls.append(json.loads(prompt.split('SOURCE DATA:\n')[1]));return {'offers':[]}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);saved=root/'tasks.json';saved.write_text(json.dumps(baseline))
            output=root/'offers.json'
            argv=['offers','--working-units',str(saved),'--pages','main-pages','--documents','main-docs',
                  '--context-pages','extra-pages','--context-documents','extra-docs','--context-type','blb',
                  '--project','unused','--chunk-pages','3','--output',str(output)]
            with patch('sys.argv',[*argv,'--max-new-calls','100']), patch('bauvo_working_units.phases.offers.stage.load_documents',
                    side_effect=[[target],[context]]), patch('bauvo_working_units.gemini.GeminiModel',
                    return_value=SimpleNamespace(extract=model)):
                main()
            result=json.loads(output.read_text())
            self.assertEqual(len(calls),1)
            self.assertEqual([d['source_type'] for d in calls[0]['source_documents']],['project','blb'])
            self.assertEqual(result['context_snapshots'][0]['source_type'],'blb')
            self.assertEqual(json.loads(saved.read_text()),baseline)

    def test_standalone_cli_rejects_unpaired_context_before_provider_initialization(self):
        required=['offers','--working-units','tasks','--pages','pages','--documents','docs',
                  '--project','unused','--output','out']
        for extra in (['--context-pages','extra'],['--context-type','blb']):
            with self.subTest(extra=extra), patch('sys.argv',required+extra), \
                    patch('bauvo_working_units.gemini.GeminiModel') as provider:
                with self.assertRaises(SystemExit) as error:
                    main()
                self.assertEqual(error.exception.code,2)
                provider.assert_not_called()
