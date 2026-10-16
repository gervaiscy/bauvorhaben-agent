"""Portable, image-only OCR boundary. Provider output is immutable source evidence."""
import base64
import hashlib
import json
import math
import re
import time
from pathlib import Path

DEFAULT_PROCESSOR = 'projects/222393655295/locations/eu/processors/6a00c6691fb66daa/processorVersions/pretrained-ocr-v2.1-2024-08-07'
VERSION = 'document-ai-image-ocr-v1'
OPTIONS = {'ocrConfig': {'enableNativePdfParsing': False, 'hints': {'languageHints': ['de', 'en']},
                        'premiumFeatures': {'enableSelectionMarkDetection': True, 'computeStyleInfo': True}}}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def save(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def endpoint(processor):
    match = re.fullmatch(r'projects/([A-Za-z0-9_-]+)/locations/([a-z0-9-]+)/processors/([A-Za-z0-9_-]+)/processorVersions/([A-Za-z0-9._-]+)', processor)
    if not match or match[4] in ('stable', 'rc', 'pretrained', 'latest'):
        raise ValueError('OCR requires a full processorVersions resource with an explicit pinned version')
    return f'https://{match[2]}-documentai.googleapis.com/v1/{processor}:process'


class GoogleDocumentOCR:
    """REST + ADC works on macOS, Linux and cloud runtimes; no native OCR dependency."""
    def __init__(self, processor=DEFAULT_PROCESSOR, session=None):
        self.processor = processor
        self.url = endpoint(processor)
        self._session = session

    def process(self, image):
        if self._session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession
            credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
            self._session = AuthorizedSession(credentials)
        body = {'rawDocument': {'mimeType': 'image/png', 'content': base64.b64encode(image).decode()}, 'processOptions': OPTIONS}
        for attempt in range(4):
            response = self._session.post(self.url, json=body, timeout=180)
            if response.ok:
                return response.json()
            if response.status_code not in (429, 500, 502, 503, 504) or attempt == 3:
                detail = 'Grant the runtime service account roles/documentai.apiUser in the processor project.' if response.status_code == 403 else 'Check processor, API, credentials and quota.'
                raise RuntimeError(f'Document AI HTTP {response.status_code}. {detail}')
            time.sleep(2 ** attempt)
        raise AssertionError('unreachable')


def cached_ocr(provider, image, folder, reuse_folders=()):
    """Cache depends on pixels and OCR version/options, never on the grouping model."""
    request = {'image_sha256': hashlib.sha256(image).hexdigest(), 'processor': provider.processor, 'options': OPTIONS, 'version': VERSION}
    key = digest(request)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (key + '.json')
    candidate=next((directory/(key+'.json') for directory in [folder,*map(Path,reuse_folders)] if (directory/(key+'.json')).is_file()),None)
    if candidate is not None:
        stored = json.loads(candidate.read_text())
        if stored.get('request') != request or stored.get('response_sha256') != digest(stored.get('response')):
            raise ValueError('OCR checkpoint modified')
        if candidate!=path:
            path.write_bytes(candidate.read_bytes())
        print('OCR response: cache reused',flush=True)
        return stored['response'], path
    print('OCR request: provider call',flush=True)
    response = provider.process(image)
    # Preserve even invalid provider output before validation; never conceal failed evidence.
    save(path, {'request': request, 'response': response, 'response_sha256': digest(response)})
    return response, path


def segments(layout, text):
    spans = layout.get('textAnchor', {}).get('textSegments', [])
    result = []
    for span in spans:
        start, end = int(span.get('startIndex', 0)), int(span['endIndex'])
        if not 0 <= start < end <= len(text):
            raise ValueError('OCR text anchor outside provider text')
        if result and start < result[-1][1]:
            raise ValueError('OCR text anchors overlap or are unordered')
        result.append((start, end))
    return result


def geometry(layout, dimension):
    poly = layout.get('boundingPoly', {})
    vertices = poly.get('normalizedVertices')
    if vertices:
        points = [[float(p.get('x', 0)), float(p.get('y', 0))] for p in vertices]
    else:
        width, height = float(dimension.get('width', 0)), float(dimension.get('height', 0))
        if not width > 0 or not height > 0:
            raise ValueError('OCR page dimensions missing')
        points = [[float(p.get('x', 0))/width, float(p.get('y', 0))/height] for p in poly.get('vertices', [])]
    if len(points) < 3 or any(not math.isfinite(v) or not 0 <= v <= 1 for p in points for v in p):
        raise ValueError('Invalid OCR polygon')
    polygon = [[x*1000, y*1000] for x, y in points]
    xs, ys = zip(*polygon)
    bbox = [min(xs), min(ys), max(xs), max(ys)]
    if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        raise ValueError('Empty OCR polygon')
    return bbox, polygon


def confidence(layout):
    value = layout.get('confidence')
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1):
        raise ValueError('Invalid OCR confidence')
    return value


def normalize(response, page_number, image_sha256, raw_file, min_confidence=.98):
    """Lossless text-anchor projection, with IDs stable for this physical page/OCR result."""
    document = response.get('document', {})
    pages = document.get('pages', [])
    text = document.get('text', '')
    if len(pages) != 1 or not isinstance(text, str):
        raise ValueError('Expected exactly one OCR page for one rendered image')
    page = pages[0]
    dimension = page.get('dimension', {})
    tokens = page.get('tokens', [])
    words=[];paragraphs=[]
    for field,prefix,target in (('tokens','W',words),('paragraphs','P',paragraphs)):
        for index,item in enumerate(page.get(field,[]),1):
            layout=item.get('layout',{});anchors=segments(layout,text)
            bbox,polygon=geometry(layout,dimension)
            target.append({'id':f'{prefix}{page_number:04}_{index:05}',
                'text':''.join(text[a:b] for a,b in anchors).rstrip('\r\n'),
                'bbox':bbox,'polygon':polygon,'confidence':confidence(layout),
                'text_segments':[list(s) for s in anchors],'style':item.get('styleInfo',{})})
    lines, covered = [], set()
    for index, item in enumerate(page.get('lines', []), 1):
        layout = item.get('layout', {})
        anchors = segments(layout, text)
        value = ''.join(text[start:end] for start, end in anchors).rstrip('\r\n')
        if not value.strip():
            raise ValueError('OCR emitted an empty text line')
        bbox, polygon = geometry(layout, dimension)
        offsets = {i for start, end in anchors for i in range(start, end)}
        if covered & offsets:
            raise ValueError('Overlapping OCR line anchors')
        covered.update(offsets)
        region = None
        for n, block in enumerate(page.get('blocks', []), 1):
            block_offsets = segments(block.get('layout', {}), text)
            if anchors and all(any(a <= start and end <= b for a, b in block_offsets) for start, end in anchors):
                region = f'P{page_number:04}R{n:04}'
                break
        styles = []
        for token in tokens:
            ts = segments(token.get('layout', {}), text)
            if any(start < b and a < end for start, end in ts for a, b in anchors):
                styles.append(token.get('styleInfo', {}))
        handwriting = [s['handwritten'] for s in styles if 'handwritten' in s]
        lines.append({'id': f'L{page_number:04}_{index:05}', 'pdf_page': page_number, 'text': value,
                      'bbox': bbox, 'polygon': polygon, 'confidence': confidence(layout),
                      'region_id': region, 'region_line': index, 'handwritten': any(handwriting) if handwriting else None,
                      'text_segments': [list(s) for s in anchors],
                      'token_ids':[w['id'] for w in words if any(a<d and c<b for a,b in anchors for c,d in w['text_segments'])],
                      'paragraph_ids':[p['id'] for p in paragraphs if any(a<d and c<b for a,b in anchors for c,d in p['text_segments'])], 'style': {'bold': any(s.get('bold',False) for s in styles), 'italic': any(s.get('italic',False) for s in styles)}})
    # A provider response must not silently lose characters outside line anchors.
    unassigned = ''.join(char for index, char in enumerate(text) if index not in covered and not char.isspace())
    residual=[]
    start=None
    for offset in range(len(text)+1):
        missing=offset<len(text) and offset not in covered
        if missing and start is None:start=offset
        if not missing and start is not None:
            value=text[start:offset].rstrip('\r\n')
            if value.strip():
                residual.append({'id':f'L{page_number:04}_U{len(residual)+1:05}',
                    'pdf_page':page_number,'text':value,'text_segments':[[start,start+len(value)]],
                    'bbox':None,'polygon':None,'confidence':None,'geometry_missing':True,
                    'region_id':None,'region_line':None,'handwritten':None,'token_ids':[], 'paragraph_ids':[], 'style':{}})
            start=None
    if residual:lines=sorted(lines+residual,key=lambda l:l['text_segments'][0][0])
    marks = []
    for index, item in enumerate(page.get('visualElements', []), 1):
        typ = item.get('type', '')
        if 'checkbox' not in typ:
            continue
        layout = item.get('layout', {})
        bbox, polygon = geometry(layout, dimension)
        score = confidence(layout)
        detected = {'filled_checkbox': 'checked', 'unfilled_checkbox': 'unchecked'}.get(typ, 'uncertain')
        marks.append({'id': f'P{page_number:04}C{index:04}', 'state': detected if score is not None and score >= min_confidence else 'uncertain',
                      'detected_state': detected, 'bbox': bbox, 'polygon': polygon, 'confidence': score, 'source': 'google-document-ai'})
    return {'ocr_schema_version': 5, 'text':text, 'text_sha256':hashlib.sha256(text.encode()).hexdigest(), 'pdf_page': page_number, 'ocr_provider': 'google-document-ai',
            'image_sha256': image_sha256, 'raw_ocr_file': raw_file, 'lines': lines, 'checkboxes': marks,
            'unassigned_text': unassigned, 'min_confidence': min_confidence, 'tokens':words, 'paragraphs':paragraphs}
