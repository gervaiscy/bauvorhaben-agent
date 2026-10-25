# Modular phases and sequential pipeline

The settled prompts are unchanged. Each phase owns its prompt, code and response
schema. `pipeline/prompts.lock.json` records all eight prompt hashes; a prompt edit
requires a reason and an explicit lock update, checked by the offline tests.

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
before the next phase starts. Facts are collected from contract sources only;
each source's later phases receive the saved facts plus their own source windows.
Working units use three-page windows with one-page overlap inside document
boundaries. Applicability receives all working units from their original window,
all facts and the lean source payload. Timing retains its existing assessment
window selection and five-working-unit batches.

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
The facts result is a collection rather than a working-unit artifact.

`pipeline.json` records each phase's status, artifact fingerprint and final output
paths. Review findings remain visible. A failed model request stops the pipeline
before later phases. To resume, repeat the same command and exact output path with
`--resume`. Verified completed outputs are reused; a failed step gets `attempt-02`
so its first partial result and raw attempts remain preserved. Changed PDFs,
configuration, code or saved outputs require review and a new run rather than
silently reusing mismatched evidence.

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
