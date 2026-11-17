"""Locate quotations with formatting differences or at most two omitted source words."""
import re

PROTOCOL = 'task-quote-matching-v2'
MAX_OMITTED_WORDS = 2


def _tokens(text):
    return list(re.finditer(r'\w+|[^\w\s]', text))


def _omission_matches(quote, source):
    """Bounded subsequence search; never substitute, insert or skip punctuation/numbers.

    Both endpoints must match. Keep every candidate span so repetition never
    selects an arbitrary source location. Equivalent alignments of the same
    original span are harmless; their first deterministic alignment is retained.
    """
    matches = {}
    for start, token in enumerate(source):
        if token.group() != quote[0].group():
            continue
        states = {(start + 1, ())}
        for expected in quote[1:]:
            following = {}
            for position, omitted in sorted(states):
                for extra in range(MAX_OMITTED_WORDS - len(omitted) + 1):
                    end = position + extra
                    if end >= len(source):
                        break
                    skipped = source[position:end]
                    if any(not re.fullmatch(r'[^\W\d_]+', t.group()) for t in skipped):
                        break
                    if source[end].group() == expected.group():
                        selected = omitted + tuple(t.group() for t in skipped)
                        following.setdefault((end + 1, len(selected)), selected)
            states = {(end, omitted) for (end, _), omitted in following.items()}
            if not states:
                break
        for end, omitted in sorted(states):
            if omitted:
                matches.setdefault((source[start].start(), source[end - 1].end()), omitted)
                if len(matches) > 1:
                    return matches
    return matches


def locate(quote, text):
    """Return one source span and audit metadata, or an explicit unresolved finding."""
    # Whitespace normalization has precedence over omitted-word matching. Capture
    # overlapping occurrences and preserve original offsets, including CRLF/tabs.
    pattern = r'\s+'.join(re.escape(part) for part in quote.split())
    matches = list(re.finditer('(?=(' + pattern + '))', text))
    if len(matches) > 1:
        return None, 'task_quote_ambiguous'
    if matches:
        start, end = matches[0].span(1)
        method = 'unique_literal_quote' if text[start:end] == quote else 'unique_whitespace_quote'
        return {'start': start, 'end': end, 'method': method}, None
    # Token equality also permits whitespace around punctuation. All source
    # words, punctuation, numbers and case still have to match before omissions.
    source, wanted = _tokens(text), _tokens(quote)
    if not wanted:
        return None, 'task_quote_not_found'
    exact = []
    for start in range(len(source) - len(wanted) + 1):
        if all(source[start + k].group() == t.group() for k, t in enumerate(wanted)):
            exact.append((source[start].start(), source[start + len(wanted) - 1].end()))
            if len(exact) > 1:
                return None, 'task_quote_ambiguous'
    if exact:
        start, end = exact[0]
        return {'start': start, 'end': end, 'method': 'unique_whitespace_quote'}, None
    candidates = _omission_matches(wanted, source)
    if len(candidates) != 1:
        return None, 'task_quote_ambiguous' if candidates else 'task_quote_not_found'
    (start, end), omitted = next(iter(candidates.items()))
    return {'start': start, 'end': end, 'method': 'unique_omitted_words_quote',
            'omitted_word_count': len(omitted), 'omitted_words': list(omitted)}, None
