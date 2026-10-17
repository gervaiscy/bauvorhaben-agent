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
contain at most five new physical pages of complete OCR text. The next window
carries the active literal title, sender, represented organization, document type,
primary reference and field-specific identity evidence, plus
`last_pdf_page` and the full `last_page_text` as context. The previous page is not
a new page and must not receive another boundary decision. Identity fields are
separate from the literal title, checked against source evidence and nullable when
unknown. Interpretation concerns remain review findings.
Different titles are not required for separate documents; fresh forms for different
senders or references can start new documents even when their titles match. Each input file
starts with no previous document. Configure `BAUVO_DOCUMENT_BOUNDARY_MODEL` for
application runs or `--document-boundary-model` on the standalone CLI.

Sender and represented organization are independent roles; neither is inferred
merely from shared branding, cooperation, a contact address or body mentions.
Covering-letter and quotation transitions require a new boundary even when sender
and reference match. Repeated quotation headers, continuing item numbers and
carried totals support continuation. Document type describes the whole document,
so a contract or brochure title page is not automatically a covering letter.

Gemini is asked to support each newly assigned non-null identity field with an exact quote for that
field from the current page. Sender and represented-organization comparisons ignore
trademark markers (`®`, `™`, `℠`) and normalize whitespace; document references
retain their punctuation. Source text and evidence quotes remain unchanged.
Evidence is retained with its source page in
`documents.json`. Context contains at most one supporting quotation per known
field, while the complete previous page remains untruncated.

Validation rejects malformed responses, incorrect page coverage/order and quotations
that do not exactly occur on their source page. Interpretation concerns (identity
or title changes, missing identity support, missing boundary justification, absent
titles and within-page boundaries) produce page-specific `review_flags` instead
of retries or whole-file fallback. Findings appear on application page decisions,
affected documents and the top-level result; status becomes `needs_review`.
The next window receives the active document's flags as provisional context.
Original model responses remain untouched in `attempts`. Application coverage rules
supply a missing initial boundary and keep empty pages in the active document;
these adjustments are flagged, never treated as reviewed approval.

## Sender roles and document references

Each page response now includes `entity_roles` (`name`, `role`, exact `quote`,
`explanation`) and `references` (`value`, `kind`, exact `quote`, `explanation`).
Roles distinguish issuer, correspondence sender, declarant, signer, recipient,
cooperation partner, represented organization and contact. References distinguish
this document's identifier from related-document identifiers, dates, customer and
account numbers, and page numbers. Classification uses source labels and context;
there are no company-specific rules or date-pattern exclusions.

Application decisions select `sender` from `correspondence_sender` for letters and
covering letters, or `issuer` for other document types. `represented_organization`
requires an explicit representation classification; cooperation alone is insufficient.
`document_reference` comes only from `document_identifier`. Multiple eligible
values remain null with an ambiguity finding. When no new classification is visible,
a continuation retains established identity; a new document starts without inherited
identity. Related references and dates remain available separately in `references`.

The classifications and explanations are preserved with source page numbers in
`documents.json` and carried with the active document into the next five-page window.
A disagreement with Gemini's reported identity, insufficient source support or a
missing explanation creates a review flag. The raw model assignment is preserved
unchanged in `attempts` and the request cache. These checks do not independently
prove Gemini's role interpretation is correct. No extra model pass is added.

The boundary protocol changed; rerun the document CLI on existing schema-6
`pages.json` to obtain these fields. PDF transcription is unchanged, and old raw
boundary cache entries are retained but not reused for the new schema.

Document titles are transcriptions of the complete visible title passage, including
any printed title dates or identifiers. Only whitespace may be normalized. The
prompt explicitly excludes generated topic/purpose subtitles and asks Gemini to
check the final wording against the title passage. Attachment grouping remains
independent of this title policy.

The response schema requires `title_quote`: the exact complete title passage on
that page, or null. The application derives a new document's title from this quote,
normalizing whitespace only, and records `title_evidence` with the source page.
A generated title that disagrees with its quote is replaced automatically and
flagged for review; raw model output remains unchanged. Missing title evidence
produces an empty title and a review flag. Continuations retain the active title,
so attachment captions cannot rename an offer. Source quotations are validated
exactly; no customer-specific title replacement rules or manual output patching
are part of this workflow.

A quotation's explicitly incorporated drawing attachments remain within its source
document when their project/model/drawing references establish the connection.
Drawing captions and author names alone do not split integral attachments. Merely
sharing a project does not establish attachment membership for independent forms.
