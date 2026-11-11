# Offline replay and explicit provider-call limits

Development starts offline. The analysis CLI and individual stage CLIs default to
`--max-new-calls 0`. A saved response is reused regardless of whether its content
passes application validation. A cache miss cannot construct a Gemini client or
contact the provider until a positive limit is supplied.

## Replay an existing facts result

This rebuilds derived data from preserved claims and original sources, without
submitting the new prompt or pretending that an old answer used it:

```sh
PYTHONPATH=pipeline/working-units/src .venv/bin/python \
  -m bauvo_working_units.cli \
  --pages /accepted/contract/pages.json \
  --documents /accepted/contract/documents.json \
  --stop-after project-facts \
  --replay-project-facts /previous/facts/result.json \
  --output /new/offline-review/result.json
```

No project, credentials or network are needed. Supported saved interpretation collections
are schemas 8–15. Original source fingerprints and the historical artifact are
validated before replay. Provider JSON, exclusions, earlier findings and the input
artifact fingerprint remain available. Historical fingerprint-based responses replay
as schema 11; short-label responses retain schema 12 or 13 and their original label map.
Schema-13 replay also retains and checks provision ownership, status and canonical links. Old
interpretation paragraphs remain in raw history; downstream interpretation comes
only from the cited claim statements. The output can be passed to `--project-facts`.

Repeated valid evidence IDs are deduplicated in derived citations. In historical
claim schemas, blank uncertainty becomes null. Raw JSON is unchanged. For schemas
8–13, unknown IDs, empty claim evidence, invalid claims, conflicting inclusion/exclusion
and missing target passages still block downstream analysis. Schemas 14–15 use the
simpler rights validation described below. Replay never fabricates missing statements
or evidence.
`recorded` means structural completion, not verified semantic correctness.

## Current extraction: homeowner contractual position

New project-facts runs use schema 15 / `contractual-rights-v3`. The German prompt
asks a construction-contract lawyer: **Welche Regelungen enthalten die Vertragsunterlagen
für mein Bauvorhaben, und was bedeuten sie für mich als Bauherrn?**
It extracts services, rights and conditional entitlements owed to the homeowner,
standalone homeowner duties, restrictions and permissions granted to others.
Rules governing formation, amendments, binding declarations, representation and
formal requirements are included even without an immediate homeowner entitlement
or an action to perform. The company or party scope stays exactly as stated.
Such rules remain extractable from offer footers alongside contact data.
Offers require evidence of incorporation/acceptance; document presence alone is
insufficient. Neighbouring rules do not automatically establish conditions or
consideration for another right.

Gemini returns only:

```json
{
  "ansprueche": [
    {
      "aussage": "Der Bauherr hat Anspruch auf die vereinbarte Leistung unter den genannten Voraussetzungen.",
      "belege": ["S1", "S2"]
    }
  ]
}
```

The existing list name `ansprueche` contains all these kinds of contractual provision;
duties and permissions are stated directly, without rewriting them as homeowner rights.
Current extraction includes only documented rules and supported service components.
Unestablished possible rights are omitted. Documented conditional rules
remain extractable without proof of their actual fulfilment. There is no separate
uncertainty field in new responses, derived rights or downstream rights projections. Historical
schema-14 / `contractual-rights-v1` artifacts keep their original `unklarheit` and
derived uncertainty on load/replay, without rewriting their saved answers.

Every request contains the complete original contract package, including amendments.
The application assigns a bounded portion for extraction, aiming for 50 source spans
without cutting a mechanically identified section/paragraph/table row. This grouping
stays internal: Gemini does not receive a provision catalog or return owner IDs,
status enums, relationship links, nested claims or passage exclusions. The seven
fictional examples are ordinary German source/result text, not JSON demonstrations.

Short source labels remain. The application generates internal result IDs and resolves
labels to original documents, PDF pages, fingerprints, offsets and verbatim quotations.
It preserves the provider's statement and original JSON; only repeated references in
derived citations are deduplicated. Unknown references, empty evidence, invalid
response structure or a missing provider response block downstream processing.
No per-passage inclusion/exclusion accounting or semantic repair call is performed.
An empty rights list is valid. All original source text remains available downstream.

Evidence may come entirely from outside the assigned extraction batch if its labels
resolve within the full supplied contract package. There is no per-fact batch-overlap
requirement. Collections record `validation_protocol: "full-contract-evidence-v1"`.
For older rights artifacts, explicit offline replay verifies the original saved
derivation first, recomputes local validation without the retired overlap check and
preserves the prior findings and response-validation results in `validation_history`.
Other validation failures remain active. Raw responses, evidence selections, original
sources and fact IDs are preserved, and this change requires no new Gemini requests.

`processing` distinguishes planned/completed batches and explicitly records
`semantic_completeness: "not_verified"`. `recorded` means responses passed structural
checks, not that every right was found or its legal interpretation was verified.
Downstream input carries `extraction_scope: "homeowner_contractual_position"`: omission
from this list does not establish that a feature, duty or rule is absent,
excluded or inapplicable.

Schema-15 artifacts support source-bound loading and offline replay with the same
command above. Older schemas and schema-15 / `contractual-rights-v2` retain their
original extraction and validation semantics; replay never relabels a rights-only
extraction as a contractual-position extraction. Saved provider responses are independent
of local validation. Re-running with the same sources, request settings and response
store rebinds the saved answers locally without new calls. The new prompt/schema is
a different provider request, so old clause-index answers cannot satisfy it. Accepted
OCR and document grouping remain reusable.

## Short passage labels in project facts

Project-facts requests introduced in schema 12 use `source-backed-interpretations-v8` and
`source-labels-v1`. The complete contract package is still supplied with each
assignment of at most 50 passages. Gemini selects `S1`, `S2`, etc. from the source
segments, including supporting passages outside the assigned batch. Labels follow
the entire package's source order and are identical across its batches. They are
local to that source package, not globally unique document identifiers.

Schema-12 and schema-13 results store `reference_map` once for the full package, mapping each
short label to its original evidence fingerprint. Every response records the map's
SHA-256. Loading or replaying an artifact reconstructs the map from the original
pages and checks both the map and each response's binding. The original fingerprint,
page hash, document identity, offsets and exact quote remain in derived citations.
Raw response files and each record's `gemini` value retain the labels exactly as
returned. Resolution never edits statements or raw JSON, guesses similar labels,
or accepts internal fingerprints in place of labels for new responses.

The prompt examples use the same short-label format. A valid label establishes a
source selection, not whether the interpretation is supported by that selection.

This is a different provider request from the old fingerprint-based prompt, so old
responses are not reused as new label-based answers. They remain readable and can
be replayed offline under their original reference semantics. Replay does not fix
an old mistyped fingerprint. Reuse accepted OCR and grouping files through the
analysis CLI when generating new facts; no PDF processing needs to be repeated.

## Historical provision ownership and explicit status

Historical schema-13 requests use `source-backed-interpretations-v9` and
`source-provisions-v1`. Source units follow numbered sections/provisions, paragraphs
and numbered table positions. This is mechanical grouping, not a determination of
legal meaning. Sections stay together across paragraphs/pages; units can contain
several independently reviewable facts. Every original passage remains available.

The planner assigns each unit (`P1`, `P2`, …) to exactly one batch. It aims for 50
passages per batch and never cuts a unit to fit that budget: an oversized unit runs
alone. Full contract context, short `S` labels and all evidence remain in every
request. Because boundaries differ, plan new calls instead of assuming the old
batch count or reusing the previous call ceiling without retry allowance.

Each fact returns `provision_id`, `status`, `related_provision_ids`, `claims`,
`uncertainty` and `zitate`. Ownership must belong to that batch. Every claim must
select evidence from its own source unit, with outside qualifications cited in
addition; appending an independently rewritten claim citing only another unit blocks.
Status is one of `CONTRACT_RULE`, `OFFERED`,
`DOCUMENTED_AGREEMENT`, `DOCUMENT_OBSERVATION` or `UNRESOLVED`; the last requires a
nonempty explanation of the uncertainty. A fact may cite outside conditions or
amendments, but the prompt tells it to reference separately owned rules rather than
independently rewriting them.

After all batch responses arrive, the application resolves `related_provision_ids`
to `related_fact_ids`. Unknown/self references and missing referenced facts block;
forward references require no extra provider call. The saved provision catalog and
each response's catalog digest are rechecked from original sources on load/replay.
Downstream prompts receive status, ownership and resolved links alongside claims.
Raw JSON remains untouched, including failed and inconsistent answers.

The prompt requires original wording for meaning-changing qualifications and full
table item/quantity/price/amount information. Six held-out interpretation controls
exercise qualifications, unassigned payment notes, postal exceptions, offer status,
base prices and signature observations. Their lexical/status checks report specific
failures without rewriting output or supplying expectations to Gemini. They do not
prove general semantic correctness. In particular, local validation cannot establish
whether evidence really supports acceptance or whether a qualification was omitted.

Old artifacts remain readable and replay under their original semantics. Replay
does not invent statuses/ownership for old model answers. A new extraction is a new
provider request; existing OCR and document grouping remain reusable.

## Plan before permitting new calls

```sh
PYTHONPATH=pipeline/working-units/src .venv/bin/python \
  -m bauvo_working_units.cli \
  --pages /accepted/contract/pages.json \
  --documents /accepted/contract/documents.json \
  --stop-after project-facts --project YOUR_PROJECT \
  --cache-dir /shared/provider-responses \
  --plan-only --output /new/review/result.json
```

`result.request-plan.json` reports request SHA-256 IDs, saved responses, planned new
calls and the remaining limit. A facts-stage plan covers all target batches before
any provider request. For later stages whose inputs depend on earlier answers,
requests are planned when those inputs exist; the plan is not a prediction of
unknown future tasks. Plan-only creates no result, raw response or pass snapshots.
An unchanged output path can therefore be used for the subsequent execution.

To permit generation, omit `--plan-only` and add an explicit `--max-new-calls N`.
The limit counts actual provider attempts, including the single transport/truncation
retry. The known minimum plan must fit the remaining limit before its first call.
Retries can consume the remaining allowance and leave a preserved partial result;
they never raise the limit. No semantic retry or verifier call is added.

After a local validation change, rerun with the same store and the default zero
limit. Completed responses replay and local validation runs again. A changed prompt,
model, project, location, response schema or generation setting is a different
provider request and requires a new plan. Unrelated stage schemas, application
validators and global protocol versions do not invalidate a request's response.

## Regenerate one genuinely incomplete answer

A completed but invalid answer remains stored and blocking. It is never silently
regenerated. If it needs a new model answer, select its request SHA from the plan:

```sh
# Same source/model/store arguments as the original request, with new output paths:
... --refresh-request REQUEST_SHA256 --max-new-calls 1 --output /new/retry/result.json
```

Only selected requests are regenerated; the other responses replay. Original
records are immutable. New versions live under `versions/REQUEST_SHA/`; a separate
`heads/` selection identifies the active version. Raw responses, request settings,
checksums and diagnostics are retained. If the selected answer truncates, a limit
of one prevents a second attempt. Refresh is an explicit live action, not part of
ordinary replay.

## All analysis stages and the PDF pipeline

Offers, timing, consequences and applicability CLI commands also accept
`--cache-dir` and `--max-new-calls`, defaulting to offline. The general CLI can run
through all stages using the same store and one budget.

The PDF-to-applicability pipeline uses one locked attempt ledger shared across OCR,
document-boundary and analysis calls, including subprocesses and retries. It also
defaults to zero new calls. Each invocation writes its own ledger under
`call-budgets/`; earlier ledgers and attempt evidence are preserved. Completed PDF
stages and analysis responses remain resumable under their source/code bindings.
Existing accepted pages should be used through the analysis CLI to avoid rerunning
OCR. For the case runner, set `BAUVO_WORKING_UNIT_MAX_NEW_CALLS` explicitly when
permitting new working-unit calls; the default is zero.

Provider-response storage is independent of business validation. Transport failures,
truncated output and non-JSON answers remain attempt evidence, not completed
responses. Input and response checksum failures stop replay rather than contacting
the provider as a fallback. The SDK's implicit retries are disabled at the guarded
boundaries so each counted attempt corresponds to one SDK request.
