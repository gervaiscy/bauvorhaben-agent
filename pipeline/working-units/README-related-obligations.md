# Lossless Working Unit extraction

The extractor calls Gemini for initial Working Units and offers, modality
classification, independent permission verification, and related obligations.
The application preserves every result. Reviews are linked to proposals by
occurrence ID when possible; unmatched reviews remain in the response list.

A related obligation expresses an action timed relative to another Working
Unit. It does not add a payment dependency or decide whether payment may be
withheld. If Gemini returns an obligation without a matching Working Unit,
the proposal stays in the relationship response for human review.

The tests use a fake model to verify preservation and structure. They do not
establish Gemini's recall or the legal effect of a clause.

## Review files

The CLI writes two JSON files. `--raw-output` (or the default
`<output-stem>.gemini-raw.json`) is one array of the unmodified results from
all Gemini calls, in call order. Malformed JSON text is retained as a string.
`--output` is a lossless application view with every Working Unit and offer
proposal, plus every later response in `gemini_responses`.

Each item has a unique occurrence `id`, the complete untouched `gemini`
object, and separate `derived`, `validation`, and `reviews` fields. Duplicate
proposals remain separate. Repeated citation IDs remain in `gemini`;
`derived.source_line_ids` is a unique lookup view. Missing fields and
unresolved or ambiguous references are annotations, never reasons to drop an
item. Review rows that cannot be attached to a unit remain in
`gemini_responses`.

## Event triggers and relationships

Gemini distinguishes dependencies on Working Units from `trigger_events`.
The application annotates dependency resolution without replacing Gemini's
original keys. The modality and permission verification passes append their
results beside the original proposals. They never clear the first pass's due
date, actor, tasks, or dependencies. The relationship pass likewise preserves
all proposed relationships, even when citations or target IDs need review.

Run tests with:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
