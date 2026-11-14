# Action anchor prototype

This experiment is separate from the production pipeline. It does not change its
prompts, schemas, orchestration, task files, accepted facts or cache protocols.

Gemini identifies each action with `source_segment`, `action_quote` and `actor`.
The application binds a unique quotation to its original offsets, restores the
complete paragraph and keeps the entire supplied window and contractual context
once in `input.json`. HTML reviews highlight the action inside the original text.
Context is available source material, not a claim that every passage proves the action.

Only whitespace differences may be normalized for binding. Unknown labels, absent
or ambiguous quotations and malformed rows stay visible, without fuzzy repair,
output deletion, extra verification calls or automatic retries. Several actions
may share the same paragraph and retain different actors.

## Comparison controls

`controls.json` supplies expectations for five fictional regression cases and the
three promotion/optional-commission controls in `../cases.json`. They produce
eleven document-window requests. Other fictional documents provide original
quotations as context; no expected interpretations or answers enter the prompt.

Saved runs can be added with separately authored private expectations. Keep those
expectations under ignored `runs/`, alongside the sources. The preparation step
reads the saved request's actual `SOURCE DATA`, not reconstructed titles or a
previous model answer. It records previous responses separately for comparison.

Inputs, expectations and prompts are frozen before generation. Each expected
action has source-local literal anchor alternatives and accepted actor aliases.
The assessment reports all of these together:

- Missing expected actions.
- Explicit forbidden-action matches, such as assigning excluded work.
- Unexpected candidates requiring review; unmatched does not automatically mean invented.
- Several expected actions grouped into one quote, or duplicate action records.
- Actors outside the authored aliases.
- Invalid source bindings.

Full paragraphs and retained context cannot satisfy an action match. This prevents
a broad context bundle from hiding a missing action. A response passes the authored
controls only when all dimensions are clear; semantic review remains required.
Literal matching is a diagnostic aid, not a general correctness verifier. Real-source
inventories may be targeted, and finer legitimate splits or alternative wording
can require manual judgment. Baseline outputs lack action anchors, so they do not
receive a fabricated automatic anchor score.

## Prepare without calling Gemini

```sh
.venv/bin/python pipeline/working-units/evaluation/action_anchor_prototype/runner.py prepare \
  --output runs/manual/anchor-suite-new
```

To add a private saved run, supply `--saved-run /path/to/run` and
`--saved-expectations /path/to/private-expectations.json`. Its format is:

```json
{
  "cases": [{
    "id": "saved-window",
    "response_number": 1,
    "expectations": {
      "scope": "targeted_authored_inventory",
      "expected_actions": [{
        "id": "delivery",
        "anchors": [{"source_segment": "S3", "any_of": ["Original action words"]}],
        "actors": ["Auftragnehmer", "AN"]
      }],
      "forbidden_actions": [],
      "context_checks": [{"id": "deadline", "for_actions": ["delivery"], "segments": ["S4"]}]
    }
  }]
}
```

## Plan, run and replay

```sh
.venv/bin/python pipeline/working-units/evaluation/action_anchor_prototype/runner.py run \
  --suite runs/manual/anchor-suite-new --output runs/manual/anchor-plan-new \
  --project "$GOOGLE_CLOUD_PROJECT" --plan-only
```

The default `--max-new-calls` is zero. A new live run requires an explicitly
authorized positive limit and configured Vertex credentials. The full request
plan is checked before any client is constructed. There is one attempt per new
request; a provider failure stops further new calls. Existing saved responses can
still be assessed offline. No extraction or assessment stage follows this experiment.

Completed responses are stored immutably in `SUITE/provider-responses/` before local
validation, with exact prompt, schema, generation, project, location and model
identities, response hashes, raw JSON and diagnostics. Validation code and local
expectations do not enter the request identity. Replaying a completed run uses the
same suite/project/model/cache and a new output directory, with zero new calls.
Preparing a new suite with changed local expectations can reuse the same actual
provider requests via `--cache-dir`. Changed model requests cannot reuse old answers.

Each case produces `input.json`, `expectations.json`, `prompt.txt`, immutable
provider evidence, `assessment.json` and `review.html`. The run has a request plan,
code/source fingerprints and a joint `summary.json`. Failed attempts and previous
outputs are preserved. Prepared directories and run directories cannot be overwritten.

Offline verification: `make test`. Synthetic providers test transport and evaluation
mechanics; only new provider responses can assess the proposed extraction behavior.
