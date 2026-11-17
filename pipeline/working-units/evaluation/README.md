# Focused evaluations

Twenty-seven fictional offer controls in `offer-cases.json` cover context-only proposals,
existing contracted services, priced extras already commissioned by amendment,
agreed conditional credits, data permissions, promotion, genuine new extras and
conditional selectable services. Indirect formulations cover selectable variants,
request-dependent separate agreements, conditionally commissioned work and choices
without a fixed price or surcharge. Negative controls retain already agreed variants,
general special-wish rules and cost duties that do not establish a service offer.
Promotion surrounding a concrete option must not suppress that option.
Additional controls distinguish separate services in one paragraph, an unidentified
provider, and a reference sentence whose commissioning terms appear immediately before it.
Held-out cases cover indirect execution of separately commissionable trades and
an explicitly inseparable service package. They differ from the prompt's fictional
garden-work and electronic-equipment demonstrations.
Their expected answers are never sent to Gemini.
Additional paired controls distinguish required-but-excluded accessories from
explicitly orderable accessories, retain concrete services next to general
special-wish rules, and distinguish a neighbouring trade's provider from an
explicit provider of the additional service.
Offline tests use authored responses to verify transport and preservation; they
do not measure whether Gemini follows the revised classification prompt.
The production Offers request sends only original primary-page text from the
current window and compact project-fact interpretations with original supporting
quotations. Each primary page is represented once by original paragraph slices
labelled `P<page>-<paragraph>`. The simple German question and JSON schema request
offer descriptions, providers, terms and `{passage}` citations. No known-items
list or full-document context is sent. The application restores exact original
source spans from selected labels, while raw responses remain unchanged. Technical compatibility alone does not establish a separately offered
service; it is a held-out negative control.
Unknown passage selections or invalid proposals do not stop the remaining extraction windows;
the completed pass retains all findings and blocks downstream processing until resolved.
Application-generated `derived.review_context` preserves original paragraph text
separately from selected citations. Offline tests cover wrapped/blank lines, page
and document boundaries, invalid bindings, unchanged raw answers, replay and audit.
Historical quotation-based source-selection recovery has separate offline tests: original model
errors remain in raw answers and audit history, only fingerprinted exact source
spans may replace derived bindings, missing selections remain blocking, and a
successful recovered result can enter the next stage without a provider call.
Current label tests cover exact page reconstruction, repeated paragraphs, multiple
selections and shared evidence, unknown/cross-window/fact labels, stale maps, raw
preservation, zero-call replay, and cache separation from old quotation requests.

The isolated [action anchor prototype](action_anchor_prototype/README.md) compares
specific original action quotations with application-retained paragraph/window
context. It jointly checks missing/extra actions, grouping and actors against
separate authored expectations. It leaves production unchanged, defaults to zero
new provider calls, preserves completed responses before validation and supports
offline replay. Its literal controls require semantic review; they are not a
production correctness verifier.

Current project-facts controls ask: **Welche Regelungen enthalten die Vertragsunterlagen
für mein Bauvorhaben, und was bedeuten sie für mich als Bauherrn?**
Twenty-one fictional cases in `contractual-rights-cases.json`
cover included services, conditional credits and postal exceptions, qualified
security, amendments, unaccepted/incorporated offers, homeowner duties, signatures,
weather-qualified deadlines, unresolved discount eligibility and drawing inclusion,
qualified bank confirmations, ownership-evidence duties, single-viewing limits and
partially established service scope, standalone advertising permissions, payment/cost
duties, neighbouring credit/viewing provisions without an invented exchange, company-scoped
confirmation requirements in mixed offer footers, contract formation and Textform for amendments.

```sh
.venv/bin/python pipeline/working-units/evaluation/project_facts_evaluation.py \
  --output runs/manual/rights-evaluation-new --project YOUR_PROJECT --max-new-calls 21
```

The default new-call limit is zero. A supplied positive ceiling counts actual
attempts, including transport/truncation retries. The runner uses the production
German prompt and schema-15 `ansprueche: [{aussage, belege}]` response.
Each batch receives the complete source package. Statements and raw JSON are
preserved; the application resolves citations from short source labels.

Expected meanings stay outside model input. `rights_checks` reports authored count,
wording checks on interpretations, including omission of unestablished entitlements
and retention of supported subsets, not on quoted source text. These
are case-specific evaluation checks, not a production filter or general legal
correctness verifier. Review the interpretations against original sources and
`expectations.json`, including missing rights. Structurally valid empty extraction
can be correct for an offer-only passage or wrong for a substantive entitlement.
Raw responses, attempts and unresolved findings remain available.

The earlier 38 broad project-fact/clause-index cases remain in
`project-facts-cases.json` for historical artifact/evaluation regression tests.
Their passage-disposition, grouping and status expectations do not define the
current contractual-position task. Controlled applicability evaluations retain explicitly
historical broad-context fixtures; they do not pretend the rights extractor produced them.

One runner records the production project-facts preparation and five task passes and focused pass-only cases.
It preserves every response and prompt with page-and-quote review flags, without repairing citations or
rewriting interpretations, or claiming that valid JSON means correct meaning.

```sh
.venv/bin/python pipeline/working-units/evaluation/focused_evaluation.py \
  --output runs/manual/focused-new-run --project YOUR_PROJECT
```

This runs thirty-four end-to-end action cases, twenty-four applicability controls and thirty
timing controls, including the qualification controls described below.
Four `task-evidence-*` action controls cover complete split sentences and extension
limits, alternative documentation, payment-table rows with a withholding exception,
and conditional refunds with deductions and offset restrictions. Their expected
source selections are separate from the model input. Offline tests check literal
restoration and preservation of those authored selections; they do not measure
whether Gemini selects them correctly.
Seven additional `task-evidence-*` controls cover linked credits and amendments,
receipt-only acknowledgements, passive work with unknown performer, an explicit
performer distinct from the issuer, nominal actions and included room rows,
scope exclusions without assignment, and separately assigned excluded work.
They include positive counterexamples: an unknown actor does not erase work;
an exclusion from one party's scope does not erase another party's assignment.
The production prompt performs its coverage check within the same response;
there is no extra model pass, application-side semantic filter or new accounting field.
Five further action controls are held out of the prompt demonstrations:
`task-evidence-credit-reference-context-only-amendment`,
`task-evidence-equivalent-consents-separate-documents`,
`task-evidence-penalty-with-later-deadline-definition`,
`task-evidence-series-specification-as-scope`, and
`task-evidence-independent-actions-different-parties-one-paragraph`.
They cover a separately sourced bonus amendment, three equivalent company consents
in separate document windows, a deadline defined after its penalty, standard scope
and exclusions, and three actors sharing one paragraph. The prompt instead contains
eight independent fictional input/output demonstrations with complete production-shaped
JSON answers. Offline checks bind those examples, preserve source text and actors,
and exercise context-only amendments without making fact quotations selectable as
window labels. These authored answers verify transport and example consistency,
not whether Gemini generalizes; fresh provider answers need separate semantic review.
Add the following arguments to include source-backed BLB windows:

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

The step-2 action/timing suite consists of the thirty-four action cases plus thirty-four timing
controls. Added cases cover statements versus actions, checklist context across pages,
performance versus commissioning, installation versus confirmation, negative timing,
WITHIN_AFTER deadlines and durations with unknown starts. Expectations remain separate
from prompts. JSON contract checks are not semantic accuracy scores; review missing,
extra and incorrectly scoped actions and timing in every saved response.

Duration regressions include parenthetical readiness/heating lengths, a six-week period
with no stated reference, and a dated confirmation with a different explicit reference
event. Raw responses and unresolved ambiguity are preserved.

Timing qualification controls add scoped start events, conditional weather extensions,
restart allowances within a total cap, existing-connection versus confirmed-date
alternatives, cumulative prerequisites and limited clock restarts. Later sentences
carry qualifications that must remain in the free-text answer. Expectations describe
meaning, not required structured timing fields or exact answer wording.
Pass-only requests include source-span evidence IDs and resolve them for citation
checks. Full source text and raw answers remain unchanged; a structurally valid but
incomplete timing answer still requires semantic review.

Run just these four controls with:

```sh
.venv/bin/python pipeline/working-units/evaluation/focused_evaluation.py \
  --output runs/manual/timing-qualifications-new --project YOUR_PROJECT \
  --case scoped-start-weather-restart-and-cap \
  --case connection-or-confirmed-date-with-reference \
  --case all-prerequisites-start-last-received \
  --case limited-restart-and-unrelated-deadline
```

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
Supplier-directory controls cover multiple company profiles with no project work,
and the same profiles alongside assigned and included work that must be retained.
Expectations remain separate from prompts; no keyword filtering or output deletion.

Evidence-timing controls assess delivery and evidence-submission units together:
only delivery dated, both activities dated separately, and an evidence-backed
alternative delivery deadline. Expectations stay outside prompts; inspect both units
for correct scope and preservation of the alternative, not only valid citations.


Production defaults are ten-page windows with one-page overlap. Historical
pass-only fixtures may contain up to five pages; assessment context is selected
from production-size windows, with unresolved context flagged. Legacy source-backed
specifications retain their explicit historical window sizes.


End-to-end action cases also run PAGE_PROJECT_FACTS before task extraction. Pass-only
historical timing/applicability controls do not constitute a complete project-facts
assessment. Offline project-fact regressions cover complete contract packages, explicit uncertainty,
offer status, conflicting amendments, source-qualified citations, failed requests
and complete fact delivery without a relevance heuristic. No live accuracy claim is
made from stub-provider tests.

Five claim-evidence controls cover deferred conditional credits, qualifications across
source lines, ambiguous consent signatures, bank-confirmation contents and exceptions,
and roof specifications with adjacent conditions. `claim-expectations.json` stays
outside model input; `claim_evidence_checks` reports required evidence groups within
an individual claim rather than merely somewhere in a fact. These controls do not
score semantic correctness: a claim may cite all required lines and still misinterpret
them. Review `expectations.json`, claims, summary and uncertainty together.

Five additional project-facts cases cover documented project configuration, conditional
features without a documented selection, offer presence without acceptance, contractual
prerequisites without evidence of completion, and multiple independent provisions in
one selectable span. The project-facts suite now has 32 cases. These cases are held
out of the fictional prompt demonstrations; expected meanings are stored separately
and require semantic review. In particular, complete ID accounting cannot prove that
both provisions in a single span were extracted. No model call is added to production.
The project-facts suite now contains 38 cases. Six additional held-out controls cover
qualified security wording, an unassigned payment note, a postal-time exception,
priced offers without acceptance, base-price completeness and OCR signature observations.
Their `interpretation_expectations` stay outside model input and are saved separately.
`interpretation_checks` examines claim text and explicit status, not copied source
quotes. These authored lexical controls can expose those omissions/overstatements;
they do not certify semantic correctness or replace source review.

Timing scope controls additionally distinguish an included rental period from
installation duration, post-completion inspection from unrelated financing rules,
and a heating qualification limited to a subset of trades. A five-task control
retains separate confirmation and delivery timing, optional solar commissioning,
alternative homeowner-work prerequisites and a selection deadline. These held-out
cases use different wording and values from the prompt illustrations. Expected
meanings stay outside model inputs; source-valid answers can still misinterpret
these clauses and therefore require semantic review.

Three further timing controls distinguish a planned subsection with no stated start
from explicit common and individual start events. Their source wording and durations
differ from the prompt illustration. Local regression tests also cover unique,
audited I/l OCR binding, rejected ambiguous or substantive quote changes, lossless
display compaction and compatibility with earlier readable timing artifacts.

A composite-task control keeps two different phase durations separate and distinguishes
a required completed state from the duration of the work producing it.
