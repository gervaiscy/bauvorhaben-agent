"""Common page traversal and unchanged proposal recording for occurrence passes."""
import copy
from ..project_flow import source_pages


def run(ctx, phase):
    pass_name, key, kind = phase.PASS, phase.KEY, phase.KIND
    target = getattr(ctx, phase.TARGET)
    for doc in ctx.documents:
        carry = ''
        for chunk in ctx.chunks[doc.id]:
            if phase.TRACK_WINDOWS:
                ctx.windows.append({'source_key': doc.source_key, 'document_id': doc.id,
                                'pages': list(chunk.pages), 'primary_pages': list(chunk.primary_pages)})
            if not any(p['text'].strip() for p in source_pages(doc, chunk) if p['primary']):
                continue
            known = [{'id': item['id'], 'name': item['gemini'].get('name')}
                     for item in target if item['derived']['document_id']==doc.id and isinstance(item['gemini'], dict)]
            extra = {'known_items': known, **phase.request_context(ctx, doc, chunk, carry)}
            rid, response = ctx.request(pass_name, doc, chunk, extra)
            carry = phase.carry_forward(ctx, doc, chunk, response)
            if response is None:
                continue
            rows = response.get(key) if isinstance(response, dict) else None
            if not isinstance(rows, list):
                ctx.findings.append({'response_id': rid, 'code': 'missing_or_invalid_' + key})
                continue
            for position, item in enumerate(rows):
                target.append({'id': f'{rid}:{kind}:{position:03d}', 'gemini': copy.deepcopy(item),
                    'derived': {**ctx.metadata(doc, chunk), 'response_id': rid, 'response_position': position}, 'reviews': []})
                if not phase.valid_proposal(item):
                    ctx.findings.append({'response_id': rid, 'row': position, 'code': 'invalid_' + kind})
