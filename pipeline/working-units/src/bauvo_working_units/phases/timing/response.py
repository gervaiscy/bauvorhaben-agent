"""Bind timing quotations and fact references offline; raw provider JSON is untouched."""
import copy

from ...citation_evidence import catalog, canonical
from ...citation_checks import text_findings
from ...core import digest
from .quote_binding import bind_quote, POLICY
from .payload import PROTOCOL


def resolve_response(response, pages, facts, supporting_pages=()):
    result = copy.deepcopy(response)
    issues, bindings = text_findings(response), []
    lookup = {f['id']: f for f in facts.get('facts', [])}
    entries = {e['evidence_id']: canonical(e) for e in catalog([*pages, *supporting_pages])}
    rows = result.get('assessments') if isinstance(result, dict) else None
    if isinstance(rows, list):
        for n, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get('citations'), list):
                continue
            selected = []
            for k, citation in enumerate(row['citations']):
                path = f'$.assessments[{n}].citations[{k}]'
                refs, issue, reason = [], None, None
                if isinstance(citation, dict) and set(citation) == {'fact_id'}:
                    identifier = citation['fact_id']
                    fact = lookup.get(identifier) if isinstance(identifier, str) else None
                    if fact is None:
                        issue = 'unknown_fact_id'
                    else:
                        refs = fact.get('citations', [])
                        if not isinstance(refs, list) or not refs or any(not isinstance(c, dict) or
                                not isinstance(c.get('evidence_id'), str) or c['evidence_id'] not in entries or
                                any(c.get(key) != entries[c['evidence_id']].get(key) for key in
                                    ('source_key', 'document_id', 'pdf_page', 'quote', 'start', 'end', 'page_sha256'))
                                for c in refs):
                            refs, issue = [], 'invalid_fact_evidence'
                        else:
                            refs = [entries[c['evidence_id']] for c in refs]
                    method = {'method': 'project_fact_reference', 'fact_id': identifier}
                else:
                    span, issue, reason, details = bind_quote(citation, pages)
                    refs = [span] if span else []
                    method = {'method': 'source_page_quote', **details}
                if issue:
                    issues.append({'path': path, 'code': issue, **({'reason': reason} if reason else {})})
                    selected.append(copy.deepcopy(citation))
                else:
                    for span in refs:
                        bindings.append({'path': path, **method, **span})
                        selected.append(copy.deepcopy(span))
            row['citations'] = selected
    audit = {'timing_protocol': PROTOCOL, 'quote_binding_policy': POLICY, 'source_pages_sha256': digest(pages),
             'bindings': bindings, 'original_findings': copy.deepcopy(issues),
             'findings': copy.deepcopy(issues)}
    return result, audit, issues
