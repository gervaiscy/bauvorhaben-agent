"""Verify source identity before any model review; never guess by line number."""
from __future__ import annotations

import re
import unicodedata
import copy


def normalize(text):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(text))).strip()


def align(unit, lines):
    derived = unit.get('derived') or {}
    model = unit.get('gemini') or {}
    original = list(dict.fromkeys(derived.get('source_line_ids') or model.get('evidence_line_ids') or []))
    excerpt = normalize(derived.get('source_excerpt') or '')
    if not excerpt:
        return {'status': 'UNRESOLVED', 'line_ids': [], 'original_line_ids': original,
                'reason': 'No extraction-time excerpt to verify line identity'}
    # Match the complete excerpt, not a fuzzy fragment. A partial-line quote is OK.
    selected = normalize(' '.join(str(lines[i].get('text', '')) for i in original if i in lines))
    if original and all(i in lines for i in original) and excerpt in selected:
        return {'status': 'VERIFIED', 'line_ids': original, 'original_line_ids': original}
    # Search the immutable transcription, preserving document and page identities.
    ids = list(lines)
    texts = [normalize(lines[i].get('text', '')) for i in ids]
    joined = ' '.join(texts)
    starts, position = [], 0
    for text in texts:
        starts.append(position)
        position += len(text) + 1
    matches = []
    offset = joined.find(excerpt)
    while offset >= 0:
        end = offset + len(excerpt)
        hit = [i for i, start in enumerate(starts) if start < end and start + len(texts[i]) > offset]
        if hit:
            matches.append([ids[i] for i in hit])
        offset = joined.find(excerpt, offset + 1)
    if len(matches) == 1:
        return {'status': 'REALIGNED', 'line_ids': matches[0], 'original_line_ids': original,
                'reason': 'Unique exact excerpt match in current transcription'}
    return {'status': 'UNRESOLVED', 'line_ids': [], 'original_line_ids': original,
            'reason': 'Excerpt missing or ambiguous in current transcription', 'matches': matches}


def verify_citations(citations, registry):
    """Require exact source-backed quotes, not merely existing line IDs."""
    if not isinstance(citations, list) or not citations:
        raise ValueError('Missing supporting citations')
    for citation in citations:
        if not isinstance(citation, dict):
            raise ValueError('Malformed citation')
        source = registry.get(citation.get('source_key'), {})
        ids = citation.get('line_ids')
        quote = normalize(citation.get('quote', ''))
        if not isinstance(ids, list) or not ids or not quote or not all(isinstance(i, str) and i in source for i in ids):
            raise ValueError(f'Unknown source lines or empty quote: {citation!r}. '
                f'Allowed source keys: {sorted(registry)}. '
                'Use only source_registry keys and line IDs with a nonempty verbatim quote. '
                'An offer title, inferred house type, or explanation is not project-scope proof. '
                'If the provided sources do not establish optional scope, keep it UNKNOWN.')
        text = normalize(' '.join(str(source[i].get('text', '')) for i in ids))
        if quote not in text:
            raise ValueError(f'Citation quote does not match its source lines: '
                f'source={citation.get("source_key")}, line_ids={ids}, submitted_quote={quote!r}, '
                f'exact_source_text={text!r}. Use a contiguous verbatim substring, '
                'or separate citations for non-contiguous clauses; do not paraphrase inside quote.')


def realign_citations(value, registry):
    """Repair only unique exact quote locations within the cited document; retain raw input."""
    result, repairs = copy.deepcopy(value), []
    def visit(node, path):
        if isinstance(node, list):
            for i, child in enumerate(node): visit(child, path+[i])
        elif isinstance(node, dict):
            if {'source_key','line_ids','quote'} <= node.keys():
                source = registry.get(node['source_key'])
                if source and isinstance(node['line_ids'],list) and isinstance(node['quote'],str):
                    try: verify_citations([node],registry)
                    except (ValueError,TypeError):
                        match=align({'derived':{'source_line_ids':node['line_ids'],
                                               'source_excerpt':node['quote']}},source)
                        if match['status']=='REALIGNED':
                            before=copy.deepcopy(node)
                            node['line_ids']=match['line_ids']
                            verify_citations([node],registry)
                            repairs.append({'path':path,'before':before,'after':copy.deepcopy(node),
                                'basis':'Unique exact quotation match in the same source document'})
            else:
                for key, child in node.items(): visit(child,path+[key])
    visit(result,[])
    return result, repairs
