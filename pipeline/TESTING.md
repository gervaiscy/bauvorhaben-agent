# Verification record — 11 October 2026

| Suite | Local result |
| --- | --- |
| Enrichment, source integrity, compiler, batching, semantic review and CLI boundaries | 71 passed |
| Lossless Working Unit extraction | 10 passed |
| PDF extraction, real CropBox rasterization and page grouping | 21 passed |
| Total | **102 passed** |

Run `python run_tests.py` with installed dependencies. The same final suite is also run in Google Cloud Shell; see the final test log and run-summary.json for confirmation.

The tests cover complete source membership, snapshot drift, invalid/exact/uniquely realigned quotations,
checkpoint tampering and replay, bounded scope ownership, schema forwarding, interrupted streams,
API retry boundaries, negative dependency lags, cycles, alternative selection, date propagation,
actual-event separation, stale publication approval, HTML escaping, required timing-rule preservation,
review attribution/fingerprint binding, question-resolution auditing, and CLI overwrite protection.

## Live model run

The earlier single-request approach failed with Vertex resource limits. The approved batch run completed
20 detail batches for all 238 inputs, then 10 scope chunks and a timing/graph pass. Model responses,
rejected attempts and partial streams remain preserved. A structurally valid model result still had
semantic mistakes and no date rules. This prompted the new empty-timing gate and a separate,
source-bound review rather than publication of the raw result.

The reviewed copy contains 231 canonical units: all 238 input members, 13 duplicate merges, and
6 added milestones/options. Eleven externally required timing rules are preserved. Review corrections
cover financing addenda, the contractor's security before the second payment, offer exclusions,
conditional credit, KfW conditions, utility alternatives, electrical/wall planning, historical events,
unsupported inferred sequences, and payment milestones versus invoice dates.

The run uses the existing project chatyamo-pushnotification, global, gemini-2.5-flash. The user confirmed
2025-07-03 as a planning baseline; no actual construction-start event or branch choice was invented.
No customer publication, IAM change or secret rotation was performed.

## Source and presentation limits

All 238 source references verify against the matching archived contract and Cloud Shell BLB transcripts.
Four invented BLB sub-line IDs were corrected in a copy with an explicit audit. Exact quotation checks
prove source identity, not semantic entailment or PDF extraction completeness. Sources, original
responses and reviewer changes remain separate.

The supplied export had 91 review flags and 86 dependency references that did not directly match its
exported IDs. Those old enrichment dates/statuses are not reused as customer facts.

HTML was checked for escaping, source references, internal links and report consistency. Browser
rendering of the local HTML was unavailable under browser security controls; no visual-render QA is
claimed. The Cloud Shell execution result is separately captured as a screenshot.

The output is a source-grounded **review draft**. Open project facts remain explicit. The full task
register still requires domain review before publication; automated tests cannot certify every legal
or construction interpretation. The publication digest is an audit binding, not authentication.

## Document-only revision — 12 October 2026

`python run_tests.py`: **112 passing locally** (81 report/boundary tests including
10 document-only tests, 10 Working Unit tests, 21 PDF tests).

The added tests cover ignoring live construction events, preserving relative deadlines,
conditional rules without status questions, preventing optional work from becoming mandatory,
complete review coverage, input/review tampering, exact quotes for UNKNOWN scope, sourced and
labelled consequences, HTML escaping, exact-artifact approval, and the CLI output boundary.

This revision has not been rerun in Google Cloud Shell. No model call is needed to reproduce
the reviewed document-only output. Passing tests establish these software properties, not
exhaustive semantic completeness, professional approval or reliability across unseen contracts.

Derived-planning addition: six further tests cover the longer-duration backward chain,
12-/6-week alternatives, baseline changes, rejection of actual-event rules, unknown baselines,
source tampering, and visible calendar dates alongside the relative contractual wording.

All-unit timing assessment: fourteen further tests cover complete classification, sequence-only
bounds, conditional 12-/6-week branches, unrooted event triggers, cycles, missing/duplicate IDs,
invalid source quotes, excluded/optional scope, ignoring actual progress, resumable batches
and complete timing integration into the customer report. Additional cases reject empty event
anchors and fabricated zero-day durations, retain undated event triggers without a stated delay,
and preserve rejected attempts across a resumed run. They also cover backward-only resolution
of construction-start event names, inherited optional scope, and avoiding false due dates at
the start of a forward date range. Total: 132 passing locally.

Interrupted-run recovery adds six tests: partial-text exclusion from model coverage,
numeric retry ordering and immutable evidence, local structural quarantine, stale
review rejection, publication blocking for incomplete coverage, and a targeted
subset retry with full source context and non-overwriting supplement import.
Total: **138 passing locally** (107 root, 10 Working Unit, 21 PDF).
