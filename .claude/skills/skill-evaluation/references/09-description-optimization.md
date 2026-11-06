# Description optimization and trigger evidence

Source: [Agent Skills: optimizing skill descriptions](https://agentskills.io/skill-creation/optimizing-descriptions).

## Review the description

The description is visible before the skill body is loaded. State the user intent and situations where the capability helps, including indirect requests. Imperative wording such as “Use this skill when…” is useful guidance, not mandatory syntax. Favor a few focused sentences that cover scope without catching unrelated work. The format limit remains 1024 characters; no shorter cutoff proves quality.

Useful exclusions define boundaries with adjacent capabilities. “Do not use this skill for database imports” can prevent false triggering without prescribing another workflow. Do not penalize that boundary as negative-only framing. Simple tasks may need no skill even if their keywords match.

## Prepare optional trigger queries

Use realistic prompts labeled with whether the skill should activate. The evaluator recognizes `eval_queries.json`, `train_queries.json`, and `validation_queries.json` in the skill root or `evals/`:

```json
[
  {"query": "My manager needs a chart from ~/Downloads/sales.csv, grouped by region", "should_trigger": true},
  {"query": "Write a script that imports ~/Downloads/sales.csv into Postgres", "should_trigger": false}
]
```

These labels illustrate a CSV analysis skill, not this evaluator. Adapt them to the target skill. Mix direct and indirect positives, terse and detailed requests, casual phrasing, and workflows where the relevant step is embedded in a larger task. Negatives should include plausible near misses with shared vocabulary but a different intent. Around 20 balanced queries is a starting point, not a quota.

Rule 4.9 checks only structure: a non-empty array, non-empty `query` strings, and JSON boolean `should_trigger` labels. Extra fields are allowed. Missing fixtures are unpenalized. Malformed provided files warn as optional evaluator policy. Counts cannot establish label correctness, realistic coverage, or successful activation.

## Measure in the target client

The static evaluator does not install or invoke uploaded skills. `metadata.trigger_evals.measurement` is always `not_run`; neither a good score nor an AI assessment establishes triggering accuracy.

For empirical evidence, install and register the skill in the intended client, run each query, and inspect client logs or tool history to determine whether the skill's `SKILL.md` was loaded. Repeat each query; three runs is a starting point. Record invocation counts and calculate the fraction of runs that activated the skill. Choose and document the pass threshold before comparing iterations.

Keep a fixed, proportionally balanced train/validation split; roughly 60/40 is a starting point. Use training failures to revise general scope and boundaries rather than adding isolated keywords. Keep held-out prompts out of revision feedback, compare iterations by validation performance, and check fresh prompts after selecting a description. Report client/model, skill revision, queries, repetitions, threshold, and observed invocation evidence before claiming measured trigger performance. Treat output-quality evaluation as a separate question.
