"""Project Tesseract TSV into page-source records without semantic interpretation."""
import csv
import hashlib
import io
import math

VERSION = 'tesseract-page-projection-v1'


def project_tsv(tsv, page_number, width, height, image_sha256, raw_file, threshold=.98):
    if width<=0 or height<=0:raise ValueError('Image dimensions must be positive')
    reader=csv.DictReader(io.StringIO(tsv),delimiter='\t',quoting=csv.QUOTE_NONE)
    required={'level','block_num','par_num','line_num','left','top','width','height','conf','text'}
    if not required <= set(reader.fieldnames or []):raise ValueError('Expected Tesseract TSV with word confidence')
    groups=[];keys=set();words=[]
    for row in reader:
        if row['level']!='5' or not row['text'].strip():continue
        key=tuple(row[k] for k in ('block_num','par_num','line_num'))
        if not groups or groups[-1][0]!=key:
            if key in keys:raise ValueError('Non-contiguous Tesseract line group')
            groups.append((key,[]));keys.add(key)
        x,y,w,h=(int(row[k]) for k in ('left','top','width','height'))
        if x<0 or y<0 or w<=0 or h<=0 or x+w>width or y+h>height:raise ValueError('Tesseract word outside rendered page')
        score=float(row['conf'])
        if not math.isfinite(score) or not 0<=score<=100:raise ValueError('Invalid Tesseract word confidence')
        box=[x/width*1000,y/height*1000,(x+w)/width*1000,(y+h)/height*1000]
        word={'id':f'W{page_number:04}_{len(words)+1:05}','text':row['text'],'bbox':box,
            'polygon':[[box[0],box[1]],[box[2],box[1]],[box[2],box[3]],[box[0],box[3]]],
            'confidence':score/100,'style':{},'line_key':list(key)}
        groups[-1][1].append(word);words.append(word)
    text='';lines=[]
    for index,(key,items) in enumerate(groups,1):
        start=len(text)
        for i,word in enumerate(items):
            if i:text+=' '
            a=len(text);text+=word['text'];word['text_segments']=[[a,len(text)]]
        end=len(text);value=text[start:end];text+='\n'
        box=[min(w['bbox'][0] for w in items),min(w['bbox'][1] for w in items),max(w['bbox'][2] for w in items),max(w['bbox'][3] for w in items)]
        lines.append({'id':f'L{page_number:04}_{index:05}','pdf_page':page_number,'text':value,
            'bbox':box,'polygon':[[box[0],box[1]],[box[2],box[1]],[box[2],box[3]],[box[0],box[3]]],
            'confidence':min(w['confidence'] for w in items),'text_segments':[[start,end]],
            'token_ids':[w['id'] for w in items],'paragraph_ids':[],
            'region_id':f'P{page_number:04}R{key[0]}','region_line':index,'handwritten':None,'style':{}})
    return {'ocr_schema_version':5,'pdf_page':page_number,'ocr_provider':'tesseract','text':text,
        'text_sha256':hashlib.sha256(text.encode()).hexdigest(),'image_sha256':image_sha256,
        'raw_ocr_file':raw_file,'lines':lines,'tokens':words,'paragraphs':[],'symbols':[],
        'checkboxes':[],'checkbox_detection_supported':False,'handwriting_detection_supported':False,
        'unassigned_text':'','min_confidence':threshold,
        'confidence_note':'Tesseract word scores are review hints, not calibrated probabilities or comparable to Google confidence.'}
