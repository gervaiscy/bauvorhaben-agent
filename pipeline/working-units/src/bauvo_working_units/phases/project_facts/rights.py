"""Homeowner contractual position: one interpretation, exact evidence, no model-owned graph."""
import copy

from ...citation_evidence import canonical, CATALOG_PROTOCOL
from ...core import digest
from ...trace import ModelRequestFailed
from .full_context import context, source_windows, TARGET_BATCH_SIZE, CONTEXT_SCOPE
from .index import source_text
from .payload import reference_map, REFERENCE_PROTOCOL


LEGACY_EVIDENCE_PROTOCOL = 'contractual-rights-v1'
RIGHTS_ONLY_EVIDENCE_PROTOCOL = 'contractual-rights-v2'
EVIDENCE_PROTOCOL = 'contractual-rights-v3'
EXTRACTION_SCOPE = 'homeowner_contractual_position'
SCOPES = {LEGACY_EVIDENCE_PROTOCOL: 'homeowner_contractual_rights',
          RIGHTS_ONLY_EVIDENCE_PROTOCOL: 'homeowner_contractual_rights',
          EVIDENCE_PROTOCOL: EXTRACTION_SCOPE}
SCHEMA_VERSION = 15
VALIDATION_PROTOCOL = 'full-contract-evidence-v1'


def target_batches(sources):
    """Keep paragraph/section boundaries internally; assign no ownership IDs to Gemini."""
    from .provisions import batches
    pages, entries, metadata, rid = context(sources)
    return [({k: v for k, v in scope.items() if k != 'target_provision_ids'}, identifier)
            for scope, identifier in batches(pages, entries, metadata, rid, TARGET_BATCH_SIZE)]


def records(response, sources, metadata, response_id, *, evidence_protocol=EVIDENCE_PROTOCOL,
            _historical_target_validation=False):
    """Bind source references only. Valid evidence does not certify legal meaning."""
    if evidence_protocol not in SCOPES:
        raise ValueError('Unknown contractual-rights evidence protocol')
    legacy = evidence_protocol == LEGACY_EVIDENCE_PROTOCOL
    pages, entries, _, _ = context(sources)
    mapping = reference_map(entries)
    lookup = {e['evidence_id']: e for e in entries}
    targets = set(metadata['target_evidence_ids'])
    if not targets <= lookup.keys():
        raise ValueError('Rights assignment differs from original source passages')
    findings, facts, selected = [], [], set()

    def issue(code, path, **details):
        findings.append({'pass': 'PAGE_PROJECT_FACTS', 'response_id': response_id,
                         **copy.deepcopy(metadata), 'code': code, 'path': path, **details})

    if not isinstance(response, dict) or set(response) != {'ansprueche'}:
        issue('invalid_rights_response', '$')
    rows = response.get('ansprueche') if isinstance(response, dict) else None
    if not isinstance(rows, list):
        issue('missing_or_invalid_rights', '$.ansprueche')
        rows = []
    for position, row in enumerate(rows):
        path = f'$.ansprueche[{position}]'
        before = len(findings)
        expected_fields = {'aussage', 'belege'} | ({'unklarheit'} if legacy else set())
        if not isinstance(row, dict) or set(row) != expected_fields:
            issue('invalid_right', path)
        fields = row if isinstance(row, dict) else {}
        statement, refs = (fields.get(k) for k in ('aussage', 'belege'))
        if not isinstance(statement, str) or not statement.strip():
            issue('missing_or_invalid_right_statement', path+'.aussage')
        if legacy:
            uncertainty = fields.get('unklarheit')
            if uncertainty is not None and (not isinstance(uncertainty, str) or not uncertainty.strip()):
                issue('invalid_right_uncertainty', path+'.unklarheit')
        if not isinstance(refs, list) or not refs:
            issue('missing_right_evidence', path+'.belege')
        identifiers = set()
        for number, ref in enumerate(refs if isinstance(refs, list) else []):
            identifier = mapping.get(ref) if isinstance(ref, str) else None
            if identifier not in lookup:
                issue('unknown_evidence_id', f'{path}.belege[{number}]', evidence_id=ref)
            else:
                identifiers.add(identifier)
        # Only reconstruct the retired check when verifying an older saved artifact.
        # New extraction and offline revalidation accept evidence from the full package.
        if _historical_target_validation and identifiers and not identifiers & targets:
            issue('right_without_target_evidence', path+'.belege')
        # Normalize repetition only in derived citations; retain every raw selection.
        citations = [canonical(e) for e in entries if e['evidence_id'] in identifiers]
        selected.update(identifiers)
        keys = {(c['source_key'], c['document_id']) for c in citations}
        scopes = [s for s in metadata['sources'] if (s['source_key'], s['document_id']) in keys]
        own = scopes[0] if len(scopes) == 1 else {}
        numbers = list(dict.fromkeys(c['pdf_page'] for c in citations))
        facts.append({'id': f'{response_id}:fact:{position:03d}',
            'gemini': copy.deepcopy(row), 'review_findings': copy.deepcopy(findings[before:]),
            'derived': {**copy.deepcopy(metadata), 'sources': copy.deepcopy(scopes),
                'source_key': own.get('source_key'), 'document_id': own.get('document_id'),
                'document_title': own.get('document_title'), 'pages': numbers, 'primary_pages': numbers,
                'response_id': response_id, 'response_position': position,
                'interpretation': statement, **({'uncertainty': uncertainty} if legacy else {}),
                'verified_citations': citations, 'text': source_text(citations, pages)}})
    validation = {'status': 'invalid' if findings else 'valid',
                  'selected_ids': [e['evidence_id'] for e in entries if e['evidence_id'] in selected]}
    return facts, validation, findings


def collect(sources, snapshots, excluded, model, language):
    from ...call_budget import prepare_requests
    from ...page_flow import _prompt
    pages, entries, _, _ = context(sources)
    references = reference_map(entries)
    requests = [(scope, rid, _prompt('PAGE_PROJECT_FACTS', {
        **scope, 'source_pages': pages, 'evidence_catalog': entries,
        'catalog_protocol': CATALOG_PROTOCOL, 'evidence_protocol': EVIDENCE_PROTOCOL}, language))
        for scope, rid in target_batches(sources)]
    prepare_requests(model, [prompt for _, _, prompt in requests])
    facts, responses, findings = [], [], []
    for metadata, rid, prompt in requests:
        saved = {'id': rid, 'pass': 'PAGE_PROJECT_FACTS', **copy.deepcopy(metadata),
                 'reference_map_sha256': digest(references)}
        try:
            response = model.extract(prompt)
        except ModelRequestFailed as exc:
            failure = {'code': 'model_request_failed', 'pass': 'PAGE_PROJECT_FACTS',
                       'response_id': rid, **copy.deepcopy(metadata), 'attempts': copy.deepcopy(exc.attempts)}
            findings.append(failure)
            saved.update(status='request_failed', gemini=None, failure=failure)
        else:
            batch_facts, validation, issues = records(response, sources, metadata, rid)
            facts.extend(batch_facts)
            findings.extend(issues)
            saved.update(gemini=copy.deepcopy(response), response_validation=validation)
        responses.append(saved)
    failed = any(r.get('status') == 'request_failed' for r in responses)
    return {'schema_version': SCHEMA_VERSION, 'evidence_protocol': EVIDENCE_PROTOCOL,
        'validation_protocol': VALIDATION_PROTOCOL,
        'extraction_scope': EXTRACTION_SCOPE,
        'reference_protocol': REFERENCE_PROTOCOL, 'reference_map': references,
        'target_batch_size': TARGET_BATCH_SIZE, 'context_scope': CONTEXT_SCOPE,
        'catalog_protocol': CATALOG_PROTOCOL,
        'status': 'request_failed' if failed else 'incomplete' if findings else
                  'no_project_sources' if not snapshots else 'recorded',
        'processing': processing(responses),
        'facts': facts, 'excluded_passages': [], 'source_snapshots': snapshots,
        'source_documents': sources, 'excluded_sources': excluded,
        'windows': source_windows(sources), 'gemini_responses': responses, 'review_findings': findings}


def processing(responses):
    return {'planned_batches': len(responses),
            'completed_batches': sum(r.get('status') != 'request_failed' for r in responses),
            'semantic_completeness': 'not_verified'}


def validate_collection(collection, sources):
    """Recompute derived data and the exact request plan against original sources."""
    pages, entries, _, _ = context(sources)
    references = reference_map(entries)
    validation_protocol = collection.get('validation_protocol')
    if validation_protocol not in (None, VALIDATION_PROTOCOL):
        raise ValueError('Unknown saved rights validation protocol')
    protocol = collection.get('evidence_protocol')
    allowed = {14: (LEGACY_EVIDENCE_PROTOCOL,),
               15: (RIGHTS_ONLY_EVIDENCE_PROTOCOL, EVIDENCE_PROTOCOL)}[collection['schema_version']]
    if protocol not in allowed:
        raise ValueError('Saved rights protocol does not match its schema')
    if any(collection.get(k) != value for k, value in {
        'evidence_protocol': protocol, 'extraction_scope': SCOPES[protocol],
        'reference_protocol': REFERENCE_PROTOCOL, 'reference_map': references,
        'target_batch_size': TARGET_BATCH_SIZE, 'context_scope': CONTEXT_SCOPE,
        'catalog_protocol': CATALOG_PROTOCOL, 'source_documents': sources,
        'windows': source_windows(sources), 'excluded_passages': []}.items()):
        raise ValueError('Saved rights context or source mapping does not match original sources')
    specs = target_batches(sources)
    responses = collection['gemini_responses']
    if (any(not isinstance(r, dict) for r in responses)
            or len(responses) != len(specs) or collection.get('processing') != processing(responses)):
        raise ValueError('Saved rights require every planned batch and unchanged processing metadata')
    expected, failed, invalid = [], False, False
    for response, (metadata, rid) in zip(responses, specs):
        if (not isinstance(response, dict) or response.get('id') != rid
                or response.get('pass') != 'PAGE_PROJECT_FACTS'
                or response.get('reference_map_sha256') != digest(references)
                or any(response.get(k) != v for k, v in metadata.items())):
            raise ValueError('Saved rights response source scope changed')
        if response.get('status') == 'request_failed':
            failed = True
            failure = response.get('failure')
            if (response.get('gemini') is not None or not isinstance(failure, dict)
                    or not isinstance(failure.get('attempts'), list)
                    or any(failure.get(k) != v for k, v in {
                        'code': 'model_request_failed', 'pass': 'PAGE_PROJECT_FACTS',
                        'response_id': rid, **metadata}.items())
                    or failure not in collection['review_findings']):
                raise ValueError('Saved rights request failure was discarded or changed')
            continue
        facts, validation, issues = records(response.get('gemini'), sources, metadata, rid,
            evidence_protocol=protocol, _historical_target_validation=validation_protocol is None)
        expected.extend(facts)
        invalid |= bool(issues)
        if (response.get('response_validation') != validation
                or any(i not in collection['review_findings'] for i in issues)):
            raise ValueError('Saved rights validation or findings changed')
    if (failed and collection['status'] != 'request_failed'
            or invalid and collection['status'] not in ('incomplete', 'request_failed')
            or len(expected) != len(collection['facts'])):
        raise ValueError('Saved rights failures or records were discarded')
    for actual, fact in zip(collection['facts'], expected):
        if (not isinstance(actual, dict) or any(actual.get(k) != v for k, v in fact.items()
                if k != 'review_findings') or not isinstance(actual.get('review_findings'), list)
                or any(i not in actual['review_findings'] for i in fact['review_findings'])):
            raise ValueError('Saved rights interpretation, evidence or provenance changed')


def replay(collection, documents):
    """Replay rights without reinterpreting old formats as the new extraction goal."""
    from .artifact import validate_facts
    original = validate_facts(collection, documents)
    result = copy.deepcopy(original)
    result.setdefault('validation_history', []).append({
        'operation': 'offline_rights_replay_without_model_calls',
        'source_artifact_sha256': digest(original), 'previous_status': original['status'],
        'previous_validation_protocol': original.get('validation_protocol'),
        'review_findings': copy.deepcopy(original['review_findings']),
        'fact_review_findings': [{'id': f['id'], 'review_findings': copy.deepcopy(f['review_findings'])}
                                 for f in original['facts'] if f['review_findings']],
        'response_validations': [{'id': r['id'], 'response_validation': copy.deepcopy(r['response_validation'])}
                                 for r in original['gemini_responses'] if 'response_validation' in r]})
    if original.get('validation_protocol') is None:
        # Original artifact was verified under its own rules above. Rebuild only
        # application data; retain every model statement, selection and source.
        retired = 'right_without_target_evidence'
        result['validation_protocol'] = VALIDATION_PROTOCOL
        result['review_findings'] = [i for i in result['review_findings'] if i.get('code') != retired]
        previous = {fact['id']: fact for fact in original['facts']}
        result['facts'] = []
        failed, invalid = False, False
        for response in result['gemini_responses']:
            if response.get('status') == 'request_failed':
                failed = True
                continue
            metadata = {k: response[k] for k in ('context_scope', 'sources', 'target_evidence_ids')}
            facts, validation, issues = records(response['gemini'], result['source_documents'],
                metadata, response['id'], evidence_protocol=result['evidence_protocol'])
            response['response_validation'] = validation
            invalid |= bool(issues)
            for fact in facts:
                for finding in previous[fact['id']]['review_findings']:
                    if finding.get('code') != retired and finding not in fact['review_findings']:
                        fact['review_findings'].append(copy.deepcopy(finding))
            result['facts'].extend(facts)
            result['review_findings'].extend(i for i in issues if i not in result['review_findings'])
        result['status'] = ('request_failed' if failed else 'incomplete' if invalid else
                            'needs_review' if result['review_findings'] or original['status'] == 'needs_review' else
                            'no_project_sources' if not result['source_snapshots'] else 'recorded')
    return validate_facts(result, documents)
