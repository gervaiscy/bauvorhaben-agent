"""Rebind saved claim evidence under the context-citation policy without model calls."""
import copy

from ...core import digest
from .artifact import validate_facts
from .full_context import UNION_EVIDENCE_PROTOCOL, AUTHORITATIVE_CLAIMS_EVIDENCE_PROTOCOL, records


def revalidate_context_citations(collection, documents):
    """Preserve raw answers and old findings; recompute only derived evidence and checks."""
    original = validate_facts(collection, documents)
    if original['schema_version'] not in (8, 9):
        raise ValueError('Citation-union revalidation requires a schema-8 or schema-9 claim collection')
    result = copy.deepcopy(original)
    result['schema_version'] = 10
    result['evidence_protocol'] = UNION_EVIDENCE_PROTOCOL
    result['validation_history'] = [*result.get('validation_history', []), {
        'operation': 'fact_citation_union_revalidation_without_model_calls',
        'source_schema_version': original['schema_version'],
        'source_evidence_protocol': original['evidence_protocol'],
        'source_artifact_sha256': digest(original),
        'previous_status': original['status'],
        'review_findings': copy.deepcopy(original['review_findings']),
        'fact_review_findings': [{'id': fact['id'], 'review_findings': copy.deepcopy(fact['review_findings'])}
                                 for fact in original['facts'] if fact['review_findings']]}]
    result['facts'], result['excluded_passages'] = [], []
    # Recompute structural findings under the new policy; retain separate review notes.
    # The complete previous findings remain in validation_history, including coverage
    # findings that become obsolete when claim-only IDs now account for their targets.
    old_findings, old_fact_findings = [], {}
    for response in original['gemini_responses']:
        if response.get('status') == 'request_failed':
            continue
        facts, _, _, findings = records(response['gemini'], original['source_documents'],
            target_evidence_ids=response['target_evidence_ids'], response_id=response['id'],
            require_claims=True, allow_context_citations=original['schema_version'] == 9)
        old_findings.extend(findings)
        old_fact_findings.update({fact['id']: fact['review_findings'] for fact in facts})
    result['review_findings'] = [copy.deepcopy(issue) for issue in original['review_findings']
                                 if issue not in old_findings]
    old_facts = {fact['id']: fact for fact in original['facts']}
    incomplete = False
    failed = False
    for response in result['gemini_responses']:
        if response.get('status') == 'request_failed':
            failed = True
            continue
        facts, exclusions, coverage, findings = records(response['gemini'], result['source_documents'],
            target_evidence_ids=response['target_evidence_ids'], response_id=response['id'],
            require_claims=True, allow_context_citations=True, derive_fact_citations=True)
        response['coverage'] = coverage
        incomplete = incomplete or bool(findings)
        for fact in facts:
            for issue in old_facts[fact['id']]['review_findings']:
                if issue in old_fact_findings[fact['id']]:
                    continue
                if issue not in fact['review_findings']:
                    fact['review_findings'].append(copy.deepcopy(issue))
        result['facts'].extend(facts)
        result['excluded_passages'].extend(exclusions)
        for issue in findings:
            if issue not in result['review_findings']:
                result['review_findings'].append(issue)
    result['status'] = ('request_failed' if failed else 'incomplete' if incomplete else
                        'needs_review' if result['review_findings'] or original['status'] == 'needs_review' else
                        'no_project_sources' if original['status'] == 'no_project_sources' else 'recorded')
    return validate_facts(result, documents)


def replay_project_facts(collection, documents):
    """Re-run local derivation only. Provider JSON and previous findings remain intact."""
    if collection.get('schema_version') in (14, 15):
        from .rights import replay
        return replay(collection, documents)
    original = validate_facts(collection, documents)
    version = original['schema_version']
    if version not in (8, 9, 10, 11, 12, 13):
        raise ValueError('Offline replay needs a saved full-context claim collection (schema 8–13)')
    result = copy.deepcopy(original)
    if version not in (12, 13):
        result.update(schema_version=11, evidence_protocol=AUTHORITATIVE_CLAIMS_EVIDENCE_PROTOCOL)
    result.setdefault('validation_history', []).append({
        'operation': 'offline_claim_replay_without_model_calls',
        'source_schema_version': version, 'source_evidence_protocol': original['evidence_protocol'],
        'source_artifact_sha256': digest(original), 'previous_status': original['status'],
        'review_findings': copy.deepcopy(original['review_findings']),
        'fact_review_findings': [{'id': f['id'], 'review_findings': copy.deepcopy(f['review_findings'])}
                                 for f in original['facts'] if f['review_findings']]})
    old_structural, old_own, old_records = [], {}, []
    for response in original['gemini_responses']:
        if response.get('status') == 'request_failed':
            continue
        facts, _, _, findings = records(response['gemini'], original['source_documents'],
            target_evidence_ids=response['target_evidence_ids'], response_id=response['id'],
            require_claims=True, allow_context_citations=version >= 9,
            derive_fact_citations=version >= 10, authoritative_claims=version >= 11,
            reference_map=original.get('reference_map') if version in (12, 13) else None,
            provisions=original.get('provisions') if version == 13 else None,
            target_provision_ids=response.get('target_provision_ids') if version == 13 else None)
        old_records.extend(facts)
        old_structural.extend(findings)
        old_own.update({f['id']: f['review_findings'] for f in facts})
    if version == 13:
        from .provisions import resolve_links
        old_structural.extend(resolve_links(old_records, original['gemini_responses']))
        old_own.update({f['id']: f['review_findings'] for f in old_records})
    result['review_findings'] = [copy.deepcopy(i) for i in original['review_findings'] if i not in old_structural]
    result['facts'], result['excluded_passages'] = [], []
    old_facts = {f['id']: f for f in original['facts']}
    failed, incomplete = False, False
    for response in result['gemini_responses']:
        if response.get('status') == 'request_failed':
            failed = True
            continue
        facts, exclusions, coverage, findings = records(response['gemini'], result['source_documents'],
            target_evidence_ids=response['target_evidence_ids'], response_id=response['id'],
            require_claims=True, allow_context_citations=True, derive_fact_citations=True,
            authoritative_claims=True, reference_map=result.get('reference_map') if version in (12, 13) else None,
            provisions=result.get('provisions') if version == 13 else None,
            target_provision_ids=response.get('target_provision_ids') if version == 13 else None)
        response['coverage'] = coverage
        incomplete |= bool(findings)
        for fact in facts:
            fact['review_findings'].extend(copy.deepcopy(i) for i in old_facts[fact['id']]['review_findings']
                if i not in old_own[fact['id']] and i not in fact['review_findings'])
        result['facts'].extend(facts)
        result['excluded_passages'].extend(exclusions)
        result['review_findings'].extend(i for i in findings if i not in result['review_findings'])
    if version == 13:
        links = resolve_links(result['facts'], result['gemini_responses'])
        incomplete |= bool(links)
        result['review_findings'].extend(i for i in links if i not in result['review_findings'])
    result['status'] = ('request_failed' if failed else 'incomplete' if incomplete else
                        'needs_review' if result['review_findings'] or original['status']=='needs_review' else
                        'no_project_sources' if not result['source_snapshots'] else 'recorded')
    return validate_facts(result, documents)
