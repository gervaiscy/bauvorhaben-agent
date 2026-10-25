"""Conservative calendar arithmetic; no model output rewriting or inferred schedules."""
import copy
from datetime import date, timedelta
import re
from ...citation_checks import normalized


def source_dates(text):
    """Only explicit numeric dates; no publication date/event reinterpretation."""
    dates = set()
    for pattern, order in ((r'\b(\d{4})-(\d{2})-(\d{2})\b', (0, 1, 2)),
                           (r'\b(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\b', (2, 1, 0))):
        for match in re.finditer(pattern, text):
            parts = match.groups()
            try:
                dates.add(date(*(int(parts[i]) for i in order)).isoformat())
            except ValueError:
                pass
    return dates


def valid_citations(citations, document):
    pages = {p['pdf_page']: normalized(p['text']) for p in document.pages}
    return bool(citations) and isinstance(citations, list) and all(
        isinstance(c, dict) and type(c.get('pdf_page')) is int and isinstance(c.get('quote'), str)
        and bool(c['quote'].strip()) and normalized(c['quote']) in pages.get(c['pdf_page'], '')
        for c in citations)


def validate_anchors(anchors, documents):
    """User-provided event dates must be explicit in cited source, not just asserted."""
    if not isinstance(anchors, list):
        raise ValueError('Date anchors must be a JSON array')
    docs = {d.id: d for d in documents}
    seen = set()
    for anchor in anchors:
        if not isinstance(anchor, dict) or not all(isinstance(anchor.get(k), str) and anchor[k].strip()
                for k in ('id', 'document_id', 'event', 'date')):
            raise ValueError('Date anchors require id, document_id, event, date and citations')
        doc = docs.get(anchor['document_id'])
        if not doc or anchor['id'] in seen or not valid_citations(anchor.get('citations'), doc):
            raise ValueError('Date anchor has duplicate ID or unsupported source citations')
        if not any(anchor['date'] in source_dates(c['quote']) and normalized(anchor['event']).casefold() in normalized(c['quote']).casefold() for c in anchor['citations']):
            raise ValueError('Date anchor event and date must occur in its cited source')
        seen.add(anchor['id'])
    return copy.deepcopy(anchors)


NUMBERS = {'ein': 1, 'eine': 1, 'einen': 1, 'einem': 1, 'eins': 1, 'zwei': 2, 'drei': 3,
           'vier': 4, 'fünf': 5, 'sechs': 6, 'sieben': 7, 'acht': 8, 'neun': 9,
           'zehn': 10, 'elf': 11, 'zwölf': 12}
UNITS = {'DAYS': r'Tag(?:e|en)?', 'WEEKS': r'Woche(?:n)?',
         'MONTHS': r'Monat(?:e|en)?', 'YEARS': r'Jahr(?:e|en)?'}


def supported_offset(rule, calculation):
    amount, unit = calculation.get('offset_amount'), calculation.get('offset_unit')
    if type(amount) is not int or amount < 0 or unit not in UNITS:
        return False
    expression = rule.get('offset')
    if not isinstance(expression, str):
        return False
    quote = ' '.join(c['quote'] for c in rule['citations'])
    # Approximate periods and working days need an explicit policy, not exact calendar dates.
    if re.search(r'\b(ca|circa|etwa|ungefähr|rund|mindestens|maximal|Werktag\w*|Arbeitstag\w*)\b', expression, re.I):
        return False
    number = r'(\d+|' + '|'.join(NUMBERS) + ')'
    match = re.fullmatch(number + r'\s+' + UNITS[unit] + r'(?:\s+(?:vorher|danach))?', expression.strip(), re.I)
    if not match:
        return False
    token = match.group(1).lower()
    if (int(token) if token.isdigit() else NUMBERS[token]) != amount:
        return False
    period = match.group(0).removesuffix(' vorher').removesuffix(' danach')
    if not re.search(r'(?<!\w)' + re.escape(normalized(period)) + r'(?!\w)', normalized(quote), re.I):
        return False
    if re.search(r'\b(ca\.?|circa|etwa|ungefähr|rund|mindestens|maximal)\s*' + re.escape(period), normalized(quote), re.I):
        return False
    return True


def shifted(anchor, amount, unit):
    if unit in ('DAYS', 'WEEKS'):
        return anchor + timedelta(days=amount * (7 if unit == 'WEEKS' else 1))
    months = anchor.year * 12 + anchor.month - 1 + amount * (12 if unit == 'YEARS' else 1)
    year, month = divmod(months, 12)
    # Do not silently clamp 31st/29th to a different day under an unstated month-end policy.
    return date(year, month + 1, anchor.day)


def calculate_dates(assessment, document, anchors):
    results = []
    rules = assessment.get('timing_rules', [])
    if not isinstance(rules, list):
        return results
    for index, rule in enumerate(rules):
        result = {'rule_index': index, 'status': 'UNRESOLVED', 'date': None}
        results.append(result)
        if not isinstance(rule, dict) or assessment.get('status') != 'EXPLICIT' or not valid_citations(rule.get('citations'), document):
            result['reason'] = 'Uncertain rule or unsupported citations'
            continue
        if rule.get('kind') not in ('DEADLINE', 'EVENT_RELATIVE') or rule.get('duration') is not None:
            result['reason'] = 'Duration/prerequisite does not establish a calendar deadline'
            continue
        quote = '\n'.join(c['quote'] for c in rule['citations'])
        fixed = rule.get('date')
        if fixed:
            if isinstance(fixed, str) and fixed in source_dates(quote) and rule.get('offset') is None and not rule.get('anchor_events'):
                result.update(status='SOURCE_DATE_CONDITIONAL' if rule.get('conditions') else 'SOURCE_DATE',
                              date=fixed, conditions=copy.deepcopy(rule.get('conditions', [])),
                              citations=copy.deepcopy(rule['citations']))
            else:
                result['reason'] = 'Calendar date is not explicit or fields conflict'
            continue
        calc = rule.get('calculation')
        anchor = next((a for a in anchors if isinstance(calc, dict) and a['id'] == calc.get('anchor_id')
                       and a['document_id'] == document.id), None)
        events = rule.get('anchor_events')
        if anchor is None and calc is None and rule.get('relation') in ('BY', 'ON') and rule.get('offset') is None:
            matching = [a for a in anchors if a['document_id'] == document.id and events == [a['event']]]
            if len(matching) == 1:
                anchor = matching[0]
        if not anchor or events != [anchor['event']]:
            result['reason'] = 'No supported date for the exact referenced event'
            continue
        if rule.get('relation') in ('BY', 'ON') and rule.get('offset') is None:
            if calc is not None and (calc.get('offset_amount') != 0 or calc.get('offset_unit') != 'DAYS'):
                result['reason'] = 'Offset contradicts an unshifted anchor date'
                continue
            result.update(status='CALCULATED_CONDITIONAL' if rule.get('conditions') else 'CALCULATED',
                          date=anchor['date'], anchor=copy.deepcopy(anchor),
                          calculation={'relation': rule['relation'], 'method': 'same_event_date'},
                          conditions=copy.deepcopy(rule.get('conditions', [])), citations=copy.deepcopy(rule['citations']))
            continue
        if rule.get('relation') not in ('BEFORE', 'AFTER', 'WITHIN_AFTER') or not supported_offset(rule, calc):
            result['reason'] = 'Unsupported calendar relation or exact offset'
            continue
        try:
            amount = calc['offset_amount'] * (-1 if rule['relation'] == 'BEFORE' else 1)
            value = shifted(date.fromisoformat(anchor['date']), amount, calc['offset_unit'])
        except (ValueError, OverflowError):
            result['reason'] = 'Calendar boundary needs an explicit policy'
            continue
        result.update(status='CALCULATED_CONDITIONAL' if rule.get('conditions') else 'CALCULATED',
                      date=value.isoformat(), anchor=copy.deepcopy(anchor),
                      calculation={'relation': rule['relation'], 'amount': calc['offset_amount'], 'unit': calc['offset_unit']},
                      conditions=copy.deepcopy(rule.get('conditions', [])), citations=copy.deepcopy(rule['citations']))
    return results
