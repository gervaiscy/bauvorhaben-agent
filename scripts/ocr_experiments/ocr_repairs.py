"""Conservative geometry repair and bounded, pixel-only OCR rechecks.

Raw provider text/lines remain available. Derived anchors refer to the new page
text; source_text_segments always refer to provider_text. No semantic grouping.
"""
import copy
import hashlib
import re
import unicodedata
from statistics import median
from pathlib import Path

from document_ocr import cached_ocr, normalize

VERSION = 'coordinate-order-crop-consensus-v1'


def bounds(items):
    return [min(i['bbox'][0] for i in items), min(i['bbox'][1] for i in items),
            max(i['bbox'][2] for i in items), max(i['bbox'][3] for i in items)]


def gap(items, axis):
    ordered = sorted(items, key=lambda i: i['bbox'][axis])
    end = ordered[0]['bbox'][axis+2]
    best = None
    for index, item in enumerate(ordered[1:], 1):
        distance = item['bbox'][axis]-end
        if best is None or distance > best[0]:
            best = (distance, ordered[:index], ordered[index:])
        end = max(end, item['bbox'][axis+2])
    return best


def baseline_rows(items, height):
    rows = []
    for item in sorted(items, key=lambda i: ((i['bbox'][1]+i['bbox'][3])/2, i['bbox'][0])):
        box = item['bbox']; center = (box[1]+box[3])/2
        if rows and abs(center-rows[-1][0]) <= height*.4:
            rows[-1][1].append(item)
        else:
            rows.append((center, [item]))
    return [sorted(row, key=lambda i:i['bbox'][0]) for _,row in rows]


def visual_order(items):
    """Whitespace XY cut, then baseline ordering; numeric tables stay row-major."""
    if len(items) < 2:
        return list(items)
    height = median(i['bbox'][3]-i['bbox'][1] for i in items)
    rows = baseline_rows(items,height)
    numeric_rows = [r for r in rows if len(r)>=3 and re.fullmatch(r'\d+(?:[.,]\d+)?',r[0]['text'].strip())]
    if len(numeric_rows)>=3 and max(r[0]['bbox'][0] for r in numeric_rows)-min(r[0]['bbox'][0] for r in numeric_rows)<height:
        return [item for row in rows for item in row]
    vertical = gap(items, 0)
    if vertical and vertical[0] > max(18, height*1.5):
        left, right = vertical[1:]
        a, b = bounds(left), bounds(right)
        overlap = min(a[3], b[3])-max(a[1], b[1])
        # Two nontrivial overlapping columns; isolated marks are row fragments.
        if len(left) >= 2 and len(right) >= 2 and overlap > height*2:
            return visual_order(left)+visual_order(right)
    horizontal = gap(items, 1)
    if horizontal and horizontal[0] > max(2, height*.65):
        return visual_order(horizontal[1])+visual_order(horizontal[2])
    return [item for row in rows for item in row]


def rebuild(page):
    chunks = []; offset = 0
    for index, line in enumerate(page['lines']):
        value = line['text']
        line['text_segments'] = [[offset, offset+len(value)]]
        following = page['lines'][index+1] if index+1 < len(page['lines']) else None
        a,b = line.get('bbox'), following.get('bbox') if following else None
        same_row = a and b and b[0] >= a[2]-2 and abs((a[1]+a[3]-b[1]-b[3])/2) <= min(a[3]-a[1],b[3]-b[1])*.4
        separator = ' ' if same_row else '\n'
        chunks.append(value+separator); offset += len(value)+1
    page['text'] = ''.join(chunks)
    page['text_sha256'] = hashlib.sha256(page['text'].encode()).hexdigest()


def repair_order(original):
    page = copy.deepcopy(original)
    page['provider_text'] = original['text']
    page['provider_text_sha256'] = original['text_sha256']
    page['provider_lines'] = copy.deepcopy(original['lines'])
    page['repair_version'] = VERSION
    page['corrections'] = []
    page['ocr_rechecks'] = []
    page['repair_findings'] = []
    for line in page['lines']:
        line['source_text_segments'] = copy.deepcopy(line['text_segments'])
        words = [w for w in page['tokens'] if w['id'] in line.get('token_ids', [])]
        # Reorder only a complete, nonoverlapping anchor projection of a line.
        spans = [tuple(s) for w in words for s in w['text_segments']]
        offsets = [i for a,b in spans for i in range(a,b)]
        covered = set(offsets)
        required = {i for a,b in line['source_text_segments'] for i in range(a,b)
                    if not page['provider_text'][i].isspace()}
        if not words or len(offsets) != len(covered) or not required <= covered:
            continue
        line_offsets = {j for a,b in line['source_text_segments'] for j in range(a,b)}
        if not covered <= line_offsets:
            continue
        ordered = visual_order(words)
        if [w['id'] for w in ordered] != [w['id'] for w in words]:
            # Preserve every token and its literal characters; change only order/spacing.
            pieces = []
            for index, word in enumerate(ordered):
                value = re.sub(r'\s+', ' ', word['text'])
                if index and pieces and not pieces[-1][-1:].isspace():
                    previous = ordered[index-1]['bbox']; box = word['bbox']
                    if box[0]-previous[2] > min(box[3]-box[1],previous[3]-previous[1])*.2:
                        pieces.append(' ')
                pieces.append(value)
            value = ''.join(pieces).strip()
            if value:
                page['corrections'].append({'kind':'token_order','line_ids':[line['id']],
                    'before':line['text'],'after':value,'bbox':line['bbox'],
                    'token_ids':[w['id'] for w in ordered],'basis':'OCR token coordinates'})
                line['text'] = value
    if all(l.get('bbox') for l in page['lines']):
        ordered = visual_order(page['lines'])
        before = [l['id'] for l in page['lines']]; after = [l['id'] for l in ordered]
        if before != after:
            page['corrections'].append({'kind':'line_order','before':before,'after':after,
                'basis':'Whitespace-separated columns and OCR baseline coordinates'})
            page['lines'] = ordered
    else:
        page['repair_findings'].append({'code':'reading_order_needs_review',
            'reason':'Missing line geometry: provider line order retained'})
    rebuild(page)
    return page


def suspicious(page):
    """Prioritize monetary units, dimensions and non-Latin noise; no global replacements."""
    candidates = []
    for line in page['lines']:
        text = line['text']
        reasons = []
        if re.search(r'\d\s*(?:\\in|~?\s*E)\s*/',text):
            reasons.append('currency')
        if re.search(r'\\(?!(?:times|circ))[A-Za-z]+|\d\s*~?\s*E\s*/|\d\s*[|]\s*(?:Speicher|Liter|Volumen)', text):
            reasons.append('math_or_unit')
        if re.search(r'[\u0400-\u04ff\u3400-\u9fff]', text):
            reasons.append('unexpected_script')
        words = [w for w in page['tokens'] if w['id'] in line.get('token_ids', [])]
        ordered = sorted(words, key=lambda w:w['bbox'][0])
        if any(min(a['bbox'][2],b['bbox'][2])-max(a['bbox'][0],b['bbox'][0]) > 2
               and re.search(r'\d',a['text']+b['text']) for a,b in zip(ordered,ordered[1:])):
            reasons.append('overlapping_numeric_tokens')
        if re.search(r'\d',text) and line.get('confidence') is not None and line['confidence'] < .9:
            reasons.append('low_confidence_number')
        if reasons:
            candidates.append({'line_id':line['id'],'bbox':line['bbox'],'reasons':reasons})
    return sorted(candidates,key=lambda c:('currency' not in c['reasons'],
        'overlapping_numeric_tokens' not in c['reasons'], 'math_or_unit' not in c['reasons']))


def canonical(text):
    """Equivalent display notation, used only to validate crop corrections."""
    text = unicodedata.normalize("NFKC",text)
    text = text.replace('\\times','x').replace('\\circ','').replace('\\in','€')
    text = re.sub(r'(?<=\d)\s*~?\s*E(?=\s*/)', '€', text)
    text = text.replace('~',' ').replace('{','').replace('}','').replace('^','')
    # A lone bar after a capacity may be corrected to l only by crop agreement.
    text = re.sub(r'(?<=\d)\s*\|', ' l', text)
    return text


def lexical(text):
    return re.findall(r'[^\W\d_]+',canonical(text).lower())


def numbers(text):
    return re.findall(r'\d+(?:[.,]\d+)?|[.,]\d+',canonical(text))


def safe_consensus(before, after, reasons):
    if lexical(before) != lexical(after):
        return False
    # Quantities alone cannot protect comparisons, signs, percentages or currency.
    operators = r'[<>≤≥±%+−=§/€£$-]'
    if re.findall(operators,canonical(before)) != re.findall(operators,canonical(after)):
        return False
    a, b = numbers(before), numbers(after)
    if a == b:
        return True
    # Repeated dimension may be removed only with overlapping-number evidence.
    if 'overlapping_numeric_tokens' in reasons:
        return any(a[:i]+a[i+1:]==b and i>0 and a[i]==a[i-1] for i in range(len(a)))
    return False


def recheck(page, raster, provider, out, reuse=(), limit=3, budget=None):
    """At most two calls per region; apply only high-confidence symbol consensus.

    budget is a mutable per-file region allowance. Unchecked/failed/disagreeing
    regions remain review findings. Crops come from rendered pixels, never PDF text.
    """
    candidates = suspicious(page)
    line_map = {l['id']:l for l in page['lines']}
    for index, candidate in enumerate(candidates):
        record = dict(candidate, status='needs_review', attempts=[])
        page['ocr_rechecks'].append(record)
        line = line_map[candidate['line_id']]
        if index >= limit or (budget is not None and budget[0] <= 0) or not candidate['bbox']:
            record['reason'] = 'Crop recheck budget exhausted or geometry unavailable'
            continue
        if budget is not None: budget[0] -= 1
        box = candidate['bbox']; rect = raster.rect
        import pymupdf
        clip = pymupdf.Rect(max(0,box[0]-5)*rect.width/1000,max(0,box[1]-4)*rect.height/1000,
            min(1000,box[2]+5)*rect.width/1000,min(1000,box[3]+4)*rect.height/1000)
        try:
            for dpi in (600,900):
                pixels = raster.get_pixmap(clip=clip,dpi=dpi,alpha=False).tobytes('png')
                name = Path('page-images')/f'{line["id"]}-recheck-{dpi}.png'
                (out/name).write_bytes(pixels)
                raw, path = cached_ocr(provider,pixels,out/'ocr-cache',reuse)
                attempt = {'dpi':dpi,'image':str(name),'image_sha256':hashlib.sha256(pixels).hexdigest(),
                    'raw_ocr_file':str(path.relative_to(out)),'raw_ocr_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
                record['attempts'].append(attempt)
                result = normalize(raw,page['pdf_page'],hashlib.sha256(pixels).hexdigest(),str(path.relative_to(out)))
                value = ' '.join(result['text'].split())
                scores = [w['confidence'] for w in result['tokens']] or [l['confidence'] for l in result['lines']]
                score = min((s if s is not None else 0 for s in scores),default=0)
                attempt.update(text=value,min_confidence=score)
            a,b = record['attempts']
            if a['text'] != b['text'] or min(a['min_confidence'],b['min_confidence']) < page['min_confidence']:
                record['reason'] = 'Crop readings disagree or confidence is below review threshold'
            elif not a['text'] or suspicious(result) or not safe_consensus(line['text'],a['text'],candidate['reasons']):
                record['reason'] = 'Crop result changes words/numbers or remains suspicious; source review required'
            else:
                record['status'] = 'confirmed'
                if line['text'] != a['text']:
                    page['corrections'].append({'kind':'crop_consensus','line_ids':[line['id']],
                        'bbox':box,'before':line['text'],'after':a['text'],
                        'basis':'Two enlarged OCR readings agree; words and numbers preserved except evidenced duplicate',
                        'recheck_index':len(page['ocr_rechecks'])-1})
                    line['text'] = a['text']
        except Exception as exc:
            if isinstance(exc,ValueError) and 'checkpoint modified' in str(exc):
                raise
            record['reason'] = 'Crop OCR failed: '+type(exc).__name__
    rebuild(page)
    return page
