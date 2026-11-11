"""Keep interpretations separate from exact evidence; account for every passage."""
import json

from ...core import digest
from ...citation_evidence import canonical, CATALOG_PROTOCOL


PASSAGE_SEPARATOR = '\n[separate source passage]\n'


def source_text(citations, pages=()):
    """Join adjacent selections using source whitespace; keep genuine omissions visible."""
    originals = {(p['source_key'], p['document_id'], p['pdf_page']): p for p in pages}
    result, previous = '', None
    for citation in citations:
        separator = PASSAGE_SEPARATOR if previous is not None else ''
        key = tuple(citation[k] for k in ('source_key', 'document_id', 'pdf_page'))
        if (previous is not None and citation.get('catalog_protocol') == CATALOG_PROTOCOL
                and previous.get('catalog_protocol') == CATALOG_PROTOCOL
                and key == tuple(previous[k] for k in ('source_key', 'document_id', 'pdf_page'))
                and previous['page_sha256'] == citation['page_sha256']
                and previous['end'] <= citation['start']):
            if previous['end'] == citation['start']:
                separator = ''
            elif key in originals:
                original = originals[key]
                if digest(original['text']) != citation['page_sha256']:
                    raise ValueError('Clause assembly source page changed')
                gap = original['text'][previous['end']:citation['start']]
                if not gap.strip():
                    separator = gap
        result += separator + citation['quote']
        previous = citation
    return result


def index_response(response, entries, pages=(), *, interpretations=True, target_evidence_ids=None,
                   require_claims=False, allow_context_citations=False, derive_fact_citations=False, authoritative_claims=False,
                   reference_map=None, provisions=None, target_provision_ids=None):
    """Keep every provider row; bind only exact IDs from this request's catalog."""
    lookup = {e['evidence_id']: e for e in entries}
    targets = set(lookup if target_evidence_ids is None else target_evidence_ids)
    if not targets <= lookup.keys():
        raise ValueError('Project-fact targets must belong to the full evidence catalog')
    units = {u['id']: u for u in provisions} if provisions is not None else None
    owners = set(target_provision_ids or ())
    if units is not None and (not owners <= units.keys() or
            targets != {i for p in owners for i in units[p]['evidence_ids']}):
        raise ValueError('Project-fact ownership must match the assigned source provisions')
    findings, facts, exclusions = [], [], []
    indexed, excluded = set(), set()

    def issue(code, path, **details):
        findings.append({'code': code, 'path': path, **details})

    def resolve(identifier):
        if not isinstance(identifier, str):
            return None
        return reference_map.get(identifier) if reference_map is not None else identifier

    if not isinstance(response, dict) or set(response) != {'verpflichtungen', 'nicht_als_fakt_erfasst'}:
        issue('invalid_clause_index_response', '$')
    rows = response.get('verpflichtungen') if isinstance(response, dict) else None
    omitted = response.get('nicht_als_fakt_erfasst') if isinstance(response, dict) else None
    if not isinstance(rows, list):
        issue('missing_or_invalid_project_facts', '$.verpflichtungen')
        rows = []
    if not isinstance(omitted, list):
        issue('missing_passage_exclusions', '$.nicht_als_fakt_erfasst')
        omitted = []
    for position, row in enumerate(rows):
        path = f'$.verpflichtungen[{position}]'
        refs = row.get('zitate') if isinstance(row, dict) else None
        fields = {'interpretation', 'uncertainty', 'zitate'} if interpretations else {'zitate'}
        if require_claims:
            fields.add('claims')
        if authoritative_claims:
            fields.discard('interpretation')
        if units is not None:
            fields.update(('provision_id', 'status', 'related_provision_ids'))
        if not isinstance(row, dict) or (set(row) - ({'interpretation'} if authoritative_claims else set())) != fields:
            issue('invalid_project_fact', path)
        if interpretations:
            interpretation = row.get('interpretation') if isinstance(row, dict) else None
            uncertainty = row.get('uncertainty') if isinstance(row, dict) else None
            if not authoritative_claims and (not isinstance(interpretation, str) or not interpretation.strip()):
                issue('missing_or_invalid_interpretation', path+'.interpretation')
            if uncertainty is not None and (not isinstance(uncertainty, str) or (not authoritative_claims and not uncertainty.strip())):
                issue('invalid_fact_uncertainty', path+'.uncertainty')
        selected, claimed = set(), set()
        if not isinstance(refs, list) or (not refs and not derive_fact_citations):
            issue('missing_citations', path+'.zitate')
        else:
            for n, ref in enumerate(refs):
                identifier = ref.get('evidence_id') if isinstance(ref, dict) else None
                location = f'{path}.zitate[{n}]'
                if not isinstance(ref, dict) or set(ref) != {'evidence_id'} or not isinstance(identifier, str):
                    issue('invalid_evidence_reference', location)
                elif resolve(identifier) not in lookup:
                    issue('unknown_evidence_id', location, evidence_id=identifier)
                elif resolve(identifier) in selected:
                    if not authoritative_claims:
                        issue('duplicate_fact_reference', location, evidence_id=identifier)
                else:
                    selected.add(resolve(identifier))
        # Source order is authoritative, even if the model returns IDs in reverse order.
        citations = [canonical(e) for e in entries if e['evidence_id'] in selected]
        value = {'verified_citations': citations, 'text': source_text(citations, pages)}
        if require_claims:
            claims = row.get('claims') if isinstance(row, dict) else None
            if not isinstance(claims, list) or not claims:
                issue('missing_or_invalid_fact_claims', path+'.claims')
            bound, claimed = [], set()
            for n, claim in enumerate(claims if isinstance(claims, list) else []):
                location = f'{path}.claims[{n}]'
                statement = claim.get('statement') if isinstance(claim, dict) else None
                identifiers = claim.get('evidence_ids') if isinstance(claim, dict) else None
                if not isinstance(claim, dict) or set(claim) != {'statement', 'evidence_ids'}:
                    issue('invalid_fact_claim', location)
                if not isinstance(statement, str) or not statement.strip():
                    issue('missing_or_invalid_claim_statement', location+'.statement')
                own = set()
                if not isinstance(identifiers, list) or not identifiers:
                    issue('missing_or_invalid_claim_evidence', location+'.evidence_ids')
                for j, identifier in enumerate(identifiers if isinstance(identifiers, list) else []):
                    reference_path = f'{location}.evidence_ids[{j}]'
                    if not isinstance(identifier, str) or not identifier.strip():
                        issue('invalid_claim_evidence_reference', reference_path)
                    elif resolve(identifier) not in lookup:
                        issue('unknown_evidence_id', reference_path, evidence_id=identifier)
                    elif resolve(identifier) in own:
                        if not authoritative_claims:
                            issue('duplicate_claim_reference', reference_path, evidence_id=identifier)
                    else:
                        identifier = resolve(identifier)
                        own.add(identifier)
                        if identifier not in selected and not derive_fact_citations:
                            issue('claim_evidence_not_in_fact_citations', reference_path, evidence_id=identifier)
                claimed.update(own)
                own_citations = [canonical(e) for e in entries if e['evidence_id'] in own]
                bound.append({'response_position': n, 'statement': statement,
                              'verified_citations': own_citations, 'text': source_text(own_citations, pages)})
            if allow_context_citations:
                value['context_citations'] = [canonical(e) for e in entries
                                              if e['evidence_id'] in selected - claimed]
            else:
                for identifier in sorted(selected - claimed):
                    issue('fact_citation_without_claim', path+'.zitate', evidence_id=identifier)
            value['claims'] = bound
            if authoritative_claims:
                value['interpretation'] = '\n'.join(dict.fromkeys(c['statement'].strip()
                    for c in bound if isinstance(c['statement'], str) and c['statement'].strip()))
                uncertainty = row.get('uncertainty') if isinstance(row, dict) else None
                value['uncertainty'] = (uncertainty.strip() or None) if isinstance(uncertainty, str) else uncertainty
            if derive_fact_citations:
                selected.update(claimed)
                citations = [canonical(e) for e in entries if e['evidence_id'] in selected]
                value.update(verified_citations=citations, text=source_text(citations, pages))
        if target_evidence_ids is not None and not selected & targets:
            issue('fact_without_target_evidence', path)
        if units is not None:
            from .provisions import FACT_STATUSES
            owner = row.get('provision_id') if isinstance(row, dict) else None
            status = row.get('status') if isinstance(row, dict) else None
            related = row.get('related_provision_ids') if isinstance(row, dict) else None
            valid_owner = isinstance(owner, str) and owner in units
            if not valid_owner:
                issue('unknown_provision_id', path+'.provision_id', provision_id=owner)
            elif owner not in owners:
                issue('provision_not_owned_by_batch', path+'.provision_id', provision_id=owner)
            elif not claimed.intersection(units[owner]['evidence_ids']):
                issue('fact_without_owned_claim_evidence', path+'.claims', provision_id=owner)
            if valid_owner and owner in owners:
                primary = set(units[owner]['evidence_ids'])
                for number, claim in enumerate(value.get('claims', [])):
                    citations = claim['verified_citations']
                    if citations and not primary.intersection(c['evidence_id'] for c in citations):
                        issue('claim_outside_owned_provision', f'{path}.claims[{number}].evidence_ids', provision_id=owner)
            if status not in FACT_STATUSES:
                issue('missing_or_invalid_fact_status', path+'.status')
            if status == 'UNRESOLVED' and not (isinstance(uncertainty, str) and uncertainty.strip()):
                issue('unresolved_fact_without_uncertainty', path+'.uncertainty')
            links = []
            if not isinstance(related, list):
                issue('invalid_provision_links', path+'.related_provision_ids')
            else:
                for number, identifier in enumerate(related):
                    location = f'{path}.related_provision_ids[{number}]'
                    if not isinstance(identifier, str) or identifier not in units:
                        issue('unknown_provision_id', location, provision_id=identifier)
                    elif identifier == owner:
                        issue('self_provision_reference', location, provision_id=identifier)
                    elif identifier not in links:
                        links.append(identifier)
            value.update(provision_id=owner if valid_owner else None, status=status, related_provision_ids=links)
        indexed.update(selected)
        facts.append(value)
    for position, row in enumerate(omitted):
        path = f'$.nicht_als_fakt_erfasst[{position}]'
        identifier = row.get('evidence_id') if isinstance(row, dict) else None
        reason = row.get('grund') if isinstance(row, dict) else None
        if (not isinstance(row, dict) or set(row) != {'evidence_id', 'grund'}
                or not isinstance(reason, str) or not reason.strip()):
            issue('invalid_passage_exclusion', path)
        citation = None
        if not isinstance(identifier, str) or resolve(identifier) not in lookup:
            issue('unknown_evidence_id', path, evidence_id=identifier)
        else:
            identifier = resolve(identifier)
            citation = canonical(lookup[identifier])
            if identifier not in targets:
                issue('exclusion_outside_targets', path, evidence_id=identifier)
            if identifier in excluded and not authoritative_claims:
                issue('duplicate_passage_exclusion', path, evidence_id=identifier)
            excluded.add(identifier)
        exclusions.append({'citation': citation})
    for identifier in sorted(indexed & excluded):
        issue('conflicting_passage_disposition', '$', evidence_id=identifier)
    missing = [i for i in lookup if i in targets and i not in indexed | excluded]
    for identifier in missing:
        issue('unaccounted_source_passage', '$', evidence_id=identifier)
    coverage = {'status': 'incomplete' if findings else 'complete',
                'catalog_sha256': digest(entries),
                'expected_ids': [i for i in lookup if i in targets],
                'indexed_ids': [i for i in lookup if i in indexed & targets],
                'excluded_ids': [i for i in lookup if i in excluded],
                'missing_ids': missing}
    if target_evidence_ids is not None:
        coverage['supporting_ids'] = [i for i in lookup if i in indexed - targets]
    return facts, exclusions, coverage, findings


def complete_response(prompt, response):
    """Application readiness only; never decides whether provider answers are stored."""
    if not prompt.startswith('PAGE_PROJECT_FACTS.'):
        return True
    from .payload import expand_payload
    payload = expand_payload(json.loads(prompt.split('SOURCE DATA:\n', 1)[1]))
    if payload.get('evidence_protocol') in ('contractual-rights-v1', 'contractual-rights-v2', 'contractual-rights-v3'):
        from .rights import records
        # Payload contains the lossless pages; rebuild the source descriptors.
        sources = [{**scope, 'title': scope['document_title'], 'pages': [
            {'pdf_page': p['pdf_page'], 'text': p['text']} for p in payload['source_pages']
            if (p['source_key'], p['document_id']) == (scope['source_key'], scope['document_id'])]}
            for scope in payload['documents']]
        metadata = {'context_scope': payload['context_scope'], 'sources': payload['documents'],
                    'target_evidence_ids': payload['target_evidence_ids']}
        return not records(response, sources, metadata, 'local-validation',
                           evidence_protocol=payload['evidence_protocol'])[2]
    return index_response(response, payload['evidence_catalog'],
        target_evidence_ids=payload.get('target_evidence_ids'),
        require_claims=payload.get('evidence_protocol') in ('source-backed-interpretations-v4', 'source-backed-interpretations-v5', 'source-backed-interpretations-v6', 'source-backed-interpretations-v7', 'source-backed-interpretations-v8', 'source-backed-interpretations-v9'),
        allow_context_citations=payload.get('evidence_protocol') in ('source-backed-interpretations-v5', 'source-backed-interpretations-v6', 'source-backed-interpretations-v7', 'source-backed-interpretations-v8', 'source-backed-interpretations-v9'),
        derive_fact_citations=payload.get('evidence_protocol') in ('source-backed-interpretations-v6', 'source-backed-interpretations-v7', 'source-backed-interpretations-v8', 'source-backed-interpretations-v9'),
        authoritative_claims=payload.get('evidence_protocol') in ('source-backed-interpretations-v7', 'source-backed-interpretations-v8', 'source-backed-interpretations-v9'),
        reference_map=payload.get('reference_map'), provisions=payload.get('provisions'),
        target_provision_ids=payload.get('target_provision_ids'))[2]['status'] == 'complete'
