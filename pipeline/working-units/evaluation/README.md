# Focused evaluations

Project-facts-only controls (price bindings, independent offer positions, permissions
versus services, conditions in following sentences, and signed-contract attachments titled as offers):

```sh
.venv/bin/python pipeline/working-units/evaluation/project_facts_evaluation.py \
  --output runs/manual/project-facts-evaluation-new --project YOUR_PROJECT
```

This uses the production contractor-commitments prompt and compact
`verpflichtungen: [{verpflichtung, zitate: [{pdf_page, text}]}]` schema with
three-page windows and one-page overlap. No categories or agreement-status
fields are generated; source identity is bound by the application. Expected meanings stay outside model requests. Review the five saved
responses against `expectations.json`; execution status does not measure semantic
completeness. Raw attempts, unfinished responses and unresolved findings survive.

One runner records the production project-facts preparation and five task passes and focused pass-only cases.
It preserves every response and prompt with page-and-quote review flags, without repairing citations or
rewriting interpretations, or claiming that valid JSON means correct meaning.

```sh
.venv/bin/python pipeline/working-units/evaluation/focused_evaluation.py \
  --output runs/manual/focused-new-run --project YOUR_PROJECT
```

This runs fifteen end-to-end action cases, twenty-four applicability controls and nineteen
timing controls. Add the following arguments to include source-backed BLB windows:

```
--pages /path/blb/pages.json --documents /path/blb/documents.json
--applicability-baseline /path/applicability-baseline/working-units.json
--timing-baseline /path/timing-baseline/working-units.json
```

Source baselines must match the exact page snapshot and contain the historical
focus IDs in the JSON specifications. Source windows include competing units and
use the same batches of five as production. The numbered-checklist case repeats
extraction before assessing timing. No full BLB run is started by this runner.

`expectations.json` stays separate from model inputs. Compare actions, responsible
parties, scope, conditions, timing, consequences and quotations with the source.
Record CORRECT / INCORRECT / MISSING / UNCERTAIN judgments in a separate review
file. Historical check labels describe scenarios, not required old schema fields.
Keep all original cases and difficult results; no model output may be hand-edited.

`recorded` is an execution status, not a semantic pass. Structural issues and failed
requests stay visible. Raw results, trace prompts, source hashes and code fingerprints
are retained. Offline unit tests exercise boundaries, batching, independent passes,
raw preservation, missing/unknown targets and the citation checks that preserve raw responses.

To run a subset, repeat `--case CASE_ID`. For example, `--case
operation-versus-installation --case deadline-across-window-overlap --case
separate-document-context` exercises short input, cross-page continuation and
document isolation. Add `--case blb-owner-work-and-costs` with the source arguments
to revisit the previously failed BLB applicability window without a full BLB run.
The production context selection and one-retry wrapper are used. Raw attempts and
finish reasons remain separate from interpreted results, including recovered failures.

The step-2 action/timing suite consists of the fifteen action cases plus nineteen timing
controls. Added cases cover statements versus actions, checklist context across pages,
performance versus commissioning, installation versus confirmation, negative timing,
WITHIN_AFTER deadlines and durations with unknown starts. Expectations remain separate
from prompts. JSON contract checks are not semantic accuracy scores; review missing,
extra and incorrectly scoped actions and timing in every saved response.

Duration regressions include parenthetical readiness/heating lengths, a six-week period
with no stated reference, and a dated confirmation with a different explicit reference
event. Raw responses and unresolved ambiguity are preserved.

Negation controls distinguish real negatively worded duties/not-before bounds from
missing evidence, consequences, denied dependencies and statements that no date exists.

Applicability scope controls separate timing and progress from contractual inclusion,
keep neighboring work out of rules, use known house facts in the decision rather than
as invented inclusion rules, and contrast unknown/adequate/inadequate replacement space.

Component-coverage cases require cited APPLIES_WHEN rules with JSON null conditions
for unconditional work/costs, alongside separately scoped conditional/excluded rules.
One case spans five pages. Coverage is reviewed from the saved response, without
an additional verification pass or application-side rule synthesis.

Unexpected control characters (except tabs/newlines/carriage returns) and U+FFFD in
any response string create review findings with their JSON path and code points.
They are not corrected or retried; plausible-looking spelling/encoding errors are
not reliably detected by this check. Provider generation failures still get at most
one unchanged retry, with partial results and every attempt preserved separately.

Promotion regressions contrast general advertising/capabilities with specific assigned
work in promotional surroundings and an explicitly selectable commissioning action.
Expectations remain separate from prompts; no keyword filtering or output deletion.

Evidence-timing controls assess delivery and evidence-submission units together:
only delivery dated, both activities dated separately, and an evidence-backed
alternative delivery deadline. Expectations stay outside prompts; inspect both units
for correct scope and preservation of the alternative, not only valid citations.


Production defaults are three-page windows with one-page overlap. Historical
pass-only fixtures may contain up to five pages; assessment context is selected
from production-size windows, with unresolved context flagged. Legacy source-backed
specifications retain their explicit historical window sizes.


End-to-end action cases also run PAGE_PROJECT_FACTS before task extraction. Pass-only
historical timing/applicability controls do not constitute a complete project-facts
assessment. Offline project-fact regressions cover all windows, explicit uncertainty,
offer status, conflicting amendments, source-qualified citations, failed requests
and complete fact delivery without a relevance heuristic. No live accuracy claim is
made from stub-provider tests.
