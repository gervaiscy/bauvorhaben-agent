import copy
import json
import unittest
from types import SimpleNamespace
from legacy_document_grouping import group_documents, validate_page_answer, VERSION

ARGS=SimpleNamespace(document_boundaries='auto',project='p',location='global',model='m')

def page(n,empty=False):
    # Deliberately nonnumeric stored order reproduces the actual floor-plan failure.
    lines=[] if empty else [{'id':f'L{n:04}_00015','text':'Document title','pdf_page':n},
        {'id':f'L{n:04}_00010','text':'Continuation','pdf_page':n},
        {'id':f'L{n:04}_00011','text':'Last visible line','pdf_page':n}]
    return {'pdf_page':n,'text':'\n'.join(l['text'] for l in lines),'lines':lines}

def decision(p,start=False,title='Contract',uncertain=False,within=False):
    return {'pdf_page':p['pdf_page'],'starts_new_document':start,'document_title':title,
        'evidence_line_ids':[p['lines'][0]['id']] if start else [],
        'reason':'Fresh standalone form title and organization' if start else 'Continuation',
        'uncertain':uncertain,'boundary_within_page':within}

def execute(pages,model,args=ARGS):
    return group_documents(args,pages,[l for p in pages for l in p['lines']],model)

def payload(parts):return json.loads(parts[0]['text'].split('SOURCE DATA:\n')[1])

