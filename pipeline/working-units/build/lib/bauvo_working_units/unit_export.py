"""Export only provider working-unit objects, without edits or deduplication."""
import argparse
import copy
import json
from pathlib import Path


def units_from_responses(responses):
    if not isinstance(responses, list):
        raise ValueError('Expected the Gemini raw-response array')
    units = []
    for response in responses:
        if not isinstance(response, dict):
            raise ValueError('Raw log contains an unfinished or invalid response; review it before exporting')
        if 'working_units' in response:
            if not isinstance(response['working_units'], list):
                raise ValueError('Invalid working_units array in provider response')
            units.extend(copy.deepcopy(response['working_units']))
    return units


def write_units(path, units):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as file:
        file.write(json.dumps({'working_units': units}, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='Saved *.gemini-raw.json response array')
    parser.add_argument('--output', required=True, help='New working-units-only JSON file')
    args = parser.parse_args()
    units = units_from_responses(json.loads(Path(args.input).read_text(encoding='utf-8')))
    write_units(args.output, units)
    print(f'Wrote {len(units)} working units to {args.output}')


if __name__ == '__main__':
    main()
