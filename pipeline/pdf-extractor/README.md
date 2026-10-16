# Visible-page OCR

```
PDF → rendered page PNGs → Google Document AI OCR → pages.json
                                             → optional document-boundary proposals
                                             → source review
```

`pages.json` (schema 5) is the authoritative transcription. Each physical page has
complete provider `text`, its checksum, stable OCR lines with text anchors and
coordinates, OCR words/paragraphs, checkboxes and pointers to immutable raw evidence.
Nothing is removed because it looks like a logo, footer, drawing or photo. OCR text
outside the provider's line detections receives a stable supplemental line ID,
unknown geometry and a review finding; its text is never discarded.

`documents.json` partitions those existing IDs into document proposals. It never
rewrites or classifies text. It supports within-page boundaries, distinct forms
with identical titles, and offers continuing across pages. All proposals must cover
every OCR ID exactly once in order. Responses and validation failures are retained.
Uncertain proposals, exhausted validation retries or provider failure keep the
whole bundle together for analysis. Boundary findings are reported separately from
OCR findings; unsuccessful document detection cannot invalidate completed OCR.

Use `--document-boundaries off` for an OCR-only run with no Gemini calls. The
standard local command reads `BAUVO_DOCUMENT_BOUNDARIES=auto|off` (default `auto`).

New runs produce no sections, headings, blocks, role annotations or separate line
export. Start a fresh run to migrate; historical artifacts remain unchanged.

`source-review.html` compares rendered pages with complete OCR text, lines,
checkbox crops and boundary proposals. Source approval binds pages, proposals,
quality findings, raw OCR responses and page/crop images. OCR confidence and
internal consistency are review signals, not a measured accuracy rate.

Offline tests exercise provider requests/retries, cache integrity, visible-only
rendering, exact text preservation, missing line geometry, boundary failure and
resume behavior. The working-unit suite tests three-page windows, document
separation and source-snapshot integrity. Run `make test` at the repository root.
