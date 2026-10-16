"""Small authoritative page fixtures shared by offline boundary tests."""
import hashlib

def pages_fixture(lines):
    count=max((l['pdf_page'] for l in lines),default=1);pages=[]
    for number in range(1,count+1):
        text='';assigned=[]
        for l in lines:
            if l['pdf_page']!=number:continue
            start=len(text);text+=l['text']+'\n'
            assigned.append({**l,'text_segments':[[start,len(text)]],'confidence':.999})
        pages.append({'pdf_page':number,'text':text,'text_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'lines':assigned,'checkboxes':[],'min_confidence':.98})
    return {'schema_version':5,'source':'fixture.pdf','page_count':count,'selected_pages':list(range(1,count+1)),'pages':pages}
