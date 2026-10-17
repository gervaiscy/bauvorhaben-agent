# Bauvo: validated extraction and customer report pipeline

## Current MVP: documents only (12 October 2026)

Use `document_report.py` for the customer view. It reports the contractual task,
responsible party, performer/organizer, relative timing, conditions and sourced consequences.
It does not consume construction-progress events, declare work completed/overdue, or ask
whether an event has happened. Optional choices remain choices. Missing drawings or annexes
are document gaps; missing actual start dates are not.

```sh
python document_report.py --input /path/to/input-snapshot.json \
  --proposal /path/to/reconciliation.json --review /path/to/document-review.json \
  --output-dir /path/to/new-document-output
```

The evidence-bound review packet supplies reviewed roles, relative deadlines, consequences
and document-scope categories for every canonical unit. The renderer validates complete
coverage, source quotes, fingerprints and HTML escaping. It always creates a review draft.
Consequences distinguish contractual text from planning implications. No separate consequence
is invented when the cited clauses do not state one.

For this project the separate 12 October review has 230 canonical entries preserving all 238
input members, 40 corrected existing records, four added obligations and five further duplicate
merges. All 72 old questions were triaged; no project-progress questions are sent to the customer.
The code and 112 tests were run locally for this revision; the cloud evidence below refers to
the earlier run. The PDF inventory is 19 contract pages and 68 BLB pages.

The review packet is project-specific data, not a reusable hardcoded rulebook. New contracts
need their own source review. This revision does not establish unattended semantic accuracy
on new contracts. `publication_check` binds an external approval/visual-QA record to the exact
report and rendered HTML; it is an audit gate, not reviewer authentication. The current report
has neither independent professional approval nor completed browser rendering QA.

The scheduling/live-event tools below remain available as legacy/internal projections. They
are not the first-MVP customer view and their diagnostic questions should not be published.

## Earlier cloud run

**Live verification:** the approved batch refactor completed all 20 detail batches and 10 scope-review chunks in Google Cloud Shell. The first structurally valid timing result had dropped its date rules and still contained semantic errors. An explicit source-bound review corrected these in a separate copy: 238 input members, 231 canonical units (13 duplicate merges and 6 additional planning milestones/options), 11 required rules, and no structural validation errors. It remains a review draft, not an automatically approved customer report. The final suite contains 102 tests; see `TESTING.md` and run evidence.

This package preserves the lossless extractor and adds a **separate, validated report projection**.
Your original ZIPs are preserved. The live verification uses an isolated Cloud Shell directory.

## What changed

- PDF sections are bound to the exact lines snapshot with a SHA-256 digest.
- Working Unit extraction records the transcription digest for each document.
- Enrichment checks saved excerpts against current line text. Unique exact matches can repair
  shifted IDs; ambiguous or missing matches remain unresolved. Lookup failure cannot mean exclusion.
- A new cross-document reconciliation pass sees the contract, addenda, offers, BLB, all units,
  and source text together. It proposes canonical units, explicit dependencies and typed date rules.
- Unique exact model quotations can have their line range realigned within the same document; raw responses and a separate before/after audit remain unchanged. Ambiguous or altered quotations still fail.
- Global review requests use an explicit JSON schema, including nonnegative dependency lags; Python enforces assigned scope IDs. The schema is saved beside each live request; semantic and source validation still run afterward.
- Python validates references, exact citation quotes, complete membership, conditional evidence,
  dependency cycles, branch choices and typed offsets before computing any date.
- Backward propagation tightens prerequisite targets. Contractual deadlines remain separate from
  propagated planning targets. Conservative and optimistic duration scenarios are separate.
- Actual events are explicit, verified inputs. A planned construction-start date never starts
  the 46-week contractual completion clock.
- HTML escapes source/model text, includes evidence and questions, and is a draft until a review
  record is bound to the exact validated result. Blocked reports do not produce HTML.

This is **not** a guarantee that Gemini understands every clause correctly. Exact quote validation
proves citation integrity, not semantic entailment. A knowledgeable reviewer must check project
scope, amendments, branch choices and the source interpretation before customer publication.
The date engine uses elapsed calendar days/weeks and calendar-month clamping; it does not decide
legal deadline extensions for weekends/holidays, contract conclusion, weather entitlement or damages.

## Included sources and compatibility

- `pdf-extractor/`: two-file Gemini visible-page transcription workflow.
- `extraction_support/`: document-boundary proposals, source review and JSON helpers.
- `working-units/`: the earlier `bauvo-milestone-source-2026-10-04.zip` found beside your run3
  files, with source-snapshot verification added. The newly supplied archives did not contain this stage.
- `enrich_working_units.py`: based on your October 11 archive (the October 10 archive is older).
  Its existing enrichment output remains an internal analysis artifact, not the publication input.
- `customer_report.py`: new replacement for the missing report-input generator.
  It accepts the manifest and lossless assembled files, NOT the old flattened 238-unit JSON.
- `baseline-audit.json`: counts from the supplied old export; no regenerated customer facts.

Original Working Units, raw model responses, source IDs and excerpts are preserved. Canonical
merging occurs only in the separate proposal/report, with every input unit mapped exactly once.

## Cloud Shell setup

Python 3.10+ and your existing authenticated Google Cloud environment are required.
Use the project, location and model already approved for your deployment. Locations are never
silently changed to global.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e ./working-units
python run_tests.py
```

Do not rerun PDF extraction if you have the matching sections, lines and assembled Working Units.
Keep those three outputs together. A checksum mismatch requires matching inputs or regeneration;
changing the checksum merely to silence a mismatch is not a repair.

For a fresh extraction run from the repository root:

```sh
make fresh-extract CASE=sample
```

This calls `pdf-extractor/ocr_pipeline.py`, rendering each PDF page to a PNG
and sending it individually to Gemini 3.5 Flash with minimal reasoning. Review
`pages.json` before running working-unit extraction. See the repository README
for configuration, document boundaries and source approval.


Source findings require review against the rendered PDF. Use the root commands
`make open-extraction`, `make resolve-extraction` and `make approve-extraction`
to inspect and record review. Existing section-based outputs remain historical
artifacts; new extraction runs use `pages.json` and `documents.json`.

## Manifest

Copy `manifest.example.json`; paths are relative to the manifest. The example date is not an
assertion about legal contract conclusion. `contract_signed_verified` defaults to false. Set it
true only after checking the intended contractual time anchor against the signed documents.

`report_settings.events` contains actual occurrences, not planned dates:

```json
{
  "construction_start": {
    "date": "2026-07-03",
    "verified": true,
    "evidence": "Reference to the confirmed site diary / contractor notice"
  }
}
```

`report_settings.selections` contains canonical unit IDs of explicitly chosen alternatives.
Run once with `[]` to discover unresolved choices, then record the real choice and rerun proposal
creation (settings changes invalidate old proposals). Never select every financing/utilities branch.

## Recommended: resumable batch reconciliation

```sh
python batched_report.py --manifest input-manifest.json --run-dir run-batched \
  --project YOUR_PROJECT --location YOUR_LOCATION --model gemini-2.5-flash \
  --batch-size 12 --workers 2 --thinking-budget 1024
```

This is a separate report projection; the original extraction stages stay intact. Every input
receives a stable canonical ID before batching. Each batch sees the full source named `contract`,
its relevant source sections with neighboring lines, offers, settings and the global unit registry.
Two global review stages see all source text and all batch results. Scope/amendment reconciliation is bounded to 24 target units per request, with full project context, and checkpointed first; timing/graph review follows. They propose only corrections,
evidenced merges, additional milestones and date rules. The normal full-report validator still
runs after that review. No partial batch is a customer deliverable. Scope updates for other known units are retained in an unassigned-updates audit and not applied in that chunk; each unit receives its own assigned review.

To resume a failed run, repeat exactly the same command and run directory. Successful checkpoints
are revalidated and reused; only missing batches and the global review are requested again.
Source/settings/model/prompt changes require a new run directory. Do not run two processes against
the same directory concurrently. Each invocation permits at most two detail-batch validation attempts and three attempts per scope-review chunk or timing-review stage. Complete prior patches resume after interruption. Original prompts, responses, partial streams and errors are retained.
An API failure stops submission of new batches after current in-flight work finishes.

The final HTML filename includes the validated review digest and remains a draft. Use the exact
filename reported by a successful invocation, not an arbitrary old HTML file from the directory.
The returned `reconciliation.json` can also be compiled using `customer_report.py` and a review
record under the existing publication workflow. Trace and checkpoint files contain private source
material and are internal developer artifacts.

## Generate and validate a report proposal

```sh
python customer_report.py --manifest input-manifest.json --propose \
  --project YOUR_PROJECT --location YOUR_LOCATION --model YOUR_MODEL \
  --proposal run/reconciliation.json --output run/validated-report.json \
  --html run/customer-draft.html
```

The reconciliation request has all source context and can be large. The adapter streams progress, uses a 600-second HTTP/server request timeout, and retries transient service status codes only before any response text arrives, up to five attempts. Non-STOP model completion and interrupted streams are rejected, with partial text retained for diagnosis. A partial text checkpoint is written about every 30 seconds; it is never treated as a valid proposal. For an overall process bound in Cloud Shell, prefix the command with `timeout 940s`. Its output budget defaults to 65,535 tokens; use `--max-output-tokens` if your selected model has a different limit. It creates a prompt trace
and preserves the returned value before validation. A truncated/malformed/incomplete answer is
blocked, never published. This pass is intended for small project bundles like this one; larger
projects need bounded retrieval/reconciliation before increasing scale. The prompt projection omits repeated section text and old model reviews/date guesses while retaining every source line, offer and input identity. The full input remains fingerprinted. An optional `--thinking-budget 4096` can bound thinking on models that support it. No automatic semantic retry
or undocumented model switch is performed.

The proposal uses the JSON contract in `RECONCILE` in customer_report.py. It contains:

- `units`: canonical units with original members, source quotes, scope and ALL/ANY requirements;
- `rules`: signed offsets and named anchors, never model-calculated dates;
- `unresolved_questions`: project facts or interpretations requiring clarification.

For example, eight weeks before an anchor is `{ "unit": "weeks", "min": -8, "max": -8 }`.
A 4–8-week lag on a dependency is `{ "min": 28, "max": 56 }` in `lag_days`.
Calendar months remain months. A rule anchored to `event:construction_start` uses the manifest event;
`unit:start` is deliberately not treated as an actual event.

`PLANNING_TARGET` rules describe a planning scenario, not a legally asserted due date.
Do not give arbitrary actions a 10-week rule. The electrical-plan regression test demonstrates
why dates must propagate through the actual planning chain.

Correct semantic errors in a **copy** of the saved proposal while preserving source citations and
all member mappings, then compile without another model call:

```sh
python customer_report.py --manifest input-manifest.json \
  --proposal run/reconciliation-reviewed.json --output run/validated-report.json \
  --html run/customer-draft-v2.html
```

HTML paths must be new: the CLI rejects an existing HTML path so a failed rerun cannot leave an old report looking current.

Exit code 2 means blocked. Inspect `errors`; no customer HTML is written. Source lookup failures,
missing members, broken references, cycles and unsupported conditional scope must be corrected.
`questions` are explicitly unresolved project facts and remain visible in the draft. Do not turn
these into confirmed obligations just to obtain dates.

## Apply a source-bound semantic review

The live run demonstrated that valid JSON and exact quotations do not ensure correct interpretation.
`apply_review.py` applies an explicit review patch to a copy, checks exact input/proposal fingerprints,
preserves every original member, and validates externally specified required rules. Removed questions
need an explicit resolution reason. The raw model response is never overwritten. The review is attributed
to its actual author and does **not** grant publication approval.

```sh
python apply_review.py --manifest portable-manifest.json \
  --proposal run-bounded/reconciliation.json --review-patch project-review.json \
  --output-dir customer-reviewed-new
```

The private run bundle contains the exact project-specific patch. It is not a universal business-rule
file: source/proposal changes invalidate it. A successful run writes `customer-summary.html`, the
linked `customer-draft.html` register, validated JSON, corrected proposal and review audit. Share both
HTML files together when distributing a review copy; source traces and internal JSON are developer data.

The batch CLI now rejects a timing stage with no date rules, including old empty-rule checkpoints.
Do not remove the gate to reuse such a checkpoint. Use its raw proposal as the base of an explicit
review, or start a new run for regeneration. `required_rules` in a review binds the full expected
rule objects, preventing later repairs from silently dropping or altering the reviewed timing.
The summary shows the conservative scenario, and the register also shows optimistic targets.
Actual construction progress remains separate from either scenario.

## Legacy scheduled-report approval (not the document-only MVP)

After reviewing the validated facts, dates, scope and unresolved questions, save a review record:

```json
{
  "review_digest": "COPY_FROM_validated-report.json",
  "reviewed_by": "Reviewer name",
  "reviewed_at": "2026-10-11",
  "approved": true
}
```

Then rerun compilation with the same inputs and `--review run/review.json` and a new HTML path.
A mismatching digest is rejected. The review file is an audit record, not authentication or an
access-control mechanism. A changed source, proposal, settings or result requires a new review.
Use the HTML as the customer deliverable; do not send raw prompts, raw responses or internal JSON.

## Tests and known limits

```sh
python run_tests.py
```

The tests include CLI/file boundaries, PDF crop rendering, source-ID drift, checksum mismatches,
exact quote validation, conditional scope, canonical member coverage, graph cycles, alternative
choices, backward propagation, event/date separation, month-end arithmetic, HTML escaping,
and stale approvals. They use synthetic/anonymized fixtures and fake model responses.

The supplied real export has 238 units, 91 review flags and 86 dependency references that do not
match its exported IDs. It is retained only as a baseline; it cannot reconstruct the complete,
verified source registry needed by the new stage. The live batch run and subsequent source-bound review have now been completed in Google Cloud Shell. Matching source snapshots, prompts, rejected responses, validated checkpoints and review changes are preserved in the separate private evidence bundle. This is evidence of a tested draft workflow, not unattended semantic accuracy.

For the model output limit, consult the [official Gemini 2.5 Flash model documentation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/2-5-flash).

## Derived planning deadlines — 12 October 2026

The document-only review now optionally accepts `planning`: a boundary anchor, named scenarios
with explicit rule IDs and hypothetical option branches, and source-backed display aliases.
`document_planning.py` reuses the validated deterministic date engine with actual events cleared.
Only planning rules rooted in the confirmed signing baseline/derived anchors are permitted.
Longer cited dependency durations give the earlier preparation deadline. Branches are displayed
separately without recording a customer choice. Relative contractual wording remains visible.

For this project: 3 July 2025 + 12 months = 3 July 2026 start target for price protection.
Utilities at minus 12 weeks: 10 April 2026; alternative installation at minus 6 weeks:
22 May 2026. Provider confirmation at the 12-week checkpoint is labelled a planning interpretation.
The complete-preconditions planning target is 24 April 2026; the electrical/wall planning
chain reserves up to eight weeks earlier, yielding 27 February 2026. These are scenario targets
without extra buffer, not proof of progress, default or guaranteed price protection.

Use the new `bauvo-planning-report-2026-10-12` input/proposal/review files together. The prior
report remains preserved. Source-bound review and approval fingerprints change with the scenario.

## All-unit timing assessment with Gemini and Python

`timing_assessment.py` replaces the manually selected timing-rule list with an assessment of
**every** canonical working unit. It uses the existing Gemini/Vertex client. No source documents
are executed as instructions and no construction-progress events are consumed.

```sh
python timing_assessment.py --input input-snapshot.json \
  --proposal reconciliation.json --review document-review.json \
  --output-dir timing-run --project YOUR_EXISTING_GOOGLE_PROJECT \
  --location global --model gemini-2.5-flash --batch-size 6 --workers 4 \
  --thinking-budget 2048

python document_report.py --input input-snapshot.json \
  --proposal reconciliation.json --review document-review.json \
  --timing-result timing-run/timing-result.json --output-dir new-customer-output
```

The model returns RULES_FOUND, NO_TIMING_RULE_FOUND, EXCLUDED, DOCUMENT_RECORD or AMBIGUOUS
for each unit, with complete relative wording, source citations and reasoning. It extracts
anchors, BEFORE/AFTER offsets, NO_LATER_THAN sequence constraints and conditional alternatives.
EVENT_TRIGGER with a null offset preserves an actual trigger with no stated delay; it never
means a zero-day deadline and cannot produce a signing-derived date.
It cannot supply calculated calendar dates. Every batch is coverage-checked and up to two
schema/source repairs are attempted. The original prompts, raw responses and partial text stay
available. Resume requires the same input/proposal/review/prompt/model/batch-size fingerprints.
Failed attempts retain their original filenames; resumed attempts append new evidence.

Python checks exact quotes, referenced IDs, complete coverage and cycles, then traces each
candidate through the graph to the signing baseline and reviewed price-protection boundary.
The final classification is DERIVABLE, CONDITIONAL or NOT_DERIVABLE_FROM_SIGNING. Every unit
retains the rule, missing anchors, source evidence and calculation path. Unselected alternatives
are hypothetical branches; incompatible branch choices are not combined. Multiple applicable
bounds are preserved, not averaged. All compatible limits must be respected.

A sequence-only edge has `offset: null`, not a fictional zero-day preparation time. Its result
is explicitly a no-later-than planning bound with no preparation allowance. Actual-event rules
(e.g. notice receipt or actual construction start) remain relative even if there is a planned
start date. No generic task duration is supplied. Dates remain review-draft planning dates.

Exact construction-start event names (for example `event:Baubeginn`) are resolved to the
hypothetical start target only for BEFORE/NO_LATER_THAN relations. The original anchor and
resolution are retained in the calculation chain. AFTER rules keep the actual-event anchor.
OPTION scope is inherited from the reviewed unit even when the model omits condition text;
the same scope condition propagates to dependent dates. This never selects the option.
Forward date ranges are displayed as ranges, not as a due date on their earliest day.

To recalculate a saved assessment without a model call:

```sh
python timing_assessment.py --input input-snapshot.json --proposal reconciliation.json \
  --review document-review.json --assessments timing-run/assessments.json \
  --output-dir recalculated-timing
```

Exact citation checks establish provenance, not interpretation accuracy. The timing model may
miss a clause or misinterpret a cited condition; AMBIGUOUS records remain visible for review.
Full 230-unit coverage means every unit was assessed, not that all clauses were independently
certified complete. Independent professional/publication review remains a separate step.

## Interrupted all-unit timing recovery

`timing_recovery.py` imports complete responses from the original run without rewriting
raw evidence. It binds the run and corrections to input/proposal/review fingerprints.
Missing or structurally rejected responses become explicit local gaps, never invented
deadlines or assertions that the contract contains no timing rule. Cyclic corrected
graphs are rejected, not silently pruned. Source-specific semantic decisions belong
in a separately reviewed JSON data file.

`timing_assessment.py --requested-ids missing-unit-ids.json` retries only a listed
subset while retaining the complete source and unit registry. It deliberately does
not claim a complete report. `timing_recovery.py --supplement /path/to/subset-run`
can combine that run with the original evidence; it cannot overwrite existing responses.
The recovery CLI takes `--run`, `--input`, `--proposal`, `--review`, `--decisions` and
a fresh `--output-dir`. Generate HTML afterward with `document_report.py --timing-result`.

Latest downloaded run: 224 complete Gemini responses out of 230, 57 source-review
corrections/withheld assessments, 34 unresolved timing assessments including six
missing responses. 138 software tests pass locally. The exact current evidence is
in the separate all-unit timing recovery bundle; no unattended publication claimed.
