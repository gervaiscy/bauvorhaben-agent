# Financial-risk evaluation

This is an isolated development benchmark. It does not alter the production pipeline.
The initial 12 cases are newly authored synthetic clauses covering observed failure classes.
They are **not independently reviewed, held out, representative contracts or legal advice**.
`dataset.lock.json` freezes bytes, not correctness. Expected answers never enter model requests.

## Comparison

- `discovery_review`: existing source-only three-page windows, followed by a project-wide review.
- `extract_interpret`: exact extraction of financially relevant passages from the same windows,
  followed by interpretation against the same project sources.
- Both approaches use both configured models, three independent calls per case/configuration.
  Testing both on the alternative model avoids selecting a winner from unreviewed labels.
- Temperature 1.0, MEDIUM thinking, 8,192 output tokens, no automatic retries. Repeats do not
  reuse model responses. Sequential stages save requests, raw responses, partial text and hashes.
- Failures remain failures. The global request ledger reserves calls before dispatch and caps
  total attempts; missing usage is not treated as zero cost. No pricing estimate is claimed.

```sh
set -a
source .env
set +a
.venv/bin/python scripts/benchmark_financial_risks.py run \
  --output runs/financial-risk-benchmark-new \
  --project YOUR_PROJECT \
  --models gemini-3.5-flash gemini-3.1-pro-preview \
  --repeats 3 --workers 8 --max-calls 400
```

Model availability must be checked on the execution project. Gemini 3's recommended temperature
is documented at https://ai.google.dev/gemini-api/docs/gemini-3/ . This deliberately differs from
older temperature-zero diagnostic runs, so comparisons are within this frozen experiment.

## Independent review before scoring

1. Give the reference reviewer `frozen-dataset.json` and `reference-review-template.json`.
   Do not show model outputs first. Check evidence, expected exposures, criticality, conditions,
   ambiguity and expected empty outputs. This reviewer must be separate from the reference author.
2. If any reference needs changing, create a new dataset version and lock, then a new run.
   Preserve old references and responses. Do not tune references to the observed model outputs.
3. A separate output-grading step uses `blind-review-packet.json`. Model, approach and repetition
   identities are withheld; source evidence and reference criteria are supplied. Keep
   `private-unblinding-map.json` from the grader until grading is complete. Wording may still
   reveal an approach; blinding reduces bias but does not guarantee it is impossible to infer.
4. Fill every output's grading record. Each output risk either maps to an expected exposure or
   is marked unsupported. For supported matches, list omitted/contradicted condition IDs.
   Set `exposure_id` to null for unsupported warnings. Use [] when no conditions are lost.
   Failed jobs have no output risks and count as missed expected exposures.
5. Reviewer identities and independent approval must be supplied by an actual reviewer. The
   agent does not fill these attestations. `reference_approval` is the approval file's SHA-256.

```sh
.venv/bin/python scripts/benchmark_financial_risks.py score \
  --output runs/financial-risk-benchmark-new \
  --approval /path/to/completed-reference-review.json \
  --grading /path/to/completed-blind-grading.json
```

## Metrics and limits

- Critical misses: expected critical exposures unmatched, including failed jobs. Denominator:
  expected critical exposures across all case repetitions.
- Unsupported warnings: explicitly labelled output risks without a supported exposure. Raw
  count and output-warning denominator are both retained; extra warnings cannot inflate recall.
- Conditions lost: distinct omitted/contradicted `(exposure, condition)` pairs per output. Missed
  exposures count as misses, not as falsely perfect condition preservation. Duplicate warnings
  cannot inflate the assessed-condition denominator.
- Consistency: pairwise Jaccard overlap of human-matched exposure IDs across complete repeats
  of the same case/configuration. Empty/empty is 1. Failed/incomplete repeat groups are excluded
  from this metric and reported separately. This does not measure stability of unsupported claims.
- Before grading, only request/schema failures and risk-presence agreement are reported. Presence
  agreement is not semantic consistency. No automatic winner is selected.

After this development diagnostic, independent unseen project packages are still needed to test
cross-company wording, real OCR defects, diagrams, long-distance qualifications and completeness.
