"""Run project-fact controls only; expectations stay outside provider requests."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from bauvo_working_units.gemini import GeminiModel
from bauvo_working_units.project_facts import collect_project_facts
from bauvo_working_units.project_flow import Document
from bauvo_working_units.schemas import GENERATION, PROTOCOL, PHASE_THINKING_LEVELS
from bauvo_working_units.trace import RawRunRecorder, RetryingModel, TracingModel

BASE = Path(__file__).resolve().parent


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def run(output, project, location='global', model=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    files = [Path(__file__), BASE/'project-facts-cases.json',
             *(BASE.parent/'src').rglob('*.py'), *(BASE.parent/'src').rglob('*.txt')]
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    write(output/'run-manifest.json', {'protocol': PROTOCOL, 'model': 'gemini-3.5-flash',
          'generation': GENERATION, 'phase_thinking_levels': PHASE_THINKING_LEVELS, 'chunk_pages': 3, 'overlap_pages': 1, 'inputs_and_code': hashes})
    backend = model if model is not None else GeminiModel(project, location, 'gemini-3.5-flash')
    summary = []
    for case in json.loads((BASE/'project-facts-cases.json').read_text())['cases']:
        folder = output/case['id']
        folder.mkdir()
        write(folder/'expectations.json', case['expectations'])
        doc = Document('D1', 'Synthetic project document', 'synthetic.pdf', schema_version=6,
                       source_key=case['id'], pages=({'pdf_page': 1, 'text': case['text']},))
        traced = RetryingModel(TracingModel(RawRunRecorder(backend, folder/'raw.json'), folder/'trace'))
        print('PROJECT FACT CASE', case['id'], flush=True)
        result = collect_project_facts([doc], traced, chunk_pages=3, overlap_pages=1)
        write(folder/'result.json', result)
        summary.append({'case': case['id'], 'status': result['status'],
                        'fact_count': len(result['facts']), 'review_findings': result['review_findings'],
                        'semantic_review_required': True})
        write(output/'execution-summary.json', summary)
    write(output/'integrity.json', {'inputs_and_code_unchanged': all(
        hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in hashes.items())})
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--location', default='global')
    run(**vars(parser.parse_args()))
