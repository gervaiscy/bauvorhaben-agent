"""Deterministic source ownership and links; no model interpretation or extra calls."""
import re

PROVISION_PROTOCOL = 'source-provisions-v1'
FACT_STATUSES = ('CONTRACT_RULE', 'OFFERED', 'DOCUMENTED_AGREEMENT',
                 'DOCUMENT_OBSERVATION', 'UNRESOLVED')


def provisions(pages, entries):
    """Keep numbered sections, paragraphs and table positions whole, in source order.

    These are mechanical source units, not a classification of legal meaning.
    A unit may contain several independent facts. No passage is removed or rewritten.
    """
    originals = {(p['source_key'], p['document_id'], p['pdf_page']): p['text'] for p in pages}
    units, previous, section = [], None, False
    for entry in entries:
        doc = (entry['source_key'], entry['document_id'])
        page = (*doc, entry['pdf_page'])
        text = entry['quote'].strip()
        heading = bool(re.match(r'^§\s*\d+', text))
        numbered = bool(re.match(r'^\d+[.)]\s+', text))
        table = bool(re.match(r'^\d+(?:[.)])?\s+\S', text) and ('\t' in text or '|' in text))
        new_doc = previous is None or doc != (previous['source_key'], previous['document_id'])
        new_page = not new_doc and entry['pdf_page'] != previous['pdf_page']
        gap = '' if new_doc or new_page else originals[page][previous['end']:entry['start']]
        paragraph = not new_doc and not new_page and bool(re.search(r'\n[ \t\r]*\n', previous['quote'][-2:] + gap))
        # A numbered section remains whole across paragraphs/pages. Other source
        # paragraphs end at a blank line or page boundary; table row starts win.
        start = new_doc or heading or numbered or table or ((new_page or paragraph) and not section)
        if start:
            section = heading or (numbered and not table)
            units.append({'id': f'P{len(units) + 1}', 'source_key': doc[0], 'document_id': doc[1],
                          'kind': 'SECTION' if heading else 'TABLE_ROW' if table else 'PARAGRAPH',
                          'evidence_ids': []})
        units[-1]['evidence_ids'].append(entry['evidence_id'])
        previous = entry
    return units


def batches(pages, entries, metadata, response_id, budget=50):
    """Each whole source unit has exactly one owning batch; never split to meet a budget."""
    groups, pending, size = [], [], 0
    for unit in provisions(pages, entries):
        count = len(unit['evidence_ids'])
        if pending and size + count > budget:
            groups.append(pending)
            pending, size = [], 0
        pending.append(unit)
        size += count
    if pending:
        groups.append(pending)
    prefix = response_id.rsplit(':', 1)[0]
    return [({**metadata, 'target_provision_ids': [u['id'] for u in group],
              'target_evidence_ids': [i for u in group for i in u['evidence_ids']]},
             f'{prefix}:PF{number:04d}') for number, group in enumerate(groups, 1)]


def model_provisions(units, reference_map):
    labels = {identifier: label for label, identifier in reference_map.items()}
    return [{**unit, 'evidence_ids': [labels[i] for i in unit['evidence_ids']]} for unit in units]


def resolve_links(facts, responses):
    """Resolve forward references after all batches; retain every raw fact and failure."""
    by_provision, scopes = {}, {r['id']: r for r in responses}
    for fact in facts:
        by_provision.setdefault(fact['derived'].get('provision_id'), []).append(fact['id'])
    findings = []
    for fact in facts:
        derived, linked = fact['derived'], []
        for number, identifier in enumerate(derived.get('related_provision_ids', [])):
            targets = by_provision.get(identifier, [])
            linked.extend(targets)
            if not targets:
                issue = {'code': 'unresolved_provision_reference',
                         'path': f"$.verpflichtungen[{derived['response_position']}].related_provision_ids[{number}]",
                         'provision_id': identifier}
                fact['review_findings'].append(issue)
                response = scopes[derived['response_id']]
                findings.append({'pass': 'PAGE_PROJECT_FACTS', 'response_id': response['id'],
                    **{k: response[k] for k in ('context_scope', 'sources', 'target_evidence_ids', 'target_provision_ids')},
                    **issue})
        derived['related_fact_ids'] = list(dict.fromkeys(linked))
    return findings
