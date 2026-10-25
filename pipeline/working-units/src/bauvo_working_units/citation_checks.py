"""Source and text checks that report findings without rewriting model output."""
import re
import unicodedata


def normalized(text):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', text)).strip()


def text_findings(response):
    """Flag observable character anomalies, not guessed spelling or encoding repairs."""
    findings = []

    def visit(value, path='$'):
        if isinstance(value, str):
            suspicious = sorted({ord(c) for c in value if c == '\ufffd' or
                                 (unicodedata.category(c) == 'Cc' and c not in '\t\r\n')})
            if suspicious:
                findings.append({'code': 'suspicious_text_character', 'path': path,
                                 'codepoints': [f'U+{n:04X}' for n in suspicious]})
        elif isinstance(value, list):
            for i, item in enumerate(value):
                visit(item, f'{path}[{i}]')
        elif isinstance(value, dict):
            for key, item in value.items():
                visit(item, f'{path}.{key}')

    visit(response)
    return findings


def citation_findings(response, pages, project_pages=(), source_key=None, document_id=None):
    """Check each citation list against this document-local source window.

    Empty lists are allowed for NOT_STATED/unknown assessments, but extracted
    actions, offers and individual rules must carry source evidence.
    """
    sources = {p['pdf_page']: normalized(p['text']) for p in pages}
    qualified = {(p['source_key'], p['document_id'], p['pdf_page']): normalized(p['text']) for p in project_pages}
    if source_key is not None and document_id is not None:
        qualified.update({(source_key, document_id, p['pdf_page']): normalized(p['text']) for p in pages})
    findings = text_findings(response)

    def visit(value, path='$', required=False):
        if isinstance(value, list):
            for i, item in enumerate(value):
                visit(item, f'{path}[{i}]', required)
            return
        if not isinstance(value, dict):
            return
        required = required or (value.get('status') == 'EXPLICIT' and 'consequences' in value)
        if required or 'citations' in value:
            citations = value.get('citations')
            if not isinstance(citations, list):
                findings.append({'code': 'invalid_citations', 'path': path})
            elif not citations and (required or value.get('status') == 'EXPLICIT'):
                findings.append({'code': 'missing_citations', 'path': path})
            else:
                for i, citation in enumerate(citations):
                    location = f'{path}.citations[{i}]'
                    if not isinstance(citation, dict):
                        code = 'invalid_citation'
                    else:
                        page, quote = citation.get('pdf_page'), citation.get('quote')
                        if type(page) is not int or not isinstance(quote, str) or not quote.strip():
                            code = 'invalid_citation'
                        else:
                            if 'source_key' in citation or 'document_id' in citation:
                                key = (citation.get('source_key'), citation.get('document_id'), page)
                                # Malformed source identifiers are findings, never lookup exceptions.
                                evidence = qualified.get(key) if all(isinstance(v, str) for v in key[:2]) else None
                            else:
                                evidence = sources.get(page)
                            if evidence is None:
                                code = 'citation_page_outside_window'
                            elif normalized(quote) not in evidence:
                                code = 'citation_quote_not_found'
                            else:
                                continue
                    findings.append({'code': code, 'path': location})
        if 'modality_citations' in value:
            visit({'citations': value['modality_citations']}, path + '.modality_evidence',
                  value.get('modality') not in (None, 'UNCLEAR'))
        for key, item in value.items():
            if key not in ('citations', 'modality_citations'):
                visit(item, f'{path}.{key}', key in ('working_units', 'offers', 'project_facts', 'applicability_rules', 'timing_rules'))

    visit(response)
    return findings
