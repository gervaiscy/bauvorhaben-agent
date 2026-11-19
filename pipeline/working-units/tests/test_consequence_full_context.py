"""Sources are complete, deduplicated and projected without application metadata."""
import copy
import unittest
from consequence_fixtures import document
from bauvo_working_units.phases.consequences.payload import model_payload


class ConsequenceFullContextTests(unittest.TestCase):
    def test_original_whitespace_and_last_page_survive_without_duplicate_contracts(self):
        docs=[document('D1','blb',[(1,'Unverändert.\n\n  Text.'),(68,'Letzte Seite.')],'blb')]
        data={'documents':docs+copy.deepcopy(docs),'project_facts':{'facts':[{'id':'F1','text':'Vertragstext','interpretation':'Proposal','citations':['metadata']}]},
              'working_units':{'U1':{'title':'X','actor':None,'quote':'Unverändert.','timing':{'answer':'NOT_STATED','citations':[]}}}}
        before=copy.deepcopy(data);payload=model_payload(data)
        self.assertEqual(payload['source_documents'],[{'document':'B1','title':'D1','pages':[{'pdf_page':1,'text':'Unverändert.\n\n  Text.'},{'pdf_page':68,'text':'Letzte Seite.'}]}])
        self.assertEqual(payload['project_facts'],[{'id':'F1','text':'Vertragstext'}])
        self.assertEqual(data,before)
        payload['working_units']['U1']['timing']['answer']='Changed'
        self.assertEqual(data,before)

    def test_same_identity_with_conflicting_source_text_is_rejected(self):
        docs=[document('D1','blb',[(1,'Original')],'blb'),document('D1','blb',[(1,'Changed')],'blb')]
        with self.assertRaisesRegex(ValueError,'Conflicting source documents'):
            model_payload({'documents':docs,'working_units':{}})
