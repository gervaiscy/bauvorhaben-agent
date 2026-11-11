# Alternative groups

`cases.json` contains independently authored source clauses and expected groups:

- A OR B with no documented selection.
- (A AND B) OR C: two steps belong to one route, one step to the other.
- Different deadlines for the alternative routes.
- A waived original duty with an explicitly available alternative; the missing
  alternative action is a separate conditional proposal, with `unit_id: null`.

Expectations are regression controls, never model input. They do not establish model
accuracy. The offline tests exercise source binding, AND/OR structure, task references,
selection evidence, preservation of raw proposals and safe checklist rendering.

The full-context reviewer returns groups in both its independent assessment and
comparison. Its source-only request receives a catalog of task IDs, names and page
locators for cross-batch links, without earlier interpretations. Group membership,
conditions, timing and documented selection still require source interpretation.

For a project review, prepare a new output directory with the existing pilot:

```sh
.venv/bin/python scripts/manual_applicability_review.py prepare \
  --run /path/to/completed-pipeline --plan /path/to/batch-plan.json \
  --output /path/to/new-review
```

The plan has `{"batches": [{"unit_ids": ["existing-task-id"]}]}`, with one to three
batches of at most five targets. Preparation makes no model calls. Running
`scripts/manual_applicability_review.py run --output /path/to/new-review` uses the
existing two requests per batch. Full original project sources go to both requests;
expectations and earlier batch responses never enter the independent request.

`alternatives.json` retains the comparison proposals and validation findings.
`checklist.html` presents structurally valid groups as unreviewed proposals with
route-specific timing and source evidence. It has no progress controls and applies
no changes to the original run. A source-valid quotation alone does not verify an
alternative's meaning or prove a selection.
