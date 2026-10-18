# Extraction support

The production PDF transcription files are in `../pdf-extractor/`.
`document_pipeline.py` is the independent post-transcription CLI: existing
`pages.json` in, document proposals/quality/duplicate reports/source review out.
It never invokes OCR or modifies `pages.json`. It calls deterministic review generation (`ocr_quality.py`),
optional document-boundary detection (`document_grouping.py`), its text-only
Gemini transport/cache (`grouping_client.py`), and JSON/hash helpers (`common.py`).

These helpers never rewrite the page transcription. Document boundaries operate
on existing complete page text after transcription. Boundary failures
preserve the complete file for later review. Use `BAUVO_DOCUMENT_BOUNDARIES=off`
for transcription without document-boundary requests.

`pages.json` uses schema 6 without derived lines; document proposals cite exact
page quotations and physical page numbers. Source review and approval requirements remain in
place; this cleanup does not approve any existing or future extraction.

Run `make test` at the repository root for offline tests. Older OCR providers and
repairs are isolated in `scripts/ocr_experiments/` for benchmark reproducibility.

Document boundaries default to Gemini 3.5 Flash with minimal reasoning. Requests
contain at most five new physical pages of complete OCR text. `previous_document`
contains only the active literal title and start page; `previous_page` contains the
immediately preceding physical page number and complete text, even when empty.
No sender roles, organization aliases, document types, references or explanations
are requested. Detailed metadata extraction is deferred to a separate stage; it is
not performed by this boundary command.

Each response page has `pdf_page`, `starts_new_document`, `document_title` (literal
visible title for starts, otherwise null), `evidence_quote` (one exact short local
passage or null), and `uncertain`. Application code constructs complete ordered
spans, carries titles across windows and retains leading/embedded/trailing empty
pages. Titles are matched against source wording with whitespace normalization;
missing/unsupported titles and questionable boundaries remain review flags.
Standalone covering letters and consent forms for distinct organizations remain
separate; continuing quotations and integral attachments remain together.

The output budget is 4096 tokens. Structural errors receive up to three attempts
per window. A retry sends the original instructions and source inventory plus a
concise validation error. It NEVER includes the rejected answer. Raw answers,
including malformed/repetitive replies, remain in `attempts` and fingerprinted
`grouping-cache` files. Exhausted validation or provider failure leaves the complete
file together with `document_boundaries_unverified`, retaining earlier decisions.
The boundary protocol and orchestration fingerprint changed; old request caches
cannot silently satisfy the new protocol. OCR caches and `pages.json` are untouched.

Title instructions exclude surrounding letterheads, logos, organization names and
contact details unless explicitly part of the heading. Organization names are never
removed mechanically. A source title passage spanning blank-line-separated blocks
receives `title_spans_multiple_blocks:<page>` for review. This conservative warning
keeps the literal title and raw answer intact, causes no extra model request, and
does not set boundary uncertainty. Legitimate headings may also span such blocks.
