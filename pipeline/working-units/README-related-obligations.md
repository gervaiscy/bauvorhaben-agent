> Current project-facts runs use the German homeowner-rights prompt and schema 15
> (`contractual-rights-v2`): `ansprueche: [{aussage, belege}]`.
> Full context remains in every batch; only citation labels are selected by Gemini.
> See [current extraction and replay](REPLAY.md#current-extraction-homeowner-contractual-rights).
> Schema-11 descriptions below document the historical broad reference format.

# Page-based working units

See [offline replay and call limits](REPLAY.md) before running model calls.

Schema-6 workflow, protocol `page-working-units-v58`, output schema 18:

```
all supplied non-BLB project sources → cited homeowner contractual rights
main pages.json + documents.json
  → Aufgaben and responsible/executing parties
  → optional offers and their conditions
  → structured timing + supported calendar calculations
  → explicitly stated consequences
  → applicability, conditions and modality with the complete project-facts collection
  → working-units.json + raw responses + traces
```

Each task source window has at most ten pages, including one overlapping page, and
never crosses a document boundary. Only primary pages introduce new action
occurrences. The three assessment passes use batches of at most five units, each
with the same proposed action, responsible party and performer. Each unit selects
the existing document-local window containing all its cited pages with the most
following-page context. Invalid or incompatible cited pages keep the original window
and add an unresolved-context finding. Source text is never truncated. Assessments are never fed into another assessment pass.

Gemini 3.5 Flash with minimal reasoning produces all interpretations. Prompts and
JSON schemas retain scoped conditions, exceptions, alternatives, relative triggers
and durations. No calendar dates or sequences are calculated by application code.
Modality (required/recommended/optional/permission/unclear) is part of action extraction.
Action extraction excludes missing information, consequences and relationship statements.
Negatively worded duties and timing bounds are retained by meaning; there are no
negation keyword filters or output rewrites. Denials of dependencies remain context.
Continuing timing/exception text alone does not create another occurrence of an overlap task.

Timing distinguishes relative rules, prerequisites and process durations in the JSON schema.
Duration rules carry no event, relation, offset or date; prerequisites carry no offset or
duration. Relative rules have no duration and at most one anchor per rule. `WITHIN_AFTER`
means an upper-bound deadline after an event, not an exact wait. An unstated reference
event stays empty with AMBIGUOUS status, its stated expression/offset and missing evidence.
An EXPLICIT relative rule with no event or date receives a review finding; nothing is rewritten. Shared targets use
separate rules. An ambiguous anchor remains unresolved; the application does not infer it.
Small contract checks flag mixed fields or a NOT_STATED assessment with positive rules.
Relative rules with no event, date, relation or offset are also flagged for review,
including AMBIGUOUS rows; their text and uncertainty remain untouched.
These checks neither interpret source language nor rewrite/filter model responses or trigger retries.


Models select `evidence_id` values from source-span catalogs instead of copying
quotations or page numbers. IDs bind the source, document, physical PDF page,
original page hash and character offsets. The app resolves exact original text
under task/offer `derived.citations` and assessment `derived.resolved_evidence`;
raw `gemini` values remain unchanged. These bindings do not establish whether the
passage supports the interpretation.

An unresolved ID can trigger one logical citation-repair request for the affected
citation lists. The separate `citation_evidence` audit preserves the original
findings, bindings and repair response. Valid supporting evidence is retained;
unknown IDs, unsupported claims and ambiguous repairs remain unresolved.
Historical quoted responses retain strict source/page/quote checks, including
Unicode/whitespace normalization. No fuzzy match automatically passes validation.
See [PHASES.md](PHASES.md) for the separate saved-run audit command and recovery behavior.

Application findings also cover malformed responses, unknown/duplicate unit IDs and
missing assessments. `citation_validation: "page_and_quote_checks"` retains the
legacy check label; `evidence_protocol: "source-span-ids-v1"` identifies the new bindings.
`status: "recorded"` means responses were recorded, not that their meaning or
citations were verified. Unknown house features and genuine uncertainty remain in
Gemini's responses. Original proposals and every response remain unchanged under
`gemini`; source metadata and response references are application-generated.

```sh
PYTHONPATH=pipeline/working-units/src .venv/bin/python -m bauvo_working_units.cli \
  --pages /path/pages.json --documents /path/documents.json \
  --output runs/manual/new-run/working-units.json \
  --raw-output runs/manual/new-run/raw.json \
  --trace-dir runs/manual/new-run/trace \
  --cache-dir runs/manual/new-run/cache --project YOUR_PROJECT
```

Each provider request has at most one unchanged retry for incomplete/empty/malformed
JSON or transient API failure. No failed response is appended to the retry prompt.
`raw.json` (or `working-units.gemini-raw.json` by default) preserves all returned
values, including failed attempts. The sibling `*.attempts.json` records prompt
hashes, raw indices, finish reasons and failures; `trace/*-response.json` preserves
exact provider text for successful and failed attempts. Transport failures with no
response have no fabricated raw value. Provider SDK retries are disabled.

If the retry fails, partial units and independent assessments remain in output with
`status: "request_failed"` and explicit findings. The CLI exits unsuccessfully;
it does not mark the run complete. A recovered first failure remains in raw/attempt
files. Review flags and model uncertainty are never removed by retry handling.

Use a new output directory for this protocol. Prior responses and outputs are never
overwritten. Cache identity includes protocol, schema, model and generation settings.
Historical schema-5 extraction remains unchanged; the later report stages still
require a separate schema-6 migration. See `evaluation/README.md` for focused tests.

The five task prompts use Rolle / Kontext / Aufgabe / Ausgabe. The preparatory
project-facts prompt asks which agreements and obligations of both parties the excerpt describes,
using the supplied context that it belongs to the signed contract package. Prompt files live in `src/bauvo_working_units/prompts/`; Aufgaben uses the agreed
German prompt with a JSON output contract instead of a Markdown table. Offers are
extracted independently from Aufgaben. No assessment result is fed into another pass.

Optional `--date-anchors /path/anchors.json` supplies a JSON array of objects with
`id`, `document_id`, `event`, ISO `date` and `citations: [{pdf_page, quote}]`.
The date and literal event name must occur together in a valid quote from that
document. Unsupported anchors fail before any paid call. A price-guarantee expiry
is not a construction-start date; only an explicitly matching timing rule may use it.
The BLB alone does not establish this project's guarantee expiry.

Calendar arithmetic is application-derived under each PAGE_TIMING review's
`derived.date_calculations`; `gemini` and separate raw responses remain untouched.
Missing anchors, ambiguous/approximate periods, working days without a calendar,
unsupported offsets and month-end ambiguity remain UNRESOLVED. Conditions remain
on calculated candidates; calculation does not establish applicability or progress.
Durations/prerequisites are not converted into fabricated calendar deadlines.
Only explicit numeric source dates and exact German integer day/week/month/year
periods are currently supported; no inferred event chains or schedules are built.


## Project-specific applicability

Applicability runs after timing and consequences for every document. Its lawyer role
assesses scope and modality (required/recommended/optional/permission/unclear) separately.
Aufgaben returns only name, action, responsible party, performer and citations; IDs
are assigned by the application. Timing belongs to its dedicated project-manager pass.
No automatic duplicate merge or deletion is introduced: primary-page instructions
and known source occurrences guide overlap handling while raw proposals stay intact.

For standalone BLB analysis, supply the contract and accepted project documents as
additional source evidence (presence alone does not prove acceptance):

```sh
.venv/bin/python -m bauvo_working_units.cli \
  --pages runs/manual/blb-schema6/pages.json --source-type blb \
  --documents runs/manual/blb-schema6/documents.json \
  --context-pages runs/manual/contract-schema6/pages.json \
  --context-documents runs/manual/contract-schema6/documents.json \
  --project YOUR_PROJECT --output runs/manual/new-analysis/working-units.json
```

Repeat both context arguments in matching order for additional sources. The case
pipeline supplies the other project sources automatically. Before task extraction,
the production entrypoint sends every page of all supplied non-BLB contract sources,
including their attachments and amendments, in every request using
`phases/project_facts/prompt.txt`. Task window/overlap/character settings do not split
or truncate this context. Each request assigns at most 50 source passages through
`target_evidence_ids`; only these targets must be interpreted or explicitly excluded.
The complete catalog remains citable, including amendments outside the targets.
BLB documents remain outside project-fact collection.
The pass produces readable, source-backed interpretations. It retains conditions,
exceptions, alternatives and explicit uncertainty without silently resolving conflicting
clauses or treating a model interpretation as authoritative source text.

The response separates interpretation from supporting references and exclusions:

```json
{
  "verpflichtungen": [{
    "uncertainty": null,
    "claims": [{
      "statement": "Alternativ ist eine Zahlungsbürgschaft zulässig.",
      "evidence_ids": ["E-clause"]
    }],
    "zitate": []
  }],
  "nicht_als_fakt_erfasst": [{"evidence_id": "E-contact", "grund": "Reine Kontaktangabe"}]
}
```

Each target ID must be selected by at least one interpretation or excluded exactly once
with a nonempty reason. Exclusions must belong to the targets, and every interpretation
must cite a target. Other passages may support an interpretation without requiring
a disposition in that response. IDs cannot have both dispositions within one response.
Related conditions,
exceptions and table rows belong with their clause; separate clauses may share
supporting passages. The application binds IDs to the original source, orders them
by source position and builds `derived.text` from untouched quotations, with explicit
separators between passages. `derived.interpretation` is assembled directly from the cited claim statements;
`derived.uncertainty` normalizes only blank text to null. The original response,
including any historical explanation, remains unchanged under `gemini`. They never replace the
source quotations, and valid citations do not establish correct interpretation.

Every fact has nonempty `claims`, each with a `statement` and nonempty `evidence_ids`.
Claim IDs are authoritative selections and do not need repeating in `zitate`. The
application builds `derived.verified_citations` from their source-ordered union with
additional fact-level references. `zitate` may be empty. References selected only at
fact level remain separately in `derived.context_citations` and are forwarded as
context, without assigning them to claims. A context reference cannot replace
evidence for any claim. The application binds each claim independently in `derived.claims` with its original statement, source-ordered
`verified_citations` and exact `text`; downstream passes retain this mapping.
Missing, unknown or invalid claim references make a batch incomplete
and blocks downstream analysis. The completed provider response is still stored and
replayed without another paid call; raw invalid rows remain preserved.
The prompt requires a complete provision before its summary and checks meaning,
qualifications, observations versus events, uncertainty and evidence within the same
request. These semantic checks are model instructions, not application verification
or a separate model call.

Collection schema 11 (`evidence_protocol: source-backed-interpretations-v7`,
`catalog_protocol: source-lines-v2`) preserves raw responses, source fingerprints,
interpretations, exclusions with original quotations, and per-request `coverage`.
`source_documents` retains the complete original non-BLB project documents. Every
downstream pass receives these full texts alongside interpretations and evidence, so
it can inspect and cite passages the facts model did not select. Interpretations may
cite an original rule and an amendment together; `derived.sources` retains each source
identity, and single-document identity fields are null for cross-document entries.
The prompt requires source-supported conclusions and checking the complete package
before claiming a referenced amendment is missing. An uncertainty note cannot excuse
an unsupported assertion in the interpretation. Older schema-2/3/4/5
artifacts, schema-6 single-request results, schema-7 batched results and schema-8
strict claim-union results, schema-9 subset-check results and schema-10 union results
remain readable. Use offline replay for schemas 8–10 before the new analysis flow.
Unknown IDs, empty claim evidence, invalid rows, contradictory dispositions and
missing targets produce `incomplete`; the pipeline saves the result and stops before
Aufgaben. Repeated valid IDs are normalized only in derived data. Completed provider
responses are stored independently of validation and replayed by default. An incomplete
STOP answer is not automatically sent to Gemini again; explicit targeted refresh is
available when a new answer is actually needed. Transport failures or truncation may
retry once within the shared, explicit attempt limit. There is no verifier or
citation-repair request in this phase.

Every batch still receives the complete contract package. Its compact `source_pages`
contain source-qualified page identities and `segments`: pairs of evidence ID and exact
original text (null IDs preserve gaps). Concatenating segments reconstructs each page
exactly. Full offsets, hashes and canonical evidence remain local, avoiding a second
copy of the source text and repeated provenance in the provider payload.

Each provider attempt now records `diagnostics` in `<output-stem>.gemini-raw.attempts.json`
and `trace/NNNN-attempt.json`, including failed or truncated generations. It contains
`requested_model`, the serialized `generation_config` sent to the SDK, returned
`model_version` and `response_id`, and `usage_metadata` with prompt, response, thinking,
total and cached-token counts when supplied by the provider. The console prints a
short request/settings and response/token summary. Missing metadata remains null;
historical responses without usage are not assigned invented token counts. Caches
and request recovery preserve original diagnostics alongside their response. This
logging adds no model requests and does not change generation limits or prompts.

Selectable evidence uses original nonblank source lines, with a bounded literal-span
fallback for long lines. Abbreviations do not create boundaries and independent table
positions can be selected separately. Each line retains source identity, physical page,
character offsets and the page fingerprint. A clause can use several lines and share
qualifications with another clause. Paragraph headings do not force whole-section groups.
Adjacent selections are reconstructed from the original text, including whitespace-only
gaps; omitted nonblank text and page changes retain explicit separators.

Downstream stages receive interpretations, uncertainty, original quotations, source
identities, document titles, exclusions, unresolved findings and complete original
project context. Historical schema-2/3/4/5 collections remain inspectable using their
original protocols. New analysis requires schema-11 full-context claim facts; replay a saved claim
collection offline or extract from accepted pages and grouping outputs. No OCR rerun is required. Complete
accounting does not prove an interpretation or exclusion is semantically correct.

The CLI writes `working-units.project-facts.json` alongside `working-units.json`
(or `<output-stem>.project-facts.json` for a custom output name). That file includes
interpretations, exclusions, passage accounting, source fingerprints, full source spans,
raw responses and unresolved findings. The main raw response file and traces preserve
the full-context request. A failed request retains its raw attempts and stops downstream
analysis; the final status remains `request_failed`. Existing model settings are unchanged.

`extract_page_units` is the lower-level task-only API: callers must supply a collected
`project_facts` object. Without one, applicability sees `not_collected`, never an
implied complete project assessment. Use `extract_project_units` or the CLI for the
complete workflow. No inferred source precedence, fact acceptance, semantic deduplication
or dependency graph is added.


## Explicit BLB type and cumulative pass files

BLB files are general specifications and never supply project facts. The case runner
marks the source with key `blb` automatically. For standalone calls, use
`--source-type blb` for a primary BLB. Additional sources default to `project`;
if one is a BLB, provide one `--context-type project|blb` per context source, in the
same order as the context path pairs. Type is explicit metadata, never inferred
from a filename or document title. The BLB still supplies tasks, offers, source
conditions and timing. Excluded fact sources and their fingerprints are recorded.
A BLB-only analysis has fact status `no_project_sources`; this establishes no
project-specific choices.

The CLI creates a fresh `<output-stem>.passes/` directory:

```
00-project-facts.json
01-aufgaben.json
02-offers.json
03-timing.json
04-consequences.json
05-applicability.json
```

Files 01–05 are cumulative working-unit documents. Tasks retain their IDs and raw
extraction; offers and later assessments are appended to the same records. Each
file is written when its entire pass has been attempted, before the next pass
starts. A failed request remains visible and does not mutate an earlier snapshot.
The final `working-units.json` equals file 05. File 00 is the separate preparatory
fact collection, also saved as `working-units.project-facts.json`.

Snapshot files are created exclusively and never overwritten. Reusing an existing
output/snapshot directory is refused before new model requests; use a fresh output
path and the existing model cache if needed. Individual Gemini calls remain focused
on their source-based question; cumulative files are assembled by the application,
not by asking Gemini to regenerate the whole JSON. Raw responses stay separate.
The optional `on_pass(name, snapshot)` callback exposes this behavior to API callers.

## Reuse an existing facts artifact

Stages have explicit handoffs:

```text
pages.json + documents.json → project_facts.collect_project_facts → facts artifact
facts artifact + original sources → fact_artifact.load_facts → task workflow
pages.json + documents.json → Aufgaben → offers → timing → consequences → applicability
```

`--project-facts` reads the **full collection** (`working-units.project-facts.json`),
not the compact `verpflichtungen.json` export. Supply the original fact sources as
context inputs so the loader can check their fingerprints and citation provenance.
Review findings and unresolved failures remain part of the collection. Reuse skips
fact model calls; it does not mark any fact or source as approved.

For example, run only Aufgaben using saved contract facts:

```sh
PYTHONPATH=pipeline/working-units/src .venv/bin/python -m bauvo_working_units.cli \
  --pages /path/blb/pages.json --documents /path/blb/documents.json --source-type blb \
  --context-pages /path/contract/pages.json --context-documents /path/contract/documents.json \
  --project-facts /path/working-units.project-facts.json \
  --stop-after aufgaben --project YOUR_PROJECT --output /new/directory/tasks.json
```

This writes a separate facts copy and `00-project-facts.json` / `01-aufgaben.json`
snapshots. The input is unchanged. Current-run raw responses contain only the new
requests; earlier responses remain in the embedded input collection. `completed_passes`
records which task passes were executed; execution does not prove completeness.
The facts are used to assess applicability later, not to suppress initial tasks.

Use `--stop-after project-facts` without `--project-facts` to run only fact collection.
Its output can be supplied directly to the next command's `--project-facts` input.
Other stop points are `offers`, `timing`, `consequences`, and `applicability` (default).
These run the workflow up to that point; resuming a later assessment directly from
an intermediate task artifact is not yet supported. Existing model caching can reuse
identical calls when running a longer prefix into a new output directory.

## Working-units-only export

Task runs automatically write `<output-stem>.units-only.json` containing only
`{"working_units": [...]}` with the exact provider objects: no application IDs,
`derived`, assessments, facts or review findings. Citations and any additional
provider fields are retained; order and overlap duplicates are unchanged.
The full result and raw log remain the evidence for review and later stages.

Export a completed run without model calls:

```sh
PYTHONPATH=pipeline/working-units/src .venv/bin/python -m bauvo_working_units.unit_export \
  --input /path/working-units.gemini-raw.json \
  --output /path/working-units.units-only.json
```

The exporter refuses to overwrite existing files. Raw logs with unfinished responses
or invalid working-unit arrays are rejected instead of silently omitting them.


## Extract offers from saved Aufgaben

The offer-only entry point uses the same offer prompt for contract and BLB sources.
It validates the saved source fingerprints, boundaries and window layout before any
model call, then preserves the tasks, facts and earlier raw responses unchanged.
Only PAGE_OFFERS is requested. Earlier files are never overwritten.
Every request receives complete text from the primary source documents and all
supplied context documents, so contracts and amendments can establish that a
service is already agreed. New offers remain scoped to the local primary pages.
Document presence alone does not establish acceptance. Cross-document evidence
uses source-qualified citations; full context fingerprints are recorded.

```sh
PYTHONPATH=pipeline/working-units/src .venv/bin/python -m bauvo_working_units.offer_stage \
  --working-units /previous/run/working-units.json \
  --pages /source/pages.json --documents /source/documents.json \
  --context-pages /other-source/pages.json --context-documents /other-source/documents.json \
  --context-type project \
  --project YOUR_PROJECT --output /new/run/working-units.json
```

Run this once per source, supplying that source's full Aufgaben-only artifact.
Repeat the context path pair for every other project source. Use `--context-type blb`
for general specifications; when setting types, provide one per context pair.
Otherwise saved context types are retained, with new sources defaulting to `project`.
The PDF pipeline supplies all other contract and BLB sources automatically.
Defaults are Gemini 3.5 Flash minimal, ten-page windows and one-page overlap.
Outputs are the cumulative working-units.json, working-units.offers-only.json,
working-units.gemini-raw.json (new requests only), attempt records and working-units.trace/.
Failed requests keep partial offers and original tasks with request_failed status.
The input must have completed Aufgaben extraction; earlier review findings remain.
This entry point does not resume timing, consequences or applicability yet.


## Assess timing from saved Aufgaben and offers

```sh
PYTHONPATH=pipeline/working-units/src .venv/bin/python -m bauvo_working_units.timing_stage \
  --working-units /previous/offers-run/working-units.json \
  --pages /source/pages.json --documents /source/documents.json \
  --project YOUR_PROJECT --output /new/timing-run/working-units.json
```

Only PAGE_TIMING runs. The saved tasks, offers, facts and earlier responses remain
preserved in the new cumulative result; timing reviews are appended to each task.
New raw responses and prompt traces are separate. The sibling
working-units.timing-only.json contains only the provider's timing assessments.
Defaults remain Gemini 3.5 Flash minimal, ten-page windows and one-page overlap
within document boundaries. Assessments use batches of up to five tasks.
Source/window changes and incomplete prior stages are rejected before model calls.
Use --date-anchors for source-cited event dates, or retain the saved anchors by
omitting it. Calendar dates remain unresolved without a supported event/date;
the application retains its existing source-checked date calculation logic.
This does not run consequences or applicability, or assess offer acceptance dates.


### Supply refreshed facts to timing

Add `--project-facts /new/facts-run/working-units.project-facts.json` to the
`timing_stage` command. The facts artifact is validated against the original
source pages and document boundaries before model calls. No facts, tasks or offers
are re-extracted. Every timing request receives all facts for that exact
(source_key, document_id), retaining original statements, citations and review flags.
There is no relevance ranking or cross-document fact selection for timing.
Verified fact quotes can support timing citations outside the ten-page source
window. The window itself remains unchanged. Unverified quotes keep their flags.
The cumulative result includes the new project_facts. When replacing an older
collection, previous_project_facts and previous_fact_review_findings preserve its
evidence separately; current review_findings refer to the new facts and existing
task/offer/timing findings. The original input files are never rewritten.

Saved schema-8/9/10/11 claims can be replayed offline with
`--replay-project-facts` (see [commands](REPLAY.md)). The replay validates the
original artifact against its sources, preserves raw answers and source fingerprints,
archives earlier findings with the original artifact digest, and recomputes schema-11
derived evidence and interpretation from claims. Separate review notes survive.
Unknown evidence, missing claim evidence and genuinely missing targets remain
blocking. This is structural revalidation, not semantic approval or a model call.
The older `revalidate_context_citations` API remains available for historical
schema-8/9 to schema-10 migrations.
