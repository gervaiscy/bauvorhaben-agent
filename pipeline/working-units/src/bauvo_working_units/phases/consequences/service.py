"""Assess timed tasks in batches of ten against the full BLB and compact facts."""
import copy
from ...core import digest
from ...trace import ModelRequestFailed
from ...page_flow import _prompt
from .payload import model_payload, task_rows
from .schema import BATCH_SIZE
from .validation import response_findings

PASS = 'PAGE_CONSEQUENCES'
PROTOCOL = 'timed-task-consequences-v1'


def run(ctx):
    # Preflight the entire stage before any provider request. Never silently skip
    # a task whose timing is missing or ambiguous.
    try:
        rows = task_rows(ctx.units, ctx.project_facts)
    except ValueError as exc:
        ctx.findings.append({'code': 'consequence_input_invalid', 'pass': PASS, 'reason': str(exc)})
        return
    sources = [d for d in [*ctx.documents, *ctx.context_documents] if d.source_type == 'blb']
    jobs = []
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start+BATCH_SIZE]
        bindings = {f'U{i+1}': row['id'] for i, row in enumerate(batch)}
        data = model_payload({'working_units': {key: {k: v for k, v in row.items() if k != 'id'}
                              for key, row in zip(bindings, batch)},
                              'documents': sources, 'project_facts': ctx.project_facts})
        jobs.append((bindings, data, _prompt(PASS, data, ctx.language)))
    ctx.plan_requests([prompt for _, _, prompt in jobs])
    targets = {u['id']: u for u in ctx.units}
    for bindings, data, prompt in jobs:
        rid = f'consequences:R{len(ctx.responses)+1:04d}'
        record = {'id': rid, 'pass': PASS, 'protocol': PROTOCOL, 'bindings': bindings,
                  'prompt_sha256': digest(prompt), 'input_sha256': digest(data)}
        try:
            response = ctx.model.extract(prompt)
        except ModelRequestFailed as exc:
            failure = {'code': 'model_request_failed', 'pass': PASS, 'response_id': rid,
                       'unit_ids': list(bindings.values()), 'attempts': exc.attempts}
            ctx.findings.append(failure)
            ctx.responses.append({**record, 'gemini': None, 'status': 'request_failed', 'failure': failure})
            ctx.save_batch()
            continue
        ctx.responses.append({**record, 'gemini': copy.deepcopy(response)})
        issues = response_findings(response, bindings)
        ctx.findings.extend({'pass': PASS, 'response_id': rid, **issue} for issue in issues)
        if not issues:
            for label, uid in bindings.items():
                targets[uid]['reviews'].append({'id': f'{rid}:{label}', 'kind': PASS,
                    'response_id': rid, 'response_label': label, 'gemini': copy.deepcopy(response[label]),
                    'derived': {'protocol': PROTOCOL, 'context_scope': data['source_scope'],
                                'citation_validation': 'not_performed'}})
        ctx.save_batch()
