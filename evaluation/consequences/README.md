# Consequence extraction controls

Generic cases cover purpose versus consequence, possibility versus certainty,
alternative fulfillment routes, overriding amendments, unsupported causal links,
missing documented effects, ordinary price/responsibility statements, and an
enabling provision incorrectly treated as necessary.

Each consequence batch makes one generation request with all supplied documents.
There are no subsequent Gemini assessment, review or correction calls.
`test_consequence_single_pass.py` checks context delivery, batching, preservation
of raw answers, local validation without another model call, and recovery.
These offline tests do not measure Gemini's semantic accuracy.

For live evaluation, send only `sources` and `task` through the production prompt.
Keep `expected` outside model input. Preserve requests, raw answers and failures;
compare actual status, claims, conditions and evidence with the expected results.
