"""Apply an evidence-bound semantic review without changing raw model outputs."""
import argparse
import copy
import json
from pathlib import Path

from batched_report import apply_global_patch
from customer_report import build_input, compile_report, fingerprint, render_report, render_summary
from enrich_working_units import load_manifest, write_json


def apply_review(payload, proposal, review):
    if review.get('input_fingerprint') != payload['input_fingerprint']:
        raise ValueError('Review belongs to another input snapshot')
    if review.get('base_proposal_fingerprint') != fingerprint(proposal):
        raise ValueError('Review belongs to another proposal')
    if not review.get('reviewer') or not review.get('reason'):
        raise ValueError('Review needs attribution and reason')
    updated = apply_global_patch(proposal, review['patch'], payload)
    if 'project_notes' in review:
        updated['project_notes'] = copy.deepcopy(review['project_notes'])
    if 'resolved_questions' in review:
        resolutions = review['resolved_questions']
        if any(not isinstance(r, dict) or not r.get('reason') or r.get('question') not in proposal['unresolved_questions'] for r in resolutions):
            raise ValueError('Question resolution needs an original question and reason')
        resolved = {r['question'] for r in resolutions}
        updated['unresolved_questions'] = [q for q in updated['unresolved_questions'] if q not in resolved]
    priority = review.get('priority_questions', [])
    if any(q not in updated['unresolved_questions'] for q in priority):
        raise ValueError('Priority question must be part of the reviewed questions')
    updated['unresolved_questions'] = priority + [q for q in updated['unresolved_questions'] if q not in priority]
    rules = {r['id']: r for r in updated['rules']}
    # These are external review requirements, never supplied by a model response.
    for expected in review.get('required_rules', []):
        actual = rules.get(expected['id'])
        if actual is None or any(actual.get(k) != v for k, v in expected.items()):
            raise ValueError('Missing or altered required rule: ' + expected['id'])
    report = compile_report(payload, updated)
    if report['status'] == 'BLOCKED':
        raise ValueError(json.dumps(report['errors'], ensure_ascii=False))
    audit = {'input_fingerprint': payload['input_fingerprint'],
             'base_proposal_fingerprint': fingerprint(proposal),
             'review_fingerprint': fingerprint(review),
             'result_proposal_fingerprint': fingerprint(updated),
             'review_digest': report['review_digest'],
             'reviewer': review['reviewer'], 'reason': review['reason'],
             'publication_approved': False}
    return updated, report, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--proposal', required=True, type=Path)
    parser.add_argument('--review-patch', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error('Use a new output directory; review artifacts are immutable')
    manifest = json.loads(args.manifest.read_text())
    signed, sources = load_manifest(args.manifest)
    payload = build_input(sources, signed, manifest.get('report_settings'))
    proposal = json.loads(args.proposal.read_text())
    review = json.loads(args.review_patch.read_text())
    updated, report, audit = apply_review(payload, proposal, review)
    args.output_dir.mkdir(parents=True)
    for name, value in [('reconciliation.json', updated), ('validated-report.json', report),
                        ('review-patch.json', review), ('review-audit.json', audit)]:
        write_json(args.output_dir/name, value)
    (args.output_dir/'customer-draft.html').write_text(render_report(report), encoding='utf-8')
    (args.output_dir/'customer-summary.html').write_text(render_summary(report), encoding='utf-8')
    print(f"DRAFT: {len(report['units'])} canonical units; {len(report['member_to_canonical'])} input members; {len(updated['rules'])} reviewed rules; {len(report['errors'])} validation errors")

if __name__ == '__main__':
    main()
