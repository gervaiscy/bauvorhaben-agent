"""Prepare synthetic schema-6 inputs; no model calls or output corrections."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from bauvo_working_units.core import digest
from bauvo_working_units.project_flow import load_documents


def prepare(output,cases_path=None):
    output=Path(output)
    cases=json.loads(Path(cases_path or Path(__file__).with_name('cases.json')).read_text())['cases']
    # Refuse reuse before writing anything: predictions and prior review stay intact.
    if output.exists():raise ValueError('Evaluation directory already exists; use a new output directory')
    output.mkdir(parents=True)
    for case in cases:
        folder=output/case['id'];folder.mkdir()
        pages=[{**p,'text_sha256':hashlib.sha256(p['text'].encode()).hexdigest(),'checkboxes':[]} for p in case['pages']]
        source={'schema_version':6,'source':f'synthetic/{case["id"]}.pdf',
                'sha256':digest(case['pages']),'page_count':len(pages),'pages':pages}
        documents={'schema_version':6,'pages_sha256':digest(pages),'status':'proposed',
                   'documents':[{**d,'uncertain':False} for d in case['documents']],
                   'page_assignments':[{'pdf_page':p,'document_id':d['document_id']} for d in case['documents'] for p in d['pdf_pages']]}
        for name,data in (('pages.json',source),('documents.json',documents),('expectations.json',case)):
            (folder/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
        load_documents(folder/'pages.json',folder/'documents.json')
    return len(cases)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    count=prepare(args.output)
    print(f'Prepared {count} synthetic evaluation cases in {args.output}; no model calls made')


if __name__=='__main__':main()
