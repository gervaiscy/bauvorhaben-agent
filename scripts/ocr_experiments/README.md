# Isolated OCR experiments

Google Document AI, coordinate repairs and Tesseract adapters are retained here
for benchmarks and reproducibility. Production runs do not import these modules.

## Historical Google Document AI experiment

These tools are isolated from production. Run `python scripts/ocr_experiments/experimental_extract.py --help` for their explicit CLI options. Production `.env` settings do not select this workflow.

New extraction explicitly disables Math OCR and enables character-level evidence.
Provider responses remain immutable in `ocr-cache/`. `pages.json` keeps
`provider_text`, `provider_text_sha256` and `provider_lines` unchanged; `text` and
`lines` contain the derived transcription used for document proposals and analysis.
Token/paragraph/symbol anchors and `source_text_segments` refer to `provider_text`;
line `text_segments` refer to the derived `text`. Line IDs are retained when order
or a symbol is corrected. No text is removed just because it occurs in a photo.

Coordinate repair uses whitespace-separated columns and visual baselines, without
headings or semantic sections. It retains every token, including suspected duplicates.
Missing coordinates keep the provider line order and raise a review finding.

Suspicious mathematical notation, monetary units, overlapping numeric tokens,
low-confidence numbers and unexpected scripts trigger enlarged pixel crops at
600 and 900 DPI. Corrections require both readings to agree above the review
threshold, with contractual words and numbers preserved. A repeated dimension
may be removed only when numeric tokens overlap and the two crops agree.
Disagreement, failed OCR, changed words/numbers or exhausted limits produce
`ocr_region_needs_review` in `quality-report.json`. These findings cannot be
acknowledged away with `resolve-extraction`. They require corrected source evidence.
Two agreeing OCR readings are corroboration, not independent proof of accuracy.

`corrections` records before/after text, line IDs, coordinates and the reason.
`ocr_rechecks` records crop images, both raw responses, checksums and unresolved
reasons. `source-review.html` displays this evidence. Approval fingerprints include
crop images and responses; no approval is created by a successful recheck.

By default each input file allows 24 regions, at most 3 per page, with 2 requests
per region (up to 48 extra OCR requests per input, before transport retries).
Configure `--ocr-recheck-per-page` and `--ocr-recheck-limit` on the experimental CLI.
Set the latter to `0` to repair order and flag suspicious regions without crop API
calls. Unresolved regions are retained for review. Changed OCR options invalidate
old OCR cache keys; the next fresh extraction makes new full-page OCR requests.
Existing runs and raw responses remain unchanged.

