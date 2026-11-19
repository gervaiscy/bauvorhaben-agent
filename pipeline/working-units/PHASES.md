# Modular phases and sequential pipeline

The current workflow defaults to offline replay. See [response storage, planning and
call limits](REPLAY.md) for commands. Completed provider answers are saved even when
local validation fails; no paid retry is triggered by business validation.

Current project-facts extraction uses schema 15 / `contractual-rights-v3`: homeowner
rights, duties, restrictions, permissions and rules governing formation, binding
declarations, representation and formal requirements, interpreted from the contract
package. Its German lawyer prompt asks: "Welche Regelungen enthalten die
Vertragsunterlagen für mein Bauvorhaben, und was bedeuten sie für mich als Bauherrn?"
Gemini returns `ansprueche: [{aussage, belege}]`; source labels are the
only identifiers it selects. Provision grouping remains internal to batch planning.
No model-owned IDs, relationship graph, status classification, nested claims or
passage dispositions are required. Full contract context remains in each request.

The application resolves exact quotations, preserves all provider JSON, and checks
response structure, source references and completed batches. Processing completion
is explicitly separate from semantic completeness. Empty extraction is valid and
does not remove any original source text. All downstream phases retain source
access and receive `extraction_scope: homeowner_contractual_position`. Older artifacts replay under
their own protocols; they are never converted to rights by renaming fields.
The units cache protocol is 127; the PDF extraction protocol is unchanged.

Timing uses `timing-page-quotes-v1`. It sends the original `source_pages` window
unchanged and project facts only as `project_facts.facts: [{id, text}]`, where text
is the original supporting wording, not the model interpretation. Gaps between
selected passages remain visibly marked. The model input has no evidence catalog,
full contract source documents, fact-level provenance metadata or repeated fact
citations. The application retains those sources and provenance in saved artifacts.

Timing answers keep `{unit_id, answer, citations}`. Citations select either a
current-window `{pdf_page, quote}` or a supplied `{fact_id}`. Page quotations bind
uniquely with whitespace tolerance to the original page; fact IDs restore their
verified source references, including source/document identities for colliding page
numbers. Unknown facts, missing fact evidence, wrong pages and altered/ambiguous
quotations remain findings. No automatic Gemini citation-repair call is made for
this timing contract. A narrow local fallback also accepts one uppercase-I/lowercase-l
OCR difference inside a word, only when the source span is unique. It never changes
digits, punctuation, standalone letters, Roman numerals or more than one character.
The binding audit records this fallback, and resolved evidence restores the original
source spelling. Raw answers are preserved alongside derived resolved evidence.
Legacy structured timing artifacts remain readable. Earlier timing request caches
and checkpoints do not satisfy the new prompt/schema identity; OCR, grouping,
settled facts and accepted Tasks/Offers do not need to be rerun.

The timing prompt distinguishes task-specific timing from the overall completion
framework and keeps optional execution conditional on selection or commissioning.
Unrelated withdrawal/funding/selection periods do not become deadlines for other
actions. Generic timing controls cover irrelevant withdrawal periods, optional and
included installations sharing a project timeframe, and a selection deadline that
differs from overall construction completion. These held-out expectations are never
sent to Gemini; offline tests verify transport, while the isolated five-task live
trial is retained under manual runs rather than incorporated as project evidence.

The timing prompt binds each date or duration to the particular action or included
service it modifies, distinguishing confirmation submission, promised delivery,
installation and included rental. Two fictional illustrations use unrelated sources
and values. A task-specific answer omits independent project obligations. When only
a general completion framework applies to a construction service, the answer refers
to the fully cited framework without rewriting weather clauses for every trade.
When a complex time rule is itself assessed, its operative original wording and
qualifications are retained. NOT_STATED and AMBIGUOUS are exact standalone answers.
The schema, original-window input and source-backed facts remain unchanged. There
is no new model pass, keyword filter, inferred schedule or answer rewrite.

A planned subsection duration does not inherit the contract date as its start from
an overall schedule heading. Without an explicit subsection start, the answer keeps
the planned duration and leaves its start unspecified. Explicit common or individual
start events remain applicable. Citations support the returned rule and its relevant
qualifications; general completion answers do not accumulate independent financing,
funding or construction-start requirements. Four held-out controls distinguish an
unstated start, an explicit common start, an explicit individual start and separate
phase durations within a combined task. A combined task does not give its components
a shared duration or turn a required completed state into an execution period.

Four additional held-out cases cover contained rental periods, post-completion
inspection amid unrelated financing/funding clauses, heating conditions restricted
to particular trades, and competing confirmation/delivery/selection/optional-work
rules. Their expectations are separate from model inputs. Offline tests verify
source delivery, routing and preservation; they do not prove interpretation quality.

Timing review output separates the task result from internal audit data:

- `<output>.json`: `status` and `working_units`, each with `id`, `title`, `actor`,
  `quote` and `timing: {answer, citations}`. Bound task quotes use original source
  text; unbound proposals are marked `quote_verified: false`. Missing assessments
  remain `timing: null`. Unexpected duplicate assessments remain visible under
  `additional_timing_assessments`. No missing result is presented as NOT_STATED.
- `<output>.audit.json`: the complete cumulative artifact, including original
  model responses, provenance, earlier stages and all validation findings.
- `<output>.validation.json`: findings with working-unit IDs where the finding
  identifies a task or assessment row. Request-wide findings remain request-wide.
- `<output>.gemini-raw.json`: unchanged provider answers, as before.

Timing citations show document titles, PDF pages and original supporting text;
internal offsets, hashes and repeated response envelopes stay in the audit file.
The readable projection removes duplicate selections of the same exact source span
and combines adjacent spans from the same source page, copying only original
intervening whitespace. It never bridges omitted substantive text. All individual
selections remain in the audit. Existing uncombined readable projections remain
accepted when they match their sibling audit.
Unknown references remain visible and retain their validation findings. The
standalone command also retains the existing timing-only export and checkpoints.
Consequences and applicability accept the readable timing output and automatically
load its sibling audit, checking that the readable projection matches. Keep those
two files together. Legacy cumulative inputs remain accepted. The sequential
pipeline records the audit as its internal handoff and also writes the readable
`result.json` in the timing attempt folder. Generic CLI `--stop-after timing` uses
the same output format. Provider prompts and request identities are unchanged, so
saved exact timing requests remain reusable without paid regeneration.

Aufgaben sends the current document-local window of at most ten pages as plain
original page text, with page numbers, overlap markers and checkbox metadata.
Documents remain grouped according to `documents.json`. Accepted
`project_facts.facts` retain their interpretations and original quotations as
contractual context; full fact source documents and evidence catalogs are not sent.
There are no selectable segment labels in the current Aufgaben request.

The German prompt identifies tasks and their performers. Gemini returns only:

```json
{"working_units": [{"title": "Kurzer Aufgabenname", "actor": null,
                    "quote": "Unveränderte Originaltextstelle"}]}
```

There is no `carry_forward`, task description, evidence-ID selection or requirement
to collect conditions, deadlines and exceptions in this stage. Independently
executable tasks are separate rows and may share the same original quotation.
The title is an orientation aid; actor is Gemini's source-based interpretation or
null. The task text is the original quotation, not the title.

The application binds each quote within the supplied window of the current document.
Whitespace and line breaks may differ. If no formatting-only match exists, it also
accepts a unique source span with at most two words omitted from Gemini's quotation.
It does not substitute or add words, skip numbers or punctuation, or search other
documents. Matching words are case-sensitive. Derived text always restores the
complete original source span, including omitted words; raw JSON remains unchanged.
The binding audit records the matching policy, method and omitted words. Repeated
candidate locations are ambiguous and remain findings. Gemini can include more surrounding original text
to disambiguate them. Cross-page quotes use the document's supplied page order with
one newline between pages; canonical citations retain exact per-page spans.

Every provider row and complete raw response remain unchanged, including invalid
proposals. Derived source locations contain application-owned IDs, page, offsets
and exact quotations. Derived task records use `task-original-quote-v1` and retain
`title`, `actor`, `source_text` and canonical `citations`. Saved raw task responses can
be rebound offline through the citation audit, retaining prior audits and findings
in its history without calling Gemini. Downstream views use the
original `source_text` as `action` and the generated title as `name`; conditions,
timing, consequences and applicability retain their separate stages and source
access. Invalid quotes remain visible with findings and do not trigger an additional
model verification or repair request. Local checks establish source binding and
structure, not semantic extraction completeness or actor correctness.

If a quotation-based task lacks bound `source_text`, downstream assessment views
still send its unchanged model-proposed quotation as `action`, explicitly marked
`action_source: unverified_task_quote`. This identifies the task without treating
its quotation as verified evidence. Timing retains the original extraction window
when source locations are unresolved, and receives the original pages and evidence
catalog for its own citations. Existing task findings and raw answers are preserved;
no quote repair or Tasks rerun is required. Structurally unusable task records receive
an `assessment_task_unusable` finding instead of disappearing silently.

For quotation-based tasks without a bound `source_text`, assessment requests retain
Gemini's unchanged task quotation as the proposed `action`, explicitly marked
`action_source: unverified_task_quote`. The original document window remains the
assessment source; the fallback creates no citations or validated source text and
does not clear extraction findings. This applies to timing, consequences and
applicability through their shared task view. Records without any usable action
receive a per-stage `assessment_task_unusable` finding instead of disappearing
silently from assessment traversal.

The same document windows, raw-response storage, request budgeting and offline
cache replay remain available. A distinct prompt/schema identity prevents the
new requests from reusing old extraction answers. Settled project-facts artifacts
remain reusable. Historical ID-selection and action/citation records remain readable
through their existing compatibility paths; they are not new provider output schemas.

Offers uses `window-additional-offers-v7` and `offer-passage-labels-v1`.
Its German question is:

> Welche konkreten zusätzlichen Leistungen beschreibt der Auszug als gesondert beauftragbar, gesondert zu vereinbaren oder gegen zusätzliche Vergütung erhältlich?

The prompt includes the approved JSON schema. The same schema is applied by the
provider adapter. Gemini returns `offers: [{name, provider, description, terms,
citations: [{passage: "P58-1"}]}]`. Gemini selects short source labels instead of copying quotations.
Short clarifications exclude already agreed services using the project facts,
separate distinct extras, distinguish explicit commissioning from cost duties or
technical compatibility, require source-supported providers, and retain necessary
supporting reference sentences through additional passage selections.
Fictional examples demonstrate indirectly commissioned work and three independent
offers sharing one passage label. An explicitly inseparable package stays one offer.
Examples are instructions, not extraction sources; held-out evaluation cases differ
from these demonstrations.

Each request contains only `source_pages: [{pdf_page, passages: [{passage, text}]}]`
for the current window's primary pages and `project_facts`. Documents stay grouped according to
`documents.json`, with at most ten pages per request. The existing ten-page/one-page
overlap planning metadata is preserved for accepted Aufgaben handoffs, but Offers
does not resend the overlap page: each primary page is processed once. Empty
primary windows are skipped. Full BLB/contract context, other documents, previous
offers (`known_items`), task records and evidence catalogs are not sent.

Project facts retain interpretations and compact literal supporting quotations,
with document/page references and a document-label/title list, without complete
contract pages or private application IDs. Repeated fact quotes are deduplicated;
only adjoining verified spans are joined. Gaps remain distinct. Existing fact
scope/qualifications remain available. Original project-fact artifacts and all
source snapshots stay unchanged.

The application labels original paragraphs `P<PDF page>-<paragraph number>` within
this request. Blank lines define paragraph boundaries; wrapped lines and table
content stay together. The passage slices include separators and leading/trailing
whitespace, so concatenating them reproduces each original page exactly. The page
text is sent once, without duplicated full-page text. A paragraph may contain
multiple independently selectable services, which can share its label.

Gemini returns only passage labels under `citations`. The application resolves
these against the current document's primary pages and builds `derived.citations`
from original text, source/document identity, page hashes and offsets. Hashes and
offsets never enter the source-selection wire payload. Full reference maps are
saved with each response and checked against the original sources during offline
audits. Labels are request-local; a label reused in another document cannot bind
to that document's text. Project-fact references are not selectable offer sources.

A missing `P` prefix is normalized only for a positive ASCII `page-paragraph`
identifier whose exact `P<page>-<paragraph>` label exists in this request's map.
The binding audit records `missing_prefix_normalization`, `original_passage` and
the canonical `passage`. Raw responses are unchanged. No other spelling, case,
whitespace, numeric or cross-window corrections are inferred. Saved responses can
replay this local normalization without a new provider request.

Raw provider JSON remains unchanged. Unknown labels, citations with copied quotes
or extra provenance fields, malformed responses and exact duplicate offer records
remain visible and block downstream stages. Shared evidence alone never merges
independently described offers. No requirement to account for every passage, extra
verification call, fuzzy matching or citation-repair call is introduced.

Each offer also has application-generated `derived.review_context`, marked with
`review_context_protocol: offer-paragraph-context-v1`. For each successfully bound
citation it stores the entire containing paragraph(s), with exact original text,
source/document/page identity, page hash and offsets. Blank lines delimit paragraphs;
wrapped lines stay together. If the quotation starts a paragraph (ignoring leading
whitespace), the preceding paragraph is included. At the start of a page this can
include the last paragraph on the immediately preceding page of the same document,
even outside the extraction window. Page-number gaps and document boundaries are
not crossed. Paragraph boundaries are layout-based; context is not guaranteed to
resolve every semantic reference.

This field is surrounding source context, not Gemini-selected evidence. It never
enters the model payload or the selected-citation list, expands an evidence claim,
repairs an invalid quotation, or overwrites raw JSON. Invalid citations get no
context. Cached responses rebuild context locally; offline citation audits refresh
it from the original sources without an Offers review call.

Unknown passage selections and malformed proposals do not stop the remaining Offers
windows. The application collects every window response, then validates the
complete pass once. Findings and raw responses remain visible; unresolved offers
still make the output incomplete and block downstream stages.

All exact window requests are planned before contacting Gemini, including saved
answers and the new-call limit. Completed answers replay independently of local
validation. Offers makes no verification or citation-repair call. Local checks
establish source binding and structure, not semantic correctness or completeness.
Existing v6 quote-based Offers remain readable and valid completed results can
continue downstream. Their completed responses cannot satisfy new v7 requests:
the prompt and schema have different request identities. Accepted v69 Aufgaben,
settled facts and source extraction remain reusable.

Historical v6 quote-based Offers runs can also use the offline `bauvo_working_units.offer_recovery`
entry point. It requires an explicit source-selection manifest bound to the full
input digest. Each selection identifies an existing failed quotation, its model
citation digest and an exact original source span (source/document/page identity,
page hash, offsets and literal text), plus an inspection reason. All source and
window bindings are checked before writing a separate result. This is an explicit
source selection following inspection, not fuzzy matching or automatic acceptance
of changed wording. No provider is constructed or called.

The recovered result preserves original provider JSON, original findings and
previous citation audits. It records `explicit_original_source_selection` bindings
in the new audit and regenerates derived citations, paragraph context and offer
validation. Unresolved failures remain blocking. A successful recovery establishes
source binding only; it does not approve classifications, completeness or customer
publication. The original run and provider cache are never overwritten.

The following paragraphs describe earlier revisions of the historical selection
protocol; their complex evidence-selection rules are no longer the live prompt.

The initial 12 November 2026 Aufgaben prompt clarification makes each task's selected
evidence self-contained: complete relevant sentences, linked prerequisites,
exceptions, alternatives, timing extensions and limits, and relevant table rows
must be selected together. Line labels are explicitly described as fragments,
not complete provisions. Fictional examples cover split sentences, alternative
evidence requirements and a payment table with a withholding exception. A fact
or citation attached to another task does not substitute for selecting that
task's own evidence. This changes the provider prompt fingerprint without changing
the response schema or local source-binding protocol. There is no additional
model call or automatic expansion of selected passages. Offline checks verify
transport and example consistency; semantic improvement requires reviewing a
fresh provider response. Existing run files are preserved.

The subsequent Aufgaben boundary change uses the paragraph/row selections described
above. A selected paragraph restores all its original line spans, including internal
whitespace, rather than exposing individual wrapped lines as model choices. This
does not append any unselected paragraph. Paragraphs are layout units, not verified
semantic clauses; related provisions elsewhere still require model selection.
Project-facts input, catalog, prompt and response identities are unchanged.

The 13 November 2026 Aufgaben prompt clarification follows references between
available passages (including credited totals and their conditions), separates
performers from document issuers and recipients, and checks each primary paragraph
and table row for concrete work. Passive and nominal work remains eligible;
unknown actors remain null without suppressing the task. Receipt-only acknowledgements
do not establish a new delivery task. Scope exclusions qualify the associated
positive service and do not alone assign excluded work to anyone; explicit
separate assignments remain tasks. Seven fictional controls exercise these
distinctions alongside the existing promotion and optional-commission controls.
This changes only the prompt fingerprint, not the input/output contract, source
binding or orchestration. No extra provider request, semantic deletion filter or
coverage-accounting field is added. Offline tests verify preservation of authored
selections and actors; semantic accuracy still requires reviewing provider responses.

The subsequent prompt revision replaces mixed prose/JSON demonstrations with eight
fictional inputs and complete expected JSON outputs. Examples cover a credit whose
amendment is context-only, equivalent consents and withdrawal roles, penalties with
referenced deadlines, standard scope/exclusions and independent actions in a shared
paragraph. Commissioning, alternatives and unknown actors remain demonstrated.
The definition explicitly includes concrete permissions, choices and recommendations;
source-supported actor roles suffice without personal names. Five separate evaluation
cases remain outside the examples. Schema, source binding, request flow and settled
project facts are unchanged. The updated prompt has a new request identity; existing
provider answers and run files remain intact. Offline example checks do not certify
Gemini's semantic completeness, and no live provider run is included in this revision.

Current local validation accepts a fact supported anywhere in the supplied contract
package; evidence need not overlap its assigned extraction batch. Batch assignments
still guide the prompt. The application records `validation_protocol:
full-contract-evidence-v1`. Offline replay verifies older artifacts under their saved
rules, then retires only the former batch-scope failures from active findings while
preserving them in validation history. Unknown or empty evidence and request failures
remain blocking. Provider prompts, response JSON and request identities are unchanged.

The twenty-one current contractual controls are in `evaluation/contractual-rights-cases.json`;
older clause/status controls remain historical regression evidence. Offline tests
cover the current CLI, full pipeline, citations, failures, replay and call limits.
They do not establish Gemini's interpretation quality; no live run is included.

The broader contractual-position prompt retains independent homeowner duties and
permissions even without an associated homeowner entitlement. Fictional controls
cover bounded advertising permissions, payment/cost duties and adjacent credit/viewing
clauses without an invented exchange relationship. The list name `ansprueche` and
its two-field entries remain unchanged. Schema-15 / `contractual-rights-v2` artifacts
retain their historical rights-only scope on load and offline replay; they are never
relabelled as complete contractual-position extractions.

Formal provisions are included even without a direct homeowner claim or an action
to perform. A confirmation rule in an offer footer remains extractable alongside
contact details without establishing entitlement to uncommissioned services.
The prompt preserves the named company scope; fictional controls also cover contract
formation and Textform for amendments without strengthening it to Schriftform.
The two-field response and evidence protocol remain unchanged. Prompt fingerprints
distinguish fresh requests; replay preserves older answers without adding new facts.

The 11 November 2026 response simplification removes the separate uncertainty field
from new Gemini responses, derived rights and downstream projections. Open questions
stay in the interpretation itself. Schema-14 artifacts keep their original fields
and replay behavior. The prompt lock and request protocol reflect the new schema.

The complete 11 November prompt revision aligns every section and fictional example
with documented entitlements only. Unestablished offers/drawings and possible-claim
observations are omitted; a supported part of a service is retained without adding
unsupported features. Documented conditional rights remain extractable without proof
that their conditions were fulfilled. Meaning-changing qualifications and amendments
stay with the entitlement. The two-field schema, batching and single-call flow are
unchanged; the prompt lock and request identity change. Held-out controls exercise
omission of unestablished candidates, retention of a documented subset and conditional
rules, separately from citation validity. They do not certify model interpretation.

Each phase owns its prompt, code and response
schema. `pipeline/prompts.lock.json` records all eight prompt hashes; a prompt edit
requires a reason and an explicit lock update, checked by the offline tests.

The 8 November 2026 Aufgaben prompt revision prevents general company capabilities
from becoming hypothetical commissioning tasks, while retaining specific work in
promotional surroundings. Generic capability-directory controls accompany the revision.

The 8 November 2026 offers prompt revision uses all supplied project documents,
including contracts, amendments and BLBs, to distinguish open offers from already
agreed services. Every offer window receives their complete page text; only its
local primary pages introduce new offers. Document presence alone is not acceptance.

The 8 November 2026 timing prompt revision preserves the exact triggering event,
affected work, prerequisites, alternatives, exceptions, extensions, restart allowances
and caps in the answer. Qualifiers in following sentences and supplied facts must
survive condensation; completeness takes priority over brevity. The free-text
`unit_id` / `answer` / `citations` contract is unchanged. Four generic timing controls
cover scoped starts and weather/restart limits, conditional connection alternatives,
cumulative prerequisites, and restricted clock restarts with unrelated deadlines.
The focused runner supplies and resolves source-bound evidence IDs for pass-only
requests, retaining raw answers and separate expectations without semantic repair.

Full-context review independently derives
complete timing from the source documents, then compares the proposed timing for
omitted qualifications, including amendments. Corrections remain separate proposals;
the original pipeline output is unchanged. Offline tests verify these handoffs and
raw preservation; actual model completeness still requires live semantic evaluation.

Full-context review protocol `manual-applicability-review-v7` also returns explicit
`alternative_groups`: `ANY_OF` between routes and `ALL_OF` between actions within
each route. Both source-only assessment and comparison receive the original sources
plus a project-wide task ID/name/locator catalog; only the current batch's one to
five targets are reviewed. The catalog contains no previous actions, responsible
parties or assessments. Groups can reference tasks outside the current batch.

Each route retains its own timing and evidence. Conditional action proposals link
existing `unit_id` values, or use null for a source-backed step missing from the
extraction. A waived original obligation can therefore stay unchanged while a
separate proposal records the still-available conditional alternative. Selection
stays `NOT_ESTABLISHED` unless the model supplies a documented route and its own
supporting citation. Unknown task IDs, unresolved source locators, malformed groups,
unsupported quotations and inconsistent selections remain validation findings.
These checks establish bindings and consistency, not correct interpretation.

The existing two stateless requests per batch are retained. Comparison groups are
exported separately as `alternatives.json`, with a read-only `checklist.html` showing
routes, conditional actions, distinct deadlines, evidence and unresolved selection.
Invalid groups remain in the raw output and audit; they are not presented as usable
routes. No selection, correction, task deletion or construction progress is applied.
Model text is escaped in the HTML view. New preparation is required for protocol v7;
the full-context stage fingerprint includes the driver, alternative helper and
cache-binding code. Original extraction prompts and the eight-prompt lock are unchanged.

Four synthetic controls in `evaluation/alternatives/cases.json` cover A OR B,
(A AND B) OR C, distinct route deadlines, and a waived duty with an unselected
alternative missing from the extraction. Their expected groups stay outside model
requests. Offline tests cover validation, cross-batch task references, raw preservation,
unselected/documented choices and HTML escaping; live model interpretation is unverified.

The 8 November 2026 citation revision uses protocol `page-working-units-v46`.
All six analysis prompts now select source-span `evidence_id` values instead of
regenerating quotations and page coordinates. IDs bind source, document, actual
PDF page, page content hash and character offsets. The original page text is never
rewritten or truncated. Fact context retains source coordinates, verified citations,
original quotations and unresolved findings. Applicability now returns citations too.

Raw proposals remain under `gemini`. Resolved task/offer citations live under
`derived.citations`; resolved assessment evidence lives under
`derived.resolved_evidence`. Each response's `citation_evidence` audit preserves
the original findings, bindings and any repair answer. A new reference response
with unresolved IDs gets at most one logical repair request, limited to failed
citation lists; valid supporting evidence is retained. Unknown IDs, unsupported
claims and ambiguous repairs remain findings. Normal transport retries and request
checkpoints also cover this repair request. Evidence binding is not interpretation
approval. Historical quoted responses retain their existing validation behavior.

This semantic change requires a new analysis run; old prompts are not replayed
under the new protocol. To audit an existing completed run without model calls:

```sh
PYTHONPATH=pipeline/working-units/src .venv/bin/python -m bauvo_working_units.citation_repair \
  --run /path/to/existing-run --output /path/to/new-citation-audit
```

Only uniquely matching literal quotations receive a recorded source/page correction.
Altered text and ambiguous matches stay unresolved. The separate output retains
raw proposals, original findings and source fingerprints; the existing run is unchanged.
Add `--repair-with-model --project YOUR_PROJECT` to request one bounded model repair
per affected response using the original project sources. Exact source binding still
does not establish that the interpretation is correct.

```text
pipeline/
  pdf-extractor/                  PDF → pages.json
    ocr_pipeline.py
    gemini_ocr.py
    prompt.txt
  document-boundaries/            pages.json → documents.json
    document_pipeline.py
    document_grouping.py
    prompt.txt
  working-units/src/bauvo_working_units/
    phases/
      project_facts/              Contract sources → cited facts; excludes BLB
      aufgaben/                   Pages + facts → working units
      offers/                     Previous output → offers added
      timing/                     Previous output → timing added
      consequences/               Previous output → stated consequences added
      applicability/              Previous output → applicability + reason added
    shared/                      Common traversal, response routing and schema primitives
    pipeline.py                  Sequential execution and saved handoffs
```

Every analysis phase contains `prompt.txt`, `schema.py` and `service.py`.
Offers, timing, consequences and applicability also have a standalone `stage.py`.
The facts phase owns fact-artifact validation; timing owns calendar arithmetic.
Source loading, model transport, caching and raw tracing are shared infrastructure.
Old module commands such as `bauvo_working_units.timing_stage` and
`bauvo_working_units.applicability_stage` still work as compatibility entry points.
The old `pipeline/extraction_support/document_pipeline.py` command also remains usable.

## Run from PDFs

From the repository root, with the environment loaded:

```sh
set -a
source .env
set +a

PYTHONPATH=pipeline/working-units/src .venv/bin/python -m bauvo_working_units.pipeline \
  --contract data/sample/contract.pdf \
  --blb data/sample/blb.pdf \
  --project chatyamo-pushnotification \
  --output "runs/manual/pipeline-$(date -u +%Y%m%dT%H%M%SZ)"
```

Repeat `--contract` for other signed project PDFs, and `--blb` for additional general
specifications. The runner uses Gemini 3.5 Flash. Facts and offers use MINIMAL thinking; working
units, timing, consequences and applicability use MEDIUM thinking. OCR and document
boundaries retain their existing settings. No model call is made by
reading this documentation or running offline tests.

The fixed order is OCR → document boundaries → project facts → working units →
offers → timing → consequences → applicability. All sources complete a phase
before the next phase starts. Facts are collected in batches of at most 50 target
passages; every request contains all contract-source pages, attachments and
amendments, excluding BLB sources. Facts retain individually cited claims alongside
their interpretations; the same request checks completeness and consistency.
Each source's later phases receive the saved facts plus their own source windows.
Working units use ten-page windows with one-page overlap inside document
boundaries. Applicability receives all working units from their original window,
all facts and the lean source payload. Timing retains its existing assessment
window selection and five-working-unit batches. Consequences uses ten-task batches
with each task’s timing assessment, full BLB text and compact project facts, as
described below.

## Outputs and resume

```text
<output>/
  pipeline.json
  01-ocr/<source>/attempt-01/pages.json
  02-documents/<source>/attempt-01/documents.json
  03-project-facts/attempt-01/result.json
  04-aufgaben/<source>/attempt-01/result.json
  05-offers/<source>/attempt-01/result.json
  06-timing/<source>/attempt-01/result.json
  07-consequences/<source>/attempt-01/result.json
  08-applicability/<source>/attempt-01/result.json
```

Every analysis attempt also has `raw.json`, `raw.attempts.json` and `trace/` with
exact model requests and responses. `result.json` is cumulative: the next phase
reads it, extends it and writes a different file. No earlier output is rewritten.
The facts result is a schema-11 source-backed claim collection rather than a
working-unit artifact, using the versioned `source-lines-v2` catalog throughout new analysis.
Original source lines are independently selectable, with source offsets retained.
Continuous selections preserve original whitespace; actual gaps remain visible.
Gemini returns cited `claims`, nullable `uncertainty`, additional context references and explicit reasons for excluded passages. The app attaches exact original
quotations separately and checks every catalog ID is accounted for. Full non-BLB
project-source documents remain in the artifact and reach downstream requests, including
unselected source text. Cross-document interpretations retain the provenance of every
supporting source. Incomplete responses stop the pipeline before Aufgaben. The complete
request remains resumable, while incomplete answers are preserved but never replayed as successes.
There is no separate model verification or citation-repair call for this phase.
Historical facts remain inspectable; saved schema-8/9/10 claims can be replayed offline for the new flow.

`pipeline.json` records each phase's status, artifact fingerprint and final output
paths. Review findings remain visible. A failed model request stops the pipeline
before later phases. To resume, repeat the same command and exact output path with
`--resume`. Verified completed outputs are reused; a failed step gets `attempt-02`
so its first partial result and raw attempts remain preserved. Changed PDFs,
configuration, code or saved outputs require review and a new run rather than
silently reusing mismatched evidence.

Analysis attempts save an atomic checkpoint after each completed model request.
With `--resume`, a failed or interrupted phase rebuilds its output using matching,
hash-verified responses from prior attempts, then requests only the missing or
changed inputs. This works for Aufgaben windows and later assessment batches.
An earlier correction may change the continuation context or known-unit list;
later responses are reused only when their complete prompts still match.
Reused responses record their original generation settings and evidence path.
Failed/incomplete responses are never reused. Prior attempts remain untouched.

Increasing only `max_output_tokens` is compatible with recovery: completed
responses keep their original limit; missing requests use the new limit.
The pre-recovery ten-page pipeline releases at 16,384 and 65,536 tokens are
supported through a versioned code-fingerprint migration. Other code, prompt,
model, source, reasoning or window changes still require a new run.

Run `make test` for offline verification. End-to-end pipeline tests use a fake
model and fake PDF-stage commands; they do not establish model interpretation
quality or trigger a paid full project run.

## Review flags and reasoning settings

Uncertain document-boundary proposals do not require approval to continue. The
proposed spans are used as returned, windows remain inside those spans, and the
uncertainty flags remain available for user review. Source fingerprints and
complete ordered page coverage are still checked. No output is marked reviewed.

Each analysis phase owns THINKING_LEVEL in its schema.py. Effective settings are
recorded in the pipeline binding and included in response-cache keys.

For an existing run that stopped before analysis, after an analysis-code or
thinking-setting update, repeat its exact command/output with
`--resume --accept-analysis-changes`. This explicit option preserves the previous
binding in binding_history and reuses verified PDF outputs. It rejects changed
PDF inputs/model/project settings and cannot be used once analysis has started.


Timing recovery: pass `--resume-from <previous-timing-output-directory>` with a new
`--output` path to the timing-stage command. Each output owns a sibling checkpoint
folder, e.g. `custom.json` uses `custom.checkpoints/`, whose manifest records the
actual trace directory and model/schema settings. If a folder contains multiple
outputs, select the previous output path or its checkpoint directory explicitly.
Only exact requests with hash-verified responses are reused. Failed, interrupted,
null or changed requests go to the provider. A checksum mismatch stops recovery
without rewriting evidence. Older entries without response hashes remain unchanged
but are skipped with a warning; their requests must be made again. Cumulative batch
progress is saved in `<output-stem>.partial.json`, marked incomplete, and the final
output is written at phase end. Recovery is currently for the standalone timing command.


Current timing responses contain only `unit_id`, `answer`, and `citations` inside
`assessments`. `answer` contains the source-supported timing text, or NOT_STATED /
AMBIGUOUS. New free-text timing answers do not generate calculated dates. Legacy
structured artifacts remain readable, but their checkpoints are not reused for the
new response schema. Downstream consequences and applicability extend the saved
artifact without rewriting its timing reviews. Source citation checks still apply.


### Consequences: timed tasks, compact assessments

The current phase uses `timed-task-consequences-v1`. Both providers run the same
German role/context/question prompt. The standalone switch is `--provider sol` or
`--provider gemini` (default). Sol uses `gpt-6.1-sol` with medium reasoning by default;
Gemini uses `gemini-3.5-flash` with MEDIUM thinking. `--reasoning-effort` changes Sol
reasoning only. The sequential pipeline and general analysis CLI expose
`--consequence-provider`, `--consequence-model` and `--consequence-reasoning`.

Every task must already have exactly one timing assessment. Missing, empty or
ambiguous duplicate assessments block submission before any consequence request.
An assessment of `NOT_STATED` is valid: it means there is no stated timing, not
that noncompletion has no consequences. Historical structured timing assessments
remain readable. Tasks, actors, original quotations and timing results are preserved.

Each request contains up to **ten tasks**, batched across extraction windows and
logical documents. Thus 110 tasks require 11 primary requests, with no action-scope
or independent-verification passes. Existing bounded transport retries can repeat
an unchanged failed/incomplete request and consume the same attempt budget.

The model receives only:

- `working_units`: short keys U1…U10, each containing `title`, `actor`, `quote`, `timing`.
- `source_documents`: all supplied BLB pages once, with document labels, titles,
  original PDF page numbers and unchanged text.
- `project_facts`: compact `{id, text}` entries, using source-backed fact text.
- `source_scope`: whether BLB and facts or facts only were supplied.

Raw contract pages are used locally to validate saved facts, but are not copied
into the model request. BLB documents are selected by their explicit `blb` source
role; when supplying a BLB through context CLI arguments, include `--context-type blb`.
No evidence catalog, hashes, offsets, action graph or duplicated window is sent.

The provider's schema is generated for the exact batch keys, including a final
partial batch. Its output is simply:

```json
{"U1": {"classification": "DERIVED", "reasoning": "Bedingte Folge mit Fundstelle und wesentlichen Einschränkungen."}}
```

The only classifications are EXPLICIT, DERIVED and NOT_STATED. DERIVED also covers
an answer combining expressly stated and derived consequences. The reasoning is
the answer itself, including relevant conditions and alternatives. The prompt asks
for a page/fact reference in prose. Those references are **not machine-validated**;
there are no separate citation fields or claim-level source checks for this stage.

Application validation checks exact task mapping, exact two-field objects, allowed
classifications and nonempty reasoning. Invalid batches remain in raw history and
block downstream processing; later independent batches still run. No semantic
repair or verification request is issued. Structural completion does not establish
legal correctness or semantic completeness.

`result.json` is a readable working-unit list with the existing timing and one extra
`consequences` object. `result.audit.json` retains the cumulative pipeline state,
short-label mappings and input/prompt fingerprints. `result.validation.json` retains
findings. Raw provider JSON, native diagnostics, exact prompts and partial results
remain separate. Completed responses are cached before local validation; replay
with the same `--cache-dir --max-new-calls 0` in a new output directory. Provider,
model, reasoning effort, exact prompt and actual per-batch schema bind cache identity.
Old high-effort or retired-schema responses are not relabelled as new responses.

The 9 November 2026 facts protocol v7 (collection schema 11) requests one authoritative
interpretation as cited claim statements. Readable interpretation is assembled locally;
there is no independent model summary. Each bounded target assignment receives the
complete contract text once as lossless, evidence-labelled page segments. Local catalogs
retain hashes and offsets. Duplicate valid IDs and blank uncertainty are normalized
only in derived data; unknown IDs, empty claim evidence and missing passages still block.
Request-specific immutable provider storage, offline artifact replay, targeted explicit
refresh and preflight attempt budgets are covered by network-free regression tests.

The 10 November 2026 project-facts prompt adds three explicitly fictional, complete
input/output examples: conditional credit with deferred payout, an unspecified deadline
trigger and heading-only context, an express cross-document amendment outside the assigned targets, and a
mixed footer separating substantive provisions and included work from contact text
and promotion. Examples use the compact source-page format and current claims schema;
example IDs and contents are explicitly excluded from project evidence. Offline tests
resolve every demonstrated answer against its own sources and keep unknown example
IDs blocking in actual project responses. This revision adds no request, changes no
schema and makes no semantic accuracy claim; the next live run remains to be reviewed.

A further 10 November 2026 purpose review gives current compact project-facts requests
phase-owned source instructions instead of the downstream evidence-catalog preface.
The prompt defines its downstream project-reference purpose, prioritizes complete
meaning over brevity, separates conditional rules from documented choices and events,
and requires representation of every substantive provision within assigned spans.
The credit demonstration now contains independently evidenced entitlement and payout
claims. Five held-out synthetic cases cover project configuration, unresolved choices,
unaccepted offers, unestablished prerequisites and multiple rules sharing one span.
Their semantic expectations remain outside model input; no schema, batch-size or
verifier change is introduced. Structural accounting alone still does not establish
that every provision was correctly interpreted.

## Project-facts short passage references

Project facts now use schema 12 / `source-backed-interpretations-v8`. Gemini receives
short, package-local labels (`S1`, `S2`, …) instead of evidence fingerprints in source
segments and target assignments. The application records the deterministic label map
and binds every batch response to its digest. Exact source fingerprints and quotations
remain in derived citations; original provider responses keep their labels unchanged.
Unknown labels, missing evidence and missing target passages remain blocking. Existing
fingerprint-based artifacts retain their original validation and offline replay semantics.
Full context, 50-passage assignments, claim interpretation and call limits are unchanged.

## Project-facts provision ownership and status

Schema 13 / `source-backed-interpretations-v9` assigns whole mechanical source
provisions to exactly one batch. A 50-passage budget never splits a provision;
oversized units run alone. Every request still contains the complete package.
Facts explicitly return their owning `provision_id`, status and related provisions.
The application checks ownership and each claim's primary evidence, resolves links to canonical facts
after all batches, and passes these fields downstream without changing raw JSON.
Unknown statuses, foreign owners, unsupported owner selections and unresolved links
remain blocking. Old schemas retain their original replay and validation behavior.

Prompt requirements preserve operative qualifications in their original wording,
distinguish observations and offered/agreed content, and require substantive table
fields including quantities and base/position prices. The evaluation set now has
38 held-out cases, including six lexical/status controls for the reviewed failure
modes. These checks are not general semantic verification and make no extra model calls.
