"""Authoritative page transcription and source-bound document proposals."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

@dataclass(frozen=True)
class SourceLine:
    id: str
    page: int
    text: str

class Model(Protocol):
    def extract(self,prompt: str) -> Any: ...


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def read_pages(path):
    payload=json.loads(Path(path).read_text())
    if not isinstance(payload,dict) or payload.get('schema_version')!=5 or not isinstance(payload.get('pages'),list):
        raise ValueError('Expected page-source schema 5; start a fresh extraction')
    seen=set();numbers=[]
    for page in payload['pages']:
        n=page.get('pdf_page');text=page.get('text');lines=page.get('lines')
        if type(n) is not int or n<1 or n in numbers or not isinstance(text,str) or not isinstance(lines,list):raise ValueError('Invalid OCR page')
        numbers.append(n);covered=set()
        if hashlib.sha256(text.encode()).hexdigest()!=page.get('text_sha256'):raise ValueError('Page text checksum changed')
        for line in lines:
            if not isinstance(line,dict) or not isinstance(line.get('id'),str) or not line['id'] or line['id'] in seen or line.get('pdf_page')!=n or not isinstance(line.get('text'),str):raise ValueError('Invalid or duplicate OCR source line')
            seen.add(line['id']);segments=line.get('text_segments')
            if not isinstance(segments,list) or not segments:raise ValueError('OCR line needs exact text anchors')
            value=''
            for span in segments:
                if not isinstance(span,list) or len(span)!=2 or any(type(x) is not int for x in span):raise ValueError('Invalid OCR text anchor')
                start,end=span
                if not 0<=start<end<=len(text):raise ValueError('OCR text anchor outside page')
                positions=set(range(start,end))
                if covered & positions:raise ValueError('Overlapping OCR line anchors')
                covered.update(positions);value+=text[start:end]
            if value.rstrip('\r\n')!=line['text']:raise ValueError('OCR line differs from authoritative page text')
        if any(not c.isspace() and i not in covered for i,c in enumerate(text)):raise ValueError('Page text missing from cited OCR lines')
    if numbers!=sorted(numbers) or numbers!=payload.get('selected_pages'):raise ValueError('Page selection/order changed')
    if numbers!=list(range(1,payload.get('page_count',0)+1)):raise ValueError('Complete PDF page coverage is required for analysis')
    return payload


def read_boundaries(path,pages):
    lines=[l for p in pages['pages'] for l in p['lines']];ids=[l['id'] for l in lines]
    if path is None:
        return [{'document_id':'D0001','document_title':'','line_ids':ids,'uncertain':True}] if ids else []
    result=json.loads(Path(path).read_text())
    if result.get('schema_version')!=5 or result.get('pages_sha256')!=digest(pages['pages']):raise ValueError('Document boundaries belong to a different pages snapshot')
    docs=result.get('documents')
    if not isinstance(docs,list) or any(not isinstance(d,dict) or not isinstance(d.get('line_ids'),list) for d in docs):raise ValueError('Invalid document proposals')
    if [lid for d in docs for lid in d['line_ids']]!=ids:raise ValueError('Document boundaries must cover all OCR lines exactly once in order')
    seen=set()
    for d in docs:
        if not isinstance(d.get('document_id'),str) or d['document_id'] in seen or not d.get('line_ids') or type(d.get('uncertain')) is not bool:raise ValueError('Invalid document proposal')
        seen.add(d['document_id'])
    # A boundary marked uncertain cannot remove neighboring-page context. Use the
    # whole bundle conservatively until the proposals have been corrected.
    if any(d['uncertain'] for d in docs):
        return [{'document_id':'D0001','document_title':'','line_ids':ids,'uncertain':True}]
    return docs
