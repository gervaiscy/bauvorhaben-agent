"""Recover complete responses from an interrupted run without claiming model completion.

Raw evidence stays immutable. Source-specific corrections are fingerprint-bound review
data, never generic scheduling heuristics. Missing or rejected records remain local gaps.
"""
import copy
import json
from pathlib import Path
from customer_report import fingerprint
from source_integrity import realign_citations, verify_citations
from timing_assessment import validate_assessments, calculate


def recover(run, payload, proposal, review, decisions=None, supplement=None):
    run = Path(run)
    manifest = json.loads((run/'run.json').read_text())
    binding = {'input': payload['input_fingerprint'], 'proposal': fingerprint(proposal),
               'review': fingerprint(review)}
    if any(manifest.get(k) != v for k, v in binding.items()):
        raise ValueError('Recovery snapshot mismatch')
    size = manifest['batch_size']
    if type(size) is not int or size < 1:
        raise ValueError('Invalid batch size')
    original, provenance = {}, []
    for index, start in enumerate(range(0, len(proposal['units']), size)):
        expected = {u['id'] for u in proposal['units'][start:start+size]}
        accepted = run/f'batch-{index:03}.json'
        attempts = sorted(run.glob(f'batch-{index:03}-attempt-*.raw.json'),
                          key=lambda p: int(p.name.split('-attempt-')[1].split('.')[0]))
        source = accepted if accepted.exists() else (attempts[-1] if attempts else None)
        if source is None:
            continue  # Partial text is evidence, not a complete model assessment.
        raw = json.loads(source.read_text())
        records = raw.get('assessments', [])
        ids = [a.get('unit_id') for a in records]
        if len(ids) != len(set(ids)) or set(ids) != expected:
            raise ValueError(f'Batch {index} coverage mismatch')
        aligned, repairs = realign_citations(raw, payload['source_registry'])
        original.update({a['unit_id']: a for a in aligned['assessments']})
        provenance.append({'batch': index, 'file': source.name,
                           'fingerprint': fingerprint(raw), 'quote_realignments': repairs})
    if supplement is not None:
        supplement=Path(supplement)
        extra_manifest=json.loads((supplement/'run.json').read_text())
        if any(extra_manifest.get(k)!=v for k,v in binding.items()):
            raise ValueError('Supplement snapshot mismatch')
        extra=json.loads((supplement/'assessments.json').read_text())
        requested=extra_manifest.get('requested_ids',[])
        validate_assessments(extra,requested,payload,proposal)
        if set(requested)&set(original):
            raise ValueError('Supplement cannot replace an existing model response')
        original.update({a['unit_id']:a for a in extra})
        provenance.append({'supplement_manifest':extra_manifest,'file':'assessments.json',
                           'fingerprint':fingerprint(extra),'quote_realignments':[]})
    decisions = decisions or {**binding, 'changes': []}
    if any(decisions.get(k) != v for k, v in binding.items()):
        raise ValueError('Timing review snapshot mismatch')
    changes = {d['unit_id']: d for d in decisions['changes']}
    if len(changes) != len(decisions['changes']) or set(changes)-set(original):
        raise ValueError('Duplicate or unknown timing review unit')
    records, audit = [], []
    for unit in proposal['units']:
        uid = unit['id']
        record = copy.deepcopy(original.get(uid))
        state, reason = 'MODEL_RESPONSE', None
        if uid in changes:
            change = changes[uid]
            if change.get('before') != fingerprint(record) or not change.get('reason'):
                raise ValueError('Stale or unjustified timing correction: '+uid)
            verify_citations(change['citations'], payload['source_registry'])
            record = copy.deepcopy(change['replacement'])
            if record.get('unit_id') != uid:
                raise ValueError('Correction changed unit identity')
            state, reason = 'SOURCE_REVIEW_CORRECTION', change['reason']
        if record is None:
            state, reason = 'MISSING_MODEL_RESPONSE', 'Keine vollständige Gemini-Antwort im heruntergeladenen Lauf.'
        else:
            try:
                validate_assessments([record], [uid], payload, proposal)
            except (ValueError, TypeError, KeyError) as exc:
                state, reason = 'REJECTED_MODEL_RESPONSE', str(exc)
                record = None
        if record is None:
            finding = ('EXCLUDED' if unit['scope']['status']=='DOES_NOT_APPLY' else
                       'DOCUMENT_RECORD' if unit['kind']=='EVENT' else 'AMBIGUOUS')
            record = {'unit_id': uid, 'finding': finding, 'reason': reason,
                      'relative_text': 'Zeitliche Einordnung offen; vorhandene Vertragsangaben unter „Wann?“ bleiben maßgeblich.',
                      'citations': copy.deepcopy(unit['citations']), 'rules': []}
        records.append(record)
        audit.append({'unit_id': uid, 'origin': state, 'reason': reason,
                      'model_response_present': uid in original})
    # Do not prune graph edges arbitrarily to make a cyclic proposal pass.
    result = calculate(payload, proposal, records)
    result['recovery'] = {**binding, 'model_manifest': manifest, 'provenance': provenance,
                          'units': audit, 'review_decisions_fingerprint': fingerprint(decisions)}
    result['coverage'].update(model_responses=len(original),
                             missing_model_responses=len(records)-len(original),
                             source_review_corrections=sum(x['origin']=='SOURCE_REVIEW_CORRECTION' for x in audit),
                             rejected_model_responses=sum(x['origin']=='REJECTED_MODEL_RESPONSE' for x in audit),
                             unresolved=sum(x['finding']=='AMBIGUOUS' for x in records))
    for row in result['units']:
        row['assessment_origin'] = next(x['origin'] for x in audit if x['unit_id']==row['unit_id'])
        if row['review_required']:
            row['no_date_reason'] = 'Terminprüfung offen: '+row['reason']
    result['digest'] = fingerprint({k:v for k,v in result.items() if k!='digest'})
    return records, result


def main():
    import argparse
    from enrich_working_units import write_json
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['run','input','proposal','review','decisions','output-dir']:
        parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--supplement',type=Path)
    args=parser.parse_args()
    if args.output_dir.exists():parser.error('Use a new output directory')
    read=lambda path:json.loads(path.read_text())
    records,result=recover(args.run,read(args.input),read(args.proposal),read(args.review),read(args.decisions),args.supplement)
    write_json(args.output_dir/'assessments.json',records)
    write_json(args.output_dir/'timing-result.json',result)
    print(json.dumps(result['coverage']))


if __name__=='__main__':main()
