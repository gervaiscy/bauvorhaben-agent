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
The units cache protocol is 111; the PDF extraction protocol is unchanged.

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

The application binds each quote to a unique literal occurrence within the supplied
window of the current document. It does not search contractual context or other
documents to validate a task quote. Exact whitespace and wording must match; no fuzzy
matching, whitespace rewriting or semantic repair is performed. Repeated occurrences
are ambiguous and remain findings. Gemini can include more surrounding original text
to disambiguate them. Cross-page quotes use the document's supplied page order with
one newline between pages; canonical citations retain exact per-page spans.

Every provider row and complete raw response remain unchanged, including invalid
proposals. Derived source locations contain application-owned IDs, page, offsets
and exact quotations. Derived task records use `task-original-quote-v1` and retain
`title`, `actor`, `source_text` and canonical `citations`. Downstream views use the
original `source_text` as `action` and the generated title as `name`; conditions,
timing, consequences and applicability retain their separate stages and source
access. Invalid quotes remain visible with findings and do not trigger an additional
model verification or repair request. Local checks establish source binding and
structure, not semantic extraction completeness or actor correctness.

The same document windows, raw-response storage, request budgeting and offline
cache replay remain available. A distinct prompt/schema identity prevents the
new requests from reusing old extraction answers. Settled project-facts artifacts
remain reusable. Historical ID-selection and action/citation records remain readable
through their existing compatibility paths; they are not new provider output schemas.

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
window selection and five-working-unit batches. Offers and consequences additionally
receive the full text of all project sources in every request, with source/document
identities for cross-document citations.

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


### Consequence source context

Every consequence request includes `source_documents` containing the complete text
of all supplied documents, including other logical documents and context sources.
The integrated pipeline supplies contracts, amendments and BLB sources to this phase.
The local `source_pages` excerpt and window metadata identify the target tasks; they
do not limit consequence evidence. Batches still contain at most five working units.
All citations require `source_key`, `document_id`, `pdf_page` and an unchanged quote,
and are checked against the same complete context. Duplicate source/document IDs
with conflicting content are rejected before consequence calls.

The German prompt distinguishes EXPLICIT, DERIVED, MIXED and NOT_STATED, preserves
alternatives and requires a brief explanation of each derived dependency. Standalone
callers must supply all additional sources through `context_documents` (or matching
`--context-pages` and `--context-documents`); unsupplied sources cannot be inferred.

### Consequence extraction (schema 18, protocol v48)

Each batch of at most five tasks is sent once as `PAGE_CONSEQUENCES`, with all
original supplied project documents and amendments in `source_documents` and the
full source-bound evidence catalog. There is no source-only assessment, comparison
request, correction request, or reverification. There is no model-based citation
repair for this stage either: invalid references are recorded as findings without
another Gemini call. Existing transport retries may resend the unchanged request
when the provider fails or returns an incomplete generation.

The extraction prompt asks Gemini to inspect the complete document context for
all consequences of each supplied task being omitted or late, check overriding
agreements, and preserve conditions, exceptions, alternatives and modality. It
separates explicit consequences from supported deductions and excludes ordinary
responsibilities, included prices, positive benefits and unsupported inverse
reasoning from failure consequences. NOT_STATED remains a valid result when no
consequence is documented. The prompt requires exactly one assessment for each
provided task ID; it must not invent additional tasks.

Each consequence retains its ID, classification, claim, failure trigger,
conditions, exceptions, alternatives, citations, cited premises and reasoning.
Original answers remain unchanged in `gemini_responses` and review `gemini`;
canonical evidence bindings are stored separately in derived output. No
`consequence_verification` state or model-verified label is produced. The
standalone `*.consequences-only.json` contains the extracted assessments.
Other stages retain their existing behavior.

The generic controls are in `evaluation/consequences/cases.json` at repository
root. Offline tests verify complete context delivery, five-task batching,
unchanged responses, local evidence findings without follow-up calls and exact
request recovery. These tests do not measure Gemini's interpretation accuracy.

The units cache protocol is bumped. All prompt files participate in fingerprints;
historical outputs are not rewritten or silently upgraded. Current stage entry
points require schema 18 / v48 inputs. Completed batches in a failed current-
protocol attempt can be replayed by the existing exact-request recovery mechanism.

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
