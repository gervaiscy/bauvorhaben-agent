from historical_facts import collect_historical_facts
from clause_fixtures import labeled_answer
"""Selectable lines, exact text assembly, and versioned historical handoffs."""
import copy
import json
from types import SimpleNamespace
import unittest

from bauvo_working_units.citation_evidence import catalog, canonical, CATALOG_PROTOCOL, LEGACY_CATALOG, resolve
from bauvo_working_units.fact_artifact import validate_facts
from bauvo_working_units.page_flow import _prompt, extract_page_units
from bauvo_working_units.phases.project_facts.index import source_text, PASSAGE_SEPARATOR
from bauvo_working_units.project_facts import collect_project_facts, facts_view
from clause_fixtures import answer_for, entries_for, historical_index, interpreted
from test_project_facts import document


def qualified(text, number=1):
    return {'source_key':'contract','document_id':'D1','pdf_page':number,'text':text}


class SourceLineTests(unittest.TestCase):
    def test_lines_keep_abbreviations_and_table_positions_separately_selectable(self):
        text=('1 Eine 2-flg. Tür\t500,00\n'
              'Ausführung z. B. aus Holz.\n'
              '2 Ein zusätzlicher Raum\t900,00\n'
              'Bis 15 m² inkl. Heizung.\n'
              'Bankverbindung: Beispielbank\n'
              'Erklärungen bedürfen einer schriftlichen Bestätigung.')
        entries=catalog([qualified(text)])
        self.assertEqual([e['quote'] for e in entries],text.splitlines(keepends=True))
        self.assertEqual(len(entries),6)
        self.assertTrue(all(e['catalog_protocol']==CATALOG_PROTOCOL for e in entries))
        self.assertNotEqual(entries[0]['evidence_id'],entries[2]['evidence_id'])
        self.assertNotEqual(entries[4]['evidence_id'],entries[5]['evidence_id'])

    def test_whitespace_unicode_tabs_crlf_and_long_lines_are_preserved_at_exact_offsets(self):
        text='Überschrift 🏠\r\n\r\n  Tür\t1,00\r\n'+'Langes Wort '*400+'\n\nEnde.'
        page=qualified(text);entries=catalog([page]);covered=set()
        for e in entries:
            self.assertEqual(e['quote'],text[e['start']:e['end']])
            self.assertLessEqual(len(e['quote']),1200)
            self.assertFalse(covered.intersection(range(e['start'],e['end'])))
            covered.update(range(e['start'],e['end']))
        self.assertTrue(all(i in covered for i,char in enumerate(text) if not char.isspace()))
        self.assertEqual(source_text([canonical(e) for e in entries],[page]),text)

    def test_whitespace_gaps_join_but_omitted_source_text_and_page_boundaries_stay_visible(self):
        page=qualified('Eine Regel.\n\nEine weitere Regel.\nEine Ausnahme.')
        entries=[canonical(e) for e in catalog([page])]
        self.assertEqual(source_text(entries,[page]),page['text'])
        self.assertEqual(source_text([entries[0],entries[2]],[page]),
                         entries[0]['quote']+PASSAGE_SEPARATOR+entries[2]['quote'])
        other=qualified('Nächste Seite.',number=2)
        other_entry=canonical(catalog([other])[0])
        self.assertIn(PASSAGE_SEPARATOR,source_text([entries[-1],other_entry],[page,other]))
        changed={**page,'text':page['text'].replace('weitere','andere')}
        with self.assertRaisesRegex(ValueError,'source page changed'):
            source_text(entries,[changed])

    def test_repeated_text_has_distinct_ids_and_unknown_catalog_versions_fail(self):
        page=qualified('Wiederholung\nWiederholung\n')
        entries=catalog([page])
        self.assertEqual(len(entries),2)
        self.assertNotEqual(entries[0]['evidence_id'],entries[1]['evidence_id'])
        self.assertEqual(catalog([page,page]),entries)
        with self.assertRaisesRegex(ValueError,'Unknown evidence catalog'):
            catalog([page],protocol='unknown')

    def test_historical_catalog_retains_its_original_punctuation_boundaries(self):
        page=qualified('Ein Satz. Noch ein Satz.\n')
        legacy=catalog([page],protocol=LEGACY_CATALOG)
        self.assertEqual([e['quote'] for e in legacy],['Ein Satz. ','Noch ein Satz.\n'])
        self.assertEqual(len(catalog([page])),1)
        self.assertTrue(all('catalog_protocol' not in canonical(e) for e in legacy))
        response={'working_units':[{'action':'Prüfen','citations':[{'evidence_id':legacy[0]['evidence_id']}]}]}
        resolved,audit,issues=resolve(response,[page],[],'contract','D1',catalog_protocol=LEGACY_CATALOG)
        self.assertEqual(issues,[])
        self.assertEqual(audit['catalog_protocol'],LEGACY_CATALOG)
        self.assertEqual(resolved['working_units'][0]['citations'],[canonical(legacy[0])])


class VersionedClauseIndexTests(unittest.TestCase):
    def test_shared_boundary_line_preserves_complete_rules_through_saved_and_downstream_handoffs(self):
        doc=document('contract','Baustrom ist nicht enthalten. Ändert sich die gesetzliche\n'
                     'Umsatzsteuer, wird der Preis angepasst. Dies gilt nicht,\n'
                     'wenn die Anpassung gesetzlich ausgeschlossen ist.')
        entries=entries_for(doc)
        refs=[{'evidence_id':e['evidence_id']} for e in entries]
        answer={'verpflichtungen':[interpreted([refs[0]]),interpreted(refs)],
                'nicht_als_fakt_erfasst':[]}
        calls=[]
        def extract(prompt):
            calls.append(prompt)
            return labeled_answer([doc], answer)
        facts=collect_historical_facts([doc],SimpleNamespace(extract=extract))
        expected=[entries[0]['quote'],doc.pages[0]['text']]
        self.assertEqual(len(calls),1)
        self.assertEqual(facts['status'],'recorded')
        self.assertEqual(facts['gemini_responses'][0]['gemini'],labeled_answer([doc], answer))
        self.assertEqual(len(facts['gemini_responses'][0]['coverage']['indexed_ids']),3)
        self.assertEqual([f['derived']['text'] for f in facts['facts']],expected)
        self.assertEqual(validate_facts(facts,[doc]),facts)
        for phase in ('PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'):
            data=json.loads(_prompt(phase,{'source_pages':[], 'project_facts':facts_view(facts)},'de')
                            .split('SOURCE DATA:\n')[1])
            if phase == 'PAGE_EXTRACTION':
                self.assertEqual([[c['quote'] for c in f['citations']] for f in data['project_facts']['facts']],
                                 [[c['quote'] for c in f['citations']] for f in facts_view(facts)['facts']])
            else:
                self.assertEqual([f['text'] for f in data['project_facts']['facts']],expected)

    def test_original_continuous_text_reaches_every_downstream_projection(self):
        doc=document('contract','Die Leistung gilt\nnur nach Freigabe.\n\nDie Ausnahme bleibt erhalten.')
        facts=collect_historical_facts([doc],SimpleNamespace(extract=lambda p:answer_for(doc)))
        self.assertEqual(facts['schema_version'],13)
        self.assertEqual(facts['catalog_protocol'],CATALOG_PROTOCOL)
        self.assertEqual(facts['facts'][0]['derived']['text'],doc.pages[0]['text'])
        before=copy.deepcopy(facts)
        self.assertEqual(validate_facts(facts,[doc]),facts)
        for phase in ('PAGE_EXTRACTION','PAGE_OFFERS','PAGE_TIMING','PAGE_CONSEQUENCES','PAGE_APPLICABILITY'):
            data={'source_pages':[], 'project_facts':facts_view(facts)}
            actual=json.loads(_prompt(phase,data,'de').split('SOURCE DATA:\n')[1])
            if phase == 'PAGE_EXTRACTION':
                self.assertEqual([c['quote'] for c in actual['project_facts']['facts'][0]['citations']],
                                 [c['quote'] for c in facts_view(facts)['facts'][0]['citations']])
            else:
                self.assertEqual(actual['project_facts']['facts'][0]['text'],doc.pages[0]['text'])
        self.assertEqual(facts,before)

    def test_schema_and_catalog_metadata_cannot_be_changed_independently(self):
        doc=document('contract','Eine Regel.\nEine Ausnahme.')
        facts=collect_historical_facts([doc],SimpleNamespace(extract=lambda p:answer_for(doc)))
        for field,value in [('catalog_protocol',LEGACY_CATALOG),('evidence_protocol','source-clause-index-v1'),
                            ('schema_version',3)]:
            bad=copy.deepcopy(facts);bad[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):validate_facts(bad,[doc])
        for field,value in [('start',999),('end',999),('quote','Invented'),('page_sha256','changed')]:
            bad=copy.deepcopy(facts);bad['facts'][0]['derived']['verified_citations'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):validate_facts(bad,[doc])

    def test_historical_collections_validate_without_becoming_new_analysis_inputs(self):
        doc=document('contract','Eine Regel. Eine Ausnahme.\n')
        for schema in (2,3,4,5):
            old=historical_index(doc,schema=schema);before=copy.deepcopy(old)
            self.assertEqual(validate_facts(old,[doc]),old)
            self.assertEqual(old,before)
            with self.subTest(schema=schema),self.assertRaisesRegex(ValueError,'historical collection format'):
                extract_page_units([doc],SimpleNamespace(extract=lambda p:self.fail('No paid call')),
                                   project_facts=old,stop_after='PAGE_EXTRACTION')
