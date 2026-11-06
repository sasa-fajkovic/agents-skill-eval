# Optional output-quality evals

The [Agent Skills evaluation guide](https://agentskills.io/skill-creation/evaluating-skills) describes an empirical improvement loop. This evaluator checks optional test-case structure; it does not run uploaded skills, grade their outputs, or measure their performance. A static score and an optional AI review do not establish output quality.

## Test cases

Store an optional manifest at `evals/evals.json` inside the skill directory. Give each case a realistic prompt and a human-readable expected output. Input paths are relative to the skill root. Assertions are optional and can be added after inspecting initial outputs.

```json
{
  "skill_name": "sales-summary",
  "evals": [
    {
      "id": 1,
      "prompt": "Summarize the monthly revenue in evals/files/sales.csv.",
      "expected_output": "A summary of revenue totals with missing values identified.",
      "files": ["evals/files/sales.csv"],
      "assertions": ["The summary identifies rows with missing revenue"]
    }
  ]
}
```

Start with a few varied cases and include edge cases. These are authoring recommendations, not required case counts. Absence of a manifest or assertions is neutral. Invalid supplied fixtures produce advisory rule `4.10` warnings under evaluator policy, not Agent Skills format errors.

## Measure in a separate runtime

1. Start each run in a clean context. Provide the same prompt and inputs for the skill and its baseline: no skill, or a snapshot of the previous version.
2. Save actual outputs in a sibling workspace, organized by iteration, case, and configuration such as `with_skill/` and `without_skill/`.
3. Record actual token usage and duration in `timing.json`. Grade assertions against outputs with concrete evidence in `grading.json`; use scripts for mechanical checks when possible.
4. Aggregate comparable runs in `benchmark.json`. Inspect individual failures, repeated-run variation, time and token costs, and assertions that pass or fail for both configurations.
5. Review outputs with a person and record specific feedback. Improve the skill's general instructions, then rerun every case in a fresh iteration.

Uploaded `grading.json`, `timing.json`, `benchmark.json`, transcripts, and claimed pass rates are supplied data. Their presence does not authenticate execution, grading, or improvement, and this evaluator does not promote them into verified measurements.
