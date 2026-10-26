"""Shared assessment traversal, routing checks and raw-response recording."""
import copy
from ..page_flow import _view, assessment_findings


def run(ctx, phase):
    for doc in ctx.documents:
        grouped = {chunk.pages: [] for chunk in ctx.chunks[doc.id]}
        for unit in ctx.units:
            if unit['derived']['document_id'] != doc.id or not isinstance(unit['gemini'], dict) or not isinstance(unit['gemini'].get('action'), str):
                continue
            original = next(c for c in ctx.chunks[doc.id] if list(c.pages) == unit['derived']['window_pages'])
            selected, reason = phase.select_window(unit, original, ctx.chunks[doc.id])
            field = phase.WINDOW_FIELD
            unit['derived'][field] = list(selected.pages)
            grouped[selected.pages].append(unit)
            if reason and phase.TRACK_CONTEXT:
                ctx.findings.append({'unit_id': unit['id'], 'code': 'assessment_context_unresolved', 'reason': reason})
        for chunk in ctx.chunks[doc.id]:
            candidates = grouped[chunk.pages]
            for pass_name in (phase.PASS,):
                batches = phase.batches(candidates)
                for batch in batches:
                    views = [_view(unit) for unit in batch]
                    extra = phase.request_data(ctx, doc, views)
                    rid, response = ctx.request(pass_name, doc, chunk, extra)
                    if response is None:
                        ctx.save_batch()
                        continue
                    issues = assessment_findings(response, [u['id'] for u in batch], pass_name)
                    ctx.findings.extend({'response_id': rid, **issue} for issue in issues)
                    rows = response.get('assessments') if isinstance(response, dict) else None
                    if not isinstance(rows, list):
                        continue
                    targets = {u['id']: u for u in batch}
                    for position, row in enumerate(rows):
                        target = targets.get(row.get('unit_id')) if isinstance(row, dict) and isinstance(row.get('unit_id'), str) else None
                        if target is not None:
                            derived = ctx.metadata(doc, chunk)
                            derived.update(phase.review_data(row, doc, ctx.anchors))
                            target['reviews'].append({'id': f'{rid}:review:{position:03d}', 'response_id': rid,
                                'kind': pass_name, 'response_position': position,
                                'gemini': copy.deepcopy(row), 'derived': derived})
                    ctx.save_batch()
