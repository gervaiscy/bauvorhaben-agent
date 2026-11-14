"""Window text and accepted fact evidence with source labels and quotations."""
import copy

from ..project_facts.payload import compact_pages
from .passages import passage_catalog, REFERENCE_PROTOCOL


def legacy_input(payload, _labelled=True):
    """Return the model data and an application-only label-to-source binding."""
    source_key = payload.get('source_key', 'source')
    document = payload.get('document', {})
    pages = [{**page, 'source_key': source_key, 'document_id': document.get('id', 'D1')}
             for page in payload.get('source_pages', [])]
    entries = passage_catalog(pages) if _labelled else []
    mapping = {f'S{n}': entry['line_evidence_ids'] for n, entry in enumerate(entries, 1)}
    available = {entry['evidence_id']: entry for entry in payload.get('evidence_catalog', [])}
    view = payload.get('project_facts', {})
    def citations_for(citations):
        result = []
        for citation in citations:
            # Fact evidence is context, not another segment in this window.
            # Its persistent IDs remain in the application's original fact data.
            entry = available.get(citation.get('evidence_id'), citation)
            result.append({
                **{k: copy.deepcopy(entry[k]) for k in ('source_key', 'document_id', 'pdf_page') if k in entry},
                **({'quote': entry['quote']} if 'quote' in entry else {})})
        return result

    facts = []
    for fact in view.get('facts', []):
        row = {'id': fact.get('id'), 'citations': citations_for(fact.get('citations', [])),
               'review_findings': copy.deepcopy(fact.get('review_findings', []))}
        if 'interpretation' in fact:
            row['interpretation'] = fact['interpretation']
        elif 'text' in fact:
            row['text'] = fact['text']
        if 'uncertainty' in fact:
            row['uncertainty'] = fact['uncertainty']
        for field in ('source_key', 'document_id', 'document_title', 'sources',
                      'provision_id', 'status', 'related_provision_ids', 'related_fact_ids'):
            if field in fact:
                row[field] = copy.deepcopy(fact[field])
        if 'context_citations' in fact:
            row['context_citations'] = citations_for(fact['context_citations'])
        if 'claims' in fact:
            row['claims'] = [{**{k: copy.deepcopy(v) for k, v in claim.items() if k != 'verified_citations'},
                              'verified_citations': citations_for(claim.get('verified_citations', []))}
                             for claim in fact['claims']]
        facts.append(row)
    data = {k: copy.deepcopy(payload[k]) for k in ('source_key', 'document', 'known_items', 'prior_context')
            if k in payload}
    data['source_pages'] = [
        {**page, **{k: copy.deepcopy(original[k]) for k in ('primary', 'checkboxes') if k in original}}
        for page, original in zip(compact_pages(pages, entries, short_labels=True), pages)] if _labelled else [
        {k: copy.deepcopy(page[k]) for k in ('pdf_page', 'text', 'primary', 'checkboxes') if k in page}
        for page in pages]
    data['project_facts'] = {'facts': facts, **{k: copy.deepcopy(view[k])
        for k in ('extraction_scope', 'processing', 'review_findings') if k in view}}
    if _labelled:
        data['reference_protocol'] = REFERENCE_PROTOCOL
    return data, mapping


def model_input(payload):
    """Plain original window; source IDs are owned by the application, not Gemini."""
    data, _ = legacy_input(payload, _labelled=False)
    data.pop('prior_context', None)
    return data, None
