# Bauvo Agent

Local, reviewable construction-document workflow:

```
PDF → rendered pages → Google Document AI OCR → pages.json
    → optional document-boundary proposals → source review
    → overlapping windows of up to 3 pages → Gemini working units
    → reconciliation, review, timing and draft report
```

## Setup

```bash
make setup
cp .env.example .env
```

Configure Google ADC credentials, the project and the pinned Document AI OCR
processor in `.env`. Never commit credentials, `.env`, source PDFs or generated runs.
The runtime service account needs Document AI API User in the processor project
and permission to call Vertex AI for Gemini. `make doctor` checks local setup;
`make auth-check` makes a minimal paid Gemini request.

`BAUVO_MODEL` selects the Gemini model for document proposals and later analysis.
It does not select the OCR engine. `BAUVO_DOCUMENT_BOUNDARIES=auto` enables document
proposals; set it to `off` for OCR without any Gemini calls. OCR configuration uses
`BAUVO_OCR_PROCESSOR` (an explicit processorVersions resource) and
`BAUVO_OCR_MIN_CONFIDENCE` (default 0.98, a review threshold, not an accuracy score).

## Run locally

```bash
make new CASE=sample CONTRACT="/absolute/path/contract.pdf" BLB="/absolute/path/blb.pdf" SIGNED=YYYY-MM-DD
make fresh-extract CASE=sample
make open-extraction CASE=sample SOURCE=contract
make open-extraction CASE=sample SOURCE=blb
```

For an existing case, skip `make new`. Case configuration is in `cases/<name>.json`.
`SIGNED` is the user's confirmed planning baseline. Extraction stops before working
units. New runs preserve previous outputs and reuse exact matching OCR caches;
changing Gemini grouping does not repeat paid OCR for unchanged page pixels,
processor version and options.

## Source files

- `pages.json`: the source of truth. Contains every physical page's complete OCR
  `text`, checksum, stable line IDs, exact text anchors, coordinates, confidence,
  words/paragraphs and separate checkbox states with source evidence.
- `documents.json`: optional document-boundary proposals over existing OCR IDs,
  bound to the exact page snapshot. Includes raw responses and failed attempts.
- `quality-report.json`: OCR findings, separate boundary findings and review state.
- `source-review.html`, `page-images/`, `ocr-cache/`: visual review and preserved
  raw OCR evidence. Duplicate physical pages remain present.

No section grouping, heading extraction, semantic block grouping or furniture
filtering runs. New extraction emits no `sections.json`, `blocks.json`,
`annotations.json` or `lines.json`. Headings visible in the source remain ordinary
OCR text. Supplemental lines preserve provider text missing from its line detector,
with unknown coordinates and an explicit review finding.

Document boundaries are useful but remain proposals: distinct forms can share a
title, and an offer can continue across pages. Boundaries must cover every source
ID in order without changing text. If any proposal is uncertain, analysis keeps the
bundle together to avoid losing context across a guessed split. Boundary detection
failure also falls back to the whole file; OCR remains available for review.

## Review and analysis

Compare every rendered page against the complete text, numbers, negations,
checkboxes and document proposals. Low OCR confidence, genuinely blank pages and
missing line geometry can be acknowledged after named source review. Wrong text,
missing pages and uncertain checkbox states need correction, not acknowledgment.

```bash
make resolve-extraction CASE=sample SOURCE=blb PAGE=1 FINDING=low_ocr_confidence REVIEWER="your name" NOTE="specific completed source comparison" CONFIRM=reviewed
make approve-extraction CASE=sample SOURCE=contract REVIEWER="your name" NOTE="completed source review" CONFIRM=reviewed
make approve-extraction CASE=sample SOURCE=blb REVIEWER="your name" NOTE="completed source review" CONFIRM=reviewed
make run CASE=sample
```

Approval is bound to source evidence; changed artifacts invalidate it. No review is
automatically fabricated. The HTML is the initial snapshot; review the updated
quality report after resolving findings. This workflow does not certify 99% OCR
accuracy; that requires independently reviewed reference transcriptions.

Working-unit extraction and its page-based review/relationship passes use at most
three total pages per window, including overlap: 1–3, 3–5, 5–7, etc. Confirmed
proposals keep separate documents apart; uncertain proposals use the complete
bundle. Exact page text and stable line IDs come from `pages.json`. Long pages are
never silently truncated. Later consolidation combines cited results and relevant
context across documents, because contractual dependencies may be far apart.

The standalone working-unit CLI accepts `--pages /path/pages.json` and optionally
`--documents /path/documents.json`, alongside project/model/output arguments.
There are no section or line-file arguments.

Use `make status CASE=sample` for checkpoints and `make open CASE=sample` for the
draft report. Resume uses `make run`; code/input changes require a fresh run.
`make regroup CASE=sample SOURCE=contract` archives boundary proposals and dependent
unit output before recomputation; it preserves OCR and prior evidence.

## Tests

```bash
make test
```

Tests are offline and make no paid API calls. They cover OCR transport, rendering,
text preservation, cache and snapshot tampering, boundary failure, overlapping
page windows, source citations, review gates, reconciliation and date calculations.
# bauvorhaben-agent
