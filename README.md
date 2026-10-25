# Bauvo Agent

For the current modular PDF-to-applicability workflow, see [phases and sequential pipeline](pipeline/working-units/PHASES.md).

Local, reviewable construction-document workflow:

```
PDF (including scanned PDF) → one 300-DPI PNG per page
    → Gemini 3.5 Flash / minimal → pages.json
    → optional document-boundary proposals → source review
    → three-page windows with one-page overlap → five Gemini passes
    → reconciliation, review, timing and draft report
```

## Setup

```bash
make setup
cp .env.example .env
```

Configure Google ADC credentials and the project in `.env`.
Never commit credentials, `.env`, source PDFs or generated runs.
The runtime service account needs permission to call Gemini through Vertex AI.
`make doctor` checks local setup;
`make auth-check` makes a minimal paid Gemini request.

`BAUVO_OCR_PROVIDER=gemini` and `BAUVO_OCR_MODEL=gemini-3.5-flash` are the defaults,
including when those new variables are absent from an existing `.env`. Transcription
always uses minimal reasoning. Each request contains only a fixed literal-transcription
prompt and one rendered PNG. Native/hidden PDF text, neighboring pages, previous OCR
and project interpretation are not sent.

`BAUVO_DOCUMENT_BOUNDARY_MODEL` independently selects the boundary model, defaulting
to `gemini-3.5-flash` with minimal reasoning. `BAUVO_MODEL` selects later analysis
models; existing settings for it do not override document-boundary detection.
`BAUVO_DOCUMENT_BOUNDARIES=auto` enables separate document proposals in windows of
at most five new physical pages, plus the previous page as continuation context;
set it to `off` for transcription only. Production
extraction uses Gemini. Google Document AI and Tesseract remain isolated experiments
under `scripts/ocr_experiments/`; they are not loaded by the production extractor.
Gemini does not provide measured OCR confidence scores.

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
changing document grouping does not repeat transcription for unchanged page pixels,
OCR model, prompt and generation settings. Use `make fresh-extract` after this cleanup; existing outputs and review approvals
are preserved, and matching Gemini transcription caches remain reusable.

## Source files

- `pages.json`: the source of truth. Contains every physical page's complete OCR
  `text`, checksum, `image_file`, image checksum, raw-response reference. There is no `lines` array in schema 6. With Gemini, coordinates/confidence are explicitly
  unavailable (`null` / availability flags). Inline checkbox marks are also recorded
  with text offsets and unknown coordinates; they are model transcriptions, not
  independently measured detections. No coordinates, confidence or handwriting
  classifications are invented.
- `page-images.json`: ordered image inventory for the PDF, with physical page
  numbers, PNG paths and checksums. All selected pages are rendered before the
  first transcription request, so the source images remain available after an API failure.
- `documents.json`: optional page-boundary proposals, bound to the exact page
  snapshot. Gemini decides which pages start a document and cites exact quotations from page text;
  application code assigns every physical page to complete spans in stored
  order. Includes page decisions/assignments, raw responses and failed attempts.
  Requests contain at most five new pages, the active literal title/start page,
  and the full previous-page text/page number as context. The response contains
  boundary decisions, literal titles and short source quotations only; detailed
  identity metadata is not extracted. Context pages receive no new decision.
  The SDK enforces a JSON response schema; local checks enforce exact page coverage
  and valid evidence. Empty pages are retained without invented text.
  Ambiguous or within-page boundaries require review; failed windows keep the whole
  input together. This workflow supports page boundaries, not automatic mid-page splits.
- `quality-report.json`: OCR findings, separate boundary findings and review state.
- `source-review.html`, `page-images/`, `ocr-cache/`: visual review and preserved
  raw OCR evidence. Duplicate physical pages remain present.

### Gemini visible-text transcription (default)

Pages are processed in physical PDF order and retained even when the answer is empty.
`page-images/page-0001.png`, etc. contain the rendered visible CropBox at 300 DPI.
The model transcribes text only: it does not group sections, classify clauses or
extract working units. Application code preserves the exact
returned text; it does not rewrite words, rearrange clauses or apply coordinate repairs.

Raw SDK responses and input/configuration fingerprints are saved in `ocr-cache/`.
Normally finished responses can be reused exactly. Incomplete responses remain as
immutable attempt evidence and stop the run; a subsequent explicit resume can retry
that page without repeating completed requests. There are no hidden SDK transport
retries. Empty responses, `[illegible]`, uncertain checkboxes and possible model
descriptions such as `[logo]` require source review. Suspected descriptions remain in
the text; no errors are silently filtered. Successful extraction does not approve
the source or certify 99% accuracy.

For transcription without the separate document-boundary requests:

```bash
BAUVO_DOCUMENT_BOUNDARIES=off make fresh-extract CASE=sample
```

No section grouping, heading extraction, semantic block grouping or furniture
filtering runs. New extraction emits no `sections.json`, `blocks.json`,
`annotations.json` or `lines.json`. Headings visible in the source remain ordinary
OCR text. Complete page text is preserved without constructing line records.

Document boundaries are useful but remain proposals: distinct forms can share a
title, and an offer can continue across pages. Boundaries must cover every physical page in order without changing text. If any proposal is uncertain, analysis keeps the
bundle together to avoid losing context across a guessed split. Boundary detection
failure also falls back to the whole file; OCR remains available for review.

## Review and analysis

Compare every rendered page against the complete text, numbers, negations,
checkboxes and document proposals. Pages with no visible text legitimately have empty
transcriptions and do not trigger warnings solely for being empty. Source images
and raw responses remain available for review. Wrong text,
missing pages and uncertain checkbox states need correction, not acknowledgment.
For Gemini, `uncertain_transcription` and `possible_nonliteral_transcription` are
also source-review diagnostics. A reviewer may acknowledge genuine illegibility
or a false alarm after inspecting the image; this never repairs wrong text or
turns unknown values into verified values.

```bash
make resolve-extraction CASE=sample SOURCE=blb PAGE=1 FINDING=uncertain_transcription REVIEWER="your name" NOTE="specific completed source comparison" CONFIRM=reviewed
make approve-extraction CASE=sample SOURCE=contract REVIEWER="your name" NOTE="completed source review" CONFIRM=reviewed
make approve-extraction CASE=sample SOURCE=blb REVIEWER="your name" NOTE="completed source review" CONFIRM=reviewed
make run CASE=sample
```

Approval is bound to source evidence; changed artifacts invalidate it. No review is
automatically fabricated. The HTML is the initial snapshot; review the updated
quality report after resolving findings. This workflow does not certify 99% OCR
accuracy; that requires independently reviewed reference transcriptions.

Schema-6 working-unit extraction uses five focused passes (actions/roles,
applicability, offers, structured timing, consequences) in three-page windows with one-page overlap:
1–3, 3–5, 5–7, etc. Windows stay within confirmed document boundaries;
unverified or uncertain boundaries stop analysis. Page-and-quote citations are checked against the source window and flagged without rewriting or repair. Five focused passes
replace semantic correction and relationship discovery. Named deadline events remain on unit reviews.
Long source pages are never silently truncated.

The standalone CLI accepts `--pages /path/pages.json` and
`--documents /path/documents.json` alongside project/model/output arguments.
There are no section or line-file arguments. The legacy schema-5 path remains
available for old evidence. See the focused workflow and small evaluation set in
[pipeline/working-units/README-related-obligations.md](pipeline/working-units/README-related-obligations.md).

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

## Isolated OCR benchmark

`scripts/benchmark_ocr.py` compares existing 300-DPI OCR, current Document AI
options at 300 and directly rendered 600 DPI, coordinate repairs, and local
Tesseract at both resolutions. It also tests a directly rendered 600-DPI reference
crop with each engine. It does not change case/run pointers, extraction artifacts,
production OCR selection or source approvals.

Install Tesseract separately (`brew install tesseract` on macOS, or your Linux
package manager). Supply official German and English `.traineddata` files in a
local directory. The benchmark records engine versions and model checksums; model
files are not committed. References and document-specific outputs belong under
ignored `imports/` and `runs/`, because they contain private source text.

```bash
.venv/bin/python scripts/benchmark_ocr.py \
  --manifest imports/ocr-benchmark/reference.json \
  --output runs/ocr-benchmark \
  --tessdata /absolute/path/to/tessdata \
  --max-google-calls 30
```

A reference manifest specifies `source_pdf`, `source_sha256`, `baseline_pages`,
`baseline_sha256`, `reference_method`, and `references`. Each reference has an
`id`, physical `pdf_page`, normalized visible-page `bbox` (`[x0,y0,x1,y1]`, 0–1000),
manual `expected` transcription and a list of `critical` phrases/values. An
optional `ambiguity` note excludes that region from the separately reported
unambiguous critical-check count. Freeze references before comparing outputs.
Do not generate expected answers from the OCR being evaluated.

The benchmark saves raw responses/TSV, images, hashes, request reservations,
latencies and HTML/JSON reports. Resuming the same output reuses matching caches;
changed document/configuration bindings require a new output directory. Google
request reservations survive interruptions and enforce the experiment-wide cap.
Transport retries are bounded by the provider separately. Costs are estimates,
not billing measurements; Tesseract compute/hosting cost is not estimated.

Character and word errors are measured on the fixed regions, with separate
strict and display-equivalent scores. Equivalent units/notation can match, but
wrong currency, negations, numbers and reading order still count as errors.
API failures are penalized as empty responses and reported separately. Crop
results and full-page results have different context; Tesseract also uses different
segmentation modes for each. A small diagnostic set is not a whole-document or
99% accuracy claim; validate any candidate on held-out documents before switching.

### Full-document Tesseract trial

Use the separate runner to extract every visible page locally at 600 DPI:

```bash
.venv/bin/python scripts/run_tesseract.py \
  --input data/sample/contract.pdf \
  --output runs/tesseract-trial/contract \
  --tessdata /absolute/path/to/tessdata
```

Repeat with the BLB input and a separate output directory. Matching cached
Tesseract requests can be reused with `--reuse-cache /path/to/tesseract-cache`.
The run records source/model/image fingerprints, original TSV, words, line IDs,
coordinates, confidence hints, `pages.json`, and `source-review.html`. Review uses
small lazy-loading previews linked to the original 600-DPI images. Resuming the
same command reuses OCR; a changed source/configuration requires another output.

This is an experimental extraction, with no cloud calls, working-unit analysis,
case pointer update, source approval or change to the configured default OCR engine.
Tesseract does not establish checkbox states or classify handwriting here, so the
quality report explicitly flags those capabilities as unsupported. Empty
checkbox results must never be interpreted as unchecked boxes or absent marks.

## Extraction code

`pipeline/pdf-extractor/` contains only two source files:

- `ocr_pipeline.py`: renders page images, orchestrates transcription and writes outputs.
- `gemini_ocr.py`: fixed visible-text prompt, Gemini 3.5 Flash minimal request,
  response validation, caching and schema-6 page normalization without derived lines.

Document-boundary proposals, review generation and shared JSON helpers live in
`pipeline/extraction_support/`. Offline extraction tests live in
`tests/pdf_extractor/`. The optional directory/GCS batch wrapper is
`scripts/extract_pdfs.py`. All dependencies are installed from `requirements.lock`.

The PDF folder is self-contained and does not import the document-processing
module. It produces `pages.json`, `page-images.json`, rendered PNGs and immutable
OCR cache evidence only. Its CLI has no boundary model, grouping or review options.

Run PDF -> pages.json independently from the repository root:

```bash
.venv/bin/python pipeline/pdf-extractor/ocr_pipeline.py \
  --input /absolute/path/contract.pdf --output /absolute/path/extraction \
  --project YOUR_GOOGLE_CLOUD_PROJECT
```

Then run the separate document-processing module when needed:

```bash
.venv/bin/python pipeline/extraction_support/document_pipeline.py \
  --input /absolute/path/extraction/pages.json \
  --project YOUR_GOOGLE_CLOUD_PROJECT
```

Both CLIs use the existing Google ADC credentials. The PDF CLI defaults to Gemini
3.5 Flash with minimal reasoning. Document processing carries only the active
title/start page and complete previous-page text between five-page windows.
Its response has five fields per page: page number, new-document decision, visible
title (null on continuations), a short exact evidence quote, and uncertainty.
Raw replies remain in `attempts` and request-bound caches. Structural retries send
only the original source and concise validation error, never the rejected reply.
Interpretation concerns remain review flags. The response budget is 4096 tokens.
The separate `--document-boundary-model` option defaults to Gemini 3.5 Flash;
this command never renders pages, invokes OCR or rewrites `pages.json`.

`make fresh-extract CASE=sample` and the batch wrapper continue to run both modules
in order, preserving document proposals, duplicate reports, quality findings and
source-review output. `BAUVO_DOCUMENT_BOUNDARIES=off` disables boundary requests in
the second module. Changes to boundary settings are absent from the PDF checkpoint
binding and do not require new transcription requests.

### Schema 6 extraction output

New `pages.json` files contain complete page `text` without a derived `lines`
array. `documents.json` references physical `pdf_pages` and exact
`evidence_quotes`; quality and source review operate on pages directly. Start
with `make fresh-extract CASE=sample`. Prior runs are preserved; exact matching
Gemini transcription caches can be reused without new transcription requests.

Working-unit extraction now consumes schema 6 with exact page-and-quote citations.
It uses three total pages, including one overlap, within each source document.
`BAUVO_WORKING_UNIT_MODEL` defaults to Gemini 3.5 Flash with minimal reasoning,
independently of `BAUVO_MODEL`. Output schema 17 records raw proposals and assessments with page-and-quote review flags.
Assessments use batches of up to five units. Applicability runs last, assesses modality,
and receives the complete cited project-facts collection. A separate preparatory pass
reads supplied non-BLB sources for agreements and obligations of both parties; it replaces
word-overlap retrieval. It returns `verpflichtung` and page/text `zitate` without
classification or acceptance status. Facts and raw responses
are saved separately for review. Cumulative files after each pass are written to
`<output-stem>.passes/`; earlier files are never overwritten. Mark standalone BLB
input with `--source-type blb` (the case pipeline does this automatically).
Use `--project-facts /path/working-units.project-facts.json` to reuse a saved
collection (with its original sources supplied as context), and `--stop-after
aufgaben` to write only the next task stage. `--stop-after project-facts` runs
only fact collection. Earlier artifacts are preserved; reused facts make no new
model requests.
The status `recorded` does not mean the response has been validated.
The later line-based reconciliation/timing/report stages still need a separate
page-citation migration. Extraction review/approval accepts schemas 5 and 6.
See `pipeline/working-units/README-related-obligations.md` for the standalone command.

Boundary protocol v33 deliberately omits detailed sender, representation, alias,
type and reference classification. `metadata_extraction` is `not_performed` in
`documents.json`; missing identity fields must not be treated as verified unknowns.
Detailed metadata extraction within detected documents is a separate future stage.
Titles use visible source wording, with whitespace normalization and exact source
passages stored in `title_evidence`. Covering letters remain separate from enclosed
quotations; continuing quotations and integral attachments stay together. Standalone
consent forms for distinct organizations remain separate despite matching titles.
