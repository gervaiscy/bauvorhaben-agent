"""Restore task evidence locally; provider titles never become task descriptions."""
import copy

from ..project_facts.index import source_text
from . import quotations


def resolve_response(response, payload):
    """Current literal-quote responses and historical ID responses stay distinct."""
    from ...citation_checks import citation_findings
    from ...citation_evidence import resolve
    from .payload import legacy_input
    source_key = payload['source_key']
    document_id = payload['document']['id']
    pages = [{**p, 'source_key': source_key, 'document_id': document_id}
             for p in payload['source_pages']]
    rows = response.get('working_units') if isinstance(response, dict) else None
    if isinstance(rows, list) and ((not rows and 'carry_forward' not in response)
                                   or any(quotations.is_quotation(row) for row in rows)):
        resolved, audit, issues = quotations.resolve_quotations(response, pages)
        issues.extend(citation_findings(resolved, payload['source_pages'],
                                        source_key=source_key, document_id=document_id))
        audit['findings'] = copy.deepcopy(issues)
        return resolved, audit, issues, None
    _, mapping = legacy_input(payload)
    resolved, audit, issues = resolve(for_resolution(response), payload['source_pages'], [],
                                     source_key, document_id, reference_map=mapping)
    return resolved, audit, issues, mapping


def is_selection(item):
    return (isinstance(item, dict) and not quotations.is_quotation(item)
            and ('title' in item or 'evidence_ids' in item))


def valid_selection(item):
    return (is_selection(item) and set(item) == {'title', 'actor', 'evidence_ids'}
            and isinstance(item['title'], str) and bool(item['title'].strip())
            and (item['actor'] is None or isinstance(item['actor'], str) and bool(item['actor'].strip()))
            and isinstance(item['evidence_ids'], list) and bool(item['evidence_ids'])
            and all(isinstance(label, str) and label.strip() for label in item['evidence_ids']))


def for_resolution(response):
    """Adapt a copy to the shared citation resolver; keep raw JSON untouched.

    Historical action/citations responses retain their original validation path.
    """
    result = copy.deepcopy(response)
    rows = result.get('working_units') if isinstance(result, dict) else None
    if isinstance(rows, list):
        for item in rows:
            if is_selection(item):
                labels = item.get('evidence_ids')
                item['citations'] = ([{'evidence_id': label} for label in labels]
                                     if isinstance(labels, list) else None)
    return result


def derived_data(item, resolved, document, chunk):
    if quotations.is_quotation(item):
        return quotations.derived_data(item, resolved)
    if not is_selection(item):
        return {}
    result = {'task_protocol': 'selected-source-passages-v2', 'title': item.get('title')}
    citations = resolved.get('citations') if isinstance(resolved, dict) else None
    # Never manufacture a complete task description from a partially bound row.
    if not valid_selection(item) or not isinstance(citations, list) or not citations or any(
            not isinstance(c, dict) or not isinstance(c.get('quote'), str)
            or not c['quote'].strip() or 'start' not in c for c in citations):
        return result
    # Repetition is harmless within one task. Shared evidence across tasks stays.
    selected = list({c['evidence_id']: copy.deepcopy(c) for c in citations}.values())
    selected.sort(key=lambda c: (c['pdf_page'], c['start']))
    pages = [{**p, 'source_key': document.source_key or document.source or document.id,
              'document_id': document.id} for p in document.pages if p['pdf_page'] in chunk.pages]
    result.update(actor=item['actor'], citations=selected, source_text=source_text(selected, pages))
    return result
