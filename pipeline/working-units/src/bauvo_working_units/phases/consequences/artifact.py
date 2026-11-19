"""Readable tasks with one consequence assessment; lossless audit kept separately."""
import copy
import json
from pathlib import Path
from ..timing.artifact import review_view as timing_view, validation_view, output_paths


def review_view(result):
    view = timing_view(result)
    for row, unit in zip(view['working_units'], result['working_units']):
        reviews = [r['gemini'] for r in unit['reviews'] if r['kind'] == 'PAGE_CONSEQUENCES']
        if len(reviews) > 1:
            raise ValueError('Multiple consequence assessments for task ' + unit['id'])
        row['consequences'] = copy.deepcopy(reviews[0]) if reviews else None
    return view


def write_outputs(output, result):
    review, audit, validation = output_paths(output)
    if any(p.exists() for p in (review, audit, validation)):
        raise FileExistsError('Consequence outputs already exist')
    review.parent.mkdir(parents=True, exist_ok=True)
    for path, value in ((audit, result), (validation, validation_view(result)), (review, review_view(result))):
        with path.open('x', encoding='utf-8') as file:
            file.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    return audit


def load_artifact(path):
    path = Path(path)
    result = json.loads(path.read_text())
    if set(result) != {'status', 'working_units'}:
        return result
    if not any('consequences' in u for u in result['working_units']):
        from ..timing.artifact import load_artifact as load_timing
        return load_timing(path)
    audit = output_paths(path)[1]
    full = json.loads(audit.read_text())
    if review_view(full) != result:
        raise ValueError('Consequence review does not match its audit: ' + str(audit))
    return full
