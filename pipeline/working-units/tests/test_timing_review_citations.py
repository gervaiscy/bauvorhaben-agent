import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bauvo_working_units.citation_evidence import evidence_span
from bauvo_working_units.phases.timing.review_citations import compact_citations
from bauvo_working_units.phases.timing.artifact import load_artifact, review_view
import test_timing_artifact as fixtures


class TimingReviewCitationTests(unittest.TestCase):
    def setUp(self):
        self.page = {'source_key': 'contract', 'document_id': 'D1', 'pdf_page': 1,
                     'text': 'Erste Regel.\n\nEine Ausnahme.\nNicht ausgewählt.\nWeitere Regel.'}
        self.pages = {('contract', 'D1', 1): self.page['text']}

    def span(self, text):
        start = self.page['text'].index(text)
        return evidence_span(self.page, start, start + len(text))

    def test_adjacent_and_whitespace_separated_evidence_preserves_exact_original_text(self):
        refs = [self.span('Erste Regel.'), self.span('Eine Ausnahme.')]
        before = copy.deepcopy(refs)
        compact = compact_citations(refs, self.pages)
        self.assertEqual(len(compact), 1)
        self.assertEqual(compact[0]['quote'], 'Erste Regel.\n\nEine Ausnahme.')
        self.assertEqual(refs, before)
        compact = compact_citations([self.span('Erste '), self.span('Regel.')], {})
        self.assertEqual(compact[0]['quote'], 'Erste Regel.')

    def test_repeated_source_spans_compact_but_separate_occurrences_do_not(self):
        first, second = self.span('Erste Regel.'), self.span('Eine Ausnahme.')
        compact = compact_citations([first, first, second, second], self.pages)
        self.assertEqual(len(compact), 1)
        elsewhere = {**first, 'start': 500, 'end': 512}
        self.assertEqual(len(compact_citations([first, elsewhere], {})), 2)

    def test_omitted_words_different_sources_pages_and_hashes_are_never_glued(self):
        first, last = self.span('Erste Regel.'), self.span('Weitere Regel.')
        self.assertEqual(len(compact_citations([first, last], self.pages)), 2)
        next_span = self.span('Eine Ausnahme.')
        for changes in ({'source_key': 'other'}, {'document_id': 'D2'}, {'pdf_page': 2},
                        {'page_sha256': 'other'}):
            with self.subTest(changes=changes):
                self.assertEqual(len(compact_citations([first, {**next_span, **changes}], self.pages)), 2)

    def test_unbound_quotes_and_unknown_references_remain_separate_and_unmodified(self):
        refs = [{'fact_id': 'UNKNOWN'}, {'pdf_page': 1, 'quote': 'Original?'}]
        self.assertEqual(compact_citations(refs, self.pages), refs)
        self.assertEqual(len(compact_citations([refs[0], refs[0]], self.pages)), 2)
        # A wrong supplied page cannot justify silently joining a gap.
        self.assertEqual(len(compact_citations([self.span('Erste Regel.'), self.span('Eine Ausnahme.')],
                                              {('contract','D1',1): 'Different source'})), 2)

    def test_legacy_readable_file_still_loads_and_audit_retains_all_selections(self):
        fixture = fixtures.TimingArtifactTests(); fixture.setUp()
        result = fixture.result
        result['working_units'][0]['reviews'][0]['derived']['resolved_evidence']['citations'] = [
            self.span('Erste '), self.span('Regel.'), self.span('Regel.')]
        before = copy.deepcopy(result)
        current, legacy = review_view(result), review_view(result, compact=False)
        self.assertEqual(len(current['working_units'][0]['timing']['citations']), 1)
        self.assertEqual(len(legacy['working_units'][0]['timing']['citations']), 3)
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'result.json'
            path.with_name('result.audit.json').write_text(json.dumps(result))
            for view in (current, legacy):
                path.write_text(json.dumps(view))
                self.assertEqual(load_artifact(path), result)
        self.assertEqual(result, before)
