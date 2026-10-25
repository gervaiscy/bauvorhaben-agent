---
name: bauvo-working-unit-prompts
description: Maintain and review Bauvo working-unit extraction prompts, preserving the distinction between promotion, assigned work and explicitly optional actions.
---

# Bauvo working-unit prompts

Read the current `EXTRACTION` prompt in
[page_flow.py](../../../pipeline/working-units/src/bauvo_working_units/page_flow.py)
and the corresponding [regression cases](../../../pipeline/working-units/evaluation/cases.json)
before changing action classification.

Preserve these distinctions:

| Source meaning | Expected extraction |
| --- | --- |
| General promotion, aspirations or company capabilities | Neither working units nor service offers |
| Specific assigned or established work | A working unit, preserving its stated modality and responsible party |
| An explicitly selectable choice or commissioning action | An `OPTIONAL` working unit; describe the offered service separately in `offers` |

A capability statement does not establish a selectable offer. Promotional surroundings
must not suppress specific work in the same passage. Preserve recommendations and
permissions as such; these three distinctions do not replace the full modality schema.

Keep the prompt generic and concise. Do not introduce company-specific wording,
keyword filters, an extra classification pass or application-side deletion of model answers.

Maintain these generic controls in `cases.json`:
- `promotion-and-capability-only`: no units or offers.
- `promotion-with-specific-work`: retain the assigned task; ignore the advertising.
- `explicit-optional-commission`: retain the commissioning action as `OPTIONAL`,
  distinct from the offered service.

Run `make test` after prompt changes. Within the authorized live-test scope, inspect
actual extraction responses against these expectations; valid citations or JSON do
not prove correct classification. Keep expectations separate from model inputs,
preserve exact responses and failures, and report unresolved results. An approved
window-by-window review stops after the requested window; this skill does not
authorize further windows or API runs.
