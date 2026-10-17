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
