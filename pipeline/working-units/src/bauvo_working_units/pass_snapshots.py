"""Write each cumulative pass result once; never overwrite an earlier snapshot."""
import json
from pathlib import Path

PASS_NAMES = {
    'PAGE_PROJECT_FACTS': '00-project-facts.json',
    'PAGE_EXTRACTION': '01-aufgaben.json',
    'PAGE_OFFERS': '02-offers.json',
    'PAGE_TIMING': '03-timing.json',
    'PAGE_CONSEQUENCES': '04-consequences.json',
    'PAGE_APPLICABILITY': '05-applicability.json',
}


class PassSnapshots:
    def __init__(self, directory, facts_output):
        self.directory = Path(directory)
        self.facts_output = Path(facts_output)
        self.directory.mkdir(parents=True, exist_ok=False)

    def __call__(self, pass_name, result):
        payload = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
        with (self.directory / PASS_NAMES[pass_name]).open('x', encoding='utf-8') as file:
            file.write(payload)
        if pass_name == 'PAGE_PROJECT_FACTS':
            with self.facts_output.open('x', encoding='utf-8') as file:
                file.write(payload)
