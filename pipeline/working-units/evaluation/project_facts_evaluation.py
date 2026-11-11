"""Run project-fact controls only; expectations stay outside provider requests."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from bauvo_working_units.gemini import GeminiModel
from bauvo_working_units.phases.project_facts.full_context import TARGET_BATCH_SIZE
from bauvo_working_units.project_facts import collect_project_facts
from bauvo_working_units.project_flow import Document
from bauvo_working_units.schemas import GENERATION, PROTOCOL, PHASE_THINKING_LEVELS
from bauvo_working_units.trace import RawRunRecorder, RetryingModel, TracingModel
from bauvo_working_units.citation_evidence import catalog, CATALOG_PROTOCOL

BASE = Path(__file__).resolve().parent
CASES = BASE/'contractual-rights-cases.json'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def selection_checks(case, result):
    """Score authored synthetic line selections/groups; never supply them to Gemini."""
    expected = case.get('selection_expectations')
    if expected is None:
        return None
    entries = catalog([{'source_key': case['id'], 'document_id': 'D1',
                        'pdf_page': 1, 'text': case['text']}])
    intervals, offset = {}, 0
    for number, line in enumerate(case['text'].splitlines(keepends=True), 1):
        intervals[number] = (offset, offset + len(line))
        offset += len(line)

    def ids(numbers):
        return {entry['evidence_id'] for n in numbers for entry in entries
                if intervals[n][0] <= entry['start'] < intervals[n][1]}

    groups = [{c['evidence_id'] for c in fact['derived']['verified_citations']}
              for fact in result['facts']]
    indexed = set().union(*groups)
    excluded = {r['derived']['citation']['evidence_id'] for r in result['excluded_passages']
                if r['derived']['citation'] is not None}
    missing_indexed = sorted(ids(expected['indexed_lines']) - indexed)
    missing_excluded = sorted(ids(expected['excluded_lines']) - excluded)
    expected_groups = [ids(lines) for lines in expected['clause_groups']]
    groups_match = sorted(tuple(sorted(g)) for g in groups) == sorted(tuple(sorted(g)) for g in expected_groups)
    missing_uncertainty = [sorted(ids(lines)) for lines in expected.get('uncertain_groups', [])
        if not any(ids(lines) <= group and isinstance(fact['gemini'].get('uncertainty'), str)
                   and fact['gemini']['uncertainty'].strip() for group, fact in zip(groups, result['facts']))]
    return {'passed': result['status'] == 'recorded' and not missing_indexed and not missing_excluded
                      and groups_match and not missing_uncertainty,
            'missing_indexed_ids': missing_indexed, 'missing_excluded_ids': missing_excluded,
            'missing_uncertainty_groups': missing_uncertainty,
            'groups_match': groups_match}


def run(output, project=None, location='global', model=None, max_new_calls=0):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    files = [Path(__file__), CASES,
             *(BASE.parent/'src').rglob('*.py'), *(BASE.parent/'src').rglob('*.txt')]
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    write(output/'run-manifest.json', {'protocol': PROTOCOL, 'catalog_protocol': CATALOG_PROTOCOL,
          'model': 'gemini-3.5-flash',
          'generation': GENERATION, 'phase_thinking_levels': PHASE_THINKING_LEVELS,
          'facts_context_scope': 'full_contract_package', 'facts_target_batch_size': TARGET_BATCH_SIZE, 'inputs_and_code': hashes})
    if model is None:
        from bauvo_working_units.call_budget import BudgetedModel, CallBudget
        from bauvo_working_units.cache import CachedModel
        if max_new_calls and not project:
            raise ValueError('A project is required for paid evaluation calls')
        budget = CallBudget(max_new_calls)
        backend = CachedModel(BudgetedModel(lambda: GeminiModel(project, location, 'gemini-3.5-flash'), budget),
            output/'provider-responses', {'project': project, 'location': location, 'model': 'gemini-3.5-flash'},
            plan_path=output/'request-plan.json')
    else:
        backend = model
    summary = []
    for case in json.loads(CASES.read_text())['cases']:
        folder = output/case['id']
        folder.mkdir()
        write(folder/'expectations.json', case['expectations'])
        if 'selection_expectations' in case:
            write(folder/'selection-expectations.json', case['selection_expectations'])
        if 'claim_expectations' in case:
            write(folder/'claim-expectations.json', case['claim_expectations'])
        if 'interpretation_expectations' in case:
            write(folder/'interpretation-expectations.json', case['interpretation_expectations'])
        if 'rights_expectations' in case:
            write(folder/'rights-expectations.json', case['rights_expectations'])
        doc = Document('D1', 'Synthetic project document', 'synthetic.pdf', schema_version=6,
                       source_key=case['id'], pages=({'pdf_page': 1, 'text': case['text']},))
        traced = RetryingModel(TracingModel(RawRunRecorder(backend, folder/'raw.json'), folder/'trace'))
        print('PROJECT FACT CASE', case['id'], flush=True)
        result = collect_project_facts([doc], traced, chunk_pages=10, overlap_pages=1)
        write(folder/'result.json', result)
        summary.append({'case': case['id'], 'status': result['status'],
                        'fact_count': len(result['facts']),
                        'excluded_passage_count': len(result['excluded_passages']),
                        'coverage': [r.get('coverage') for r in result['gemini_responses']],
                        'selection_checks': selection_checks(case, result),
                        'claim_evidence_checks': claim_evidence_checks(case, result),
                        'interpretation_checks': interpretation_checks(case, result),
                        'rights_checks': rights_checks(case, result),
                        'review_findings': result['review_findings'],
                        'semantic_review_required': True})
        write(output/'execution-summary.json', summary)
    write(output/'integrity.json', {'inputs_and_code_unchanged': all(
        hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in hashes.items())})
    return summary


def rights_checks(case, result):
    """Held-out case checks on interpretations, never a production correction/filter."""
    expected = case.get('rights_expectations')
    if expected is None:
        return None
    facts = result['facts']
    text = '\n'.join(f['derived'].get('interpretation') or '' for f in facts).casefold()
    missing = [s for s in expected.get('required_fragments', []) if s.casefold() not in text]
    forbidden = [s for s in expected.get('forbidden_fragments', []) if s.casefold() in text]
    count_ok = expected['min_count'] <= len(facts) <= expected['max_count']
    return {'passed': result['status'] == 'recorded' and count_ok and not missing and not forbidden,
            'count_matches': count_ok, 'missing_fragments': missing, 'forbidden_fragments': forbidden,
            'semantic_review_required': True}


def claim_evidence_checks(case, result):
    """Check authored evidence groups per claim, not the correctness of its prose."""
    expected = case.get('claim_expectations')
    if expected is None:
        return None
    entries = catalog([{'source_key': case['id'], 'document_id': 'D1',
                        'pdf_page': 1, 'text': case['text']}])
    lines, offset = {}, 0
    for number, line in enumerate(case['text'].splitlines(keepends=True), 1):
        lines[number] = {e['evidence_id'] for e in entries
                         if offset <= e['start'] < offset + len(line)}
        offset += len(line)
    groups = [{c['evidence_id'] for c in claim['verified_citations']}
              for fact in result['facts'] for claim in fact['derived'].get('claims', [])]
    missing = [numbers for numbers in expected['evidence_groups']
               if not any(set().union(*(lines[n] for n in numbers)) <= group for group in groups)]
    return {'passed': result['status'] == 'recorded' and not missing,
            'missing_claim_evidence_groups': missing, 'semantic_review_required': True}


def interpretation_checks(case, result):
    """Held-out lexical/status controls, not a general semantic verifier or model input.

    Only claim statements count. Quoting a complete source in derived citations
    cannot mask an omitted amount or qualification in its interpretation.
    """
    expected = case.get('interpretation_expectations')
    if expected is None:
        return None
    statements = [c.get('statement', '') for f in result['facts'] for c in f['derived'].get('claims', [])]
    text = '\n'.join(s for s in statements if isinstance(s, str)).casefold()
    missing = [fragment for fragment in expected.get('required_fragments', []) if fragment.casefold() not in text]
    forbidden = [fragment for fragment in expected.get('forbidden_fragments', []) if fragment.casefold() in text]
    statuses = [f['derived'].get('status') for f in result['facts']]
    wrong_statuses = [status for status in statuses if status not in expected.get('allowed_statuses', statuses)]
    missing_uncertainty = expected.get('uncertainty_required', False) and not any(
        isinstance(f['derived'].get('uncertainty'), str) and f['derived']['uncertainty'].strip()
        for f in result['facts'])
    return {'passed': result['status'] == 'recorded' and bool(statements) and not any(
                (missing, forbidden, wrong_statuses, missing_uncertainty)),
            'missing_fragments': missing, 'forbidden_fragments': forbidden,
            'wrong_statuses': wrong_statuses, 'missing_uncertainty': missing_uncertainty,
            'semantic_review_required': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--project')
    parser.add_argument('--location', default='global')
    parser.add_argument('--max-new-calls', type=int, default=0,
                        help='Actual Gemini attempts, including retries; default 0 (offline)')
    run(**vars(parser.parse_args()))
