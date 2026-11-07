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

The manifest uses a non-empty `skill_name` matching the frontmatter name, a non-empty `evals` array, unique integer IDs, and non-empty prompt and expected-output strings. Optional `files`, `assertions`, and the guide-linked skill-creator alias `expectations` are arrays; empty arrays are valid. Assertion counts include declarations in both spellings. Input-file counts count unique paths.

Local policy caps the strict UTF-8 manifest at 1 MiB, JSON nesting at 64 levels, and each input at 5 MiB. Inputs must be regular files within the selected skill root, with root-relative POSIX paths and no symlinks at or within that root. The selected root can be reached through ancestors outside that boundary. Safe descriptor-relative inspection requires platform support; unavailable support yields an advisory fixture warning. These are evaluator limits, separate from transport limits and format requirements.

Output-eval content below `evals/` is omitted from supporting context, preserving recognized trigger-query fixtures under their separate read policy unless also declared as output inputs. Filesystem identities preserve these roles when capitalization aliases resolve to the same directories or query files; distinct case-sensitive paths retain their ordinary roles. Input paths recovered from a parseable manifest and safely identified aliases/hardlinks are also omitted from context, even when the manifest structure is invalid. Private file identities are checked without reading input contents and are never included in public metadata or logs. Supporting files and scripts receive bounded reads; declaring a script as input does not suppress its security checks. Public metadata contains only the fixed manifest path, validity, counts, and `measurement: "not_run"`.

## Measure in a separate runtime

1. Start each run in a clean context. Provide the same prompt and inputs for the skill and its baseline: no skill, or a snapshot of the previous version.
2. Save actual outputs in a sibling workspace, organized by iteration, case, and configuration such as `with_skill/` and `without_skill/`.
3. Record actual token usage and duration in `timing.json`. Grade assertions against outputs with concrete evidence in `grading.json`; use scripts for mechanical checks when possible.
4. Aggregate comparable runs in `benchmark.json`. Inspect individual failures, repeated-run variation, time and token costs, and assertions that pass or fail for both configurations.
5. Review outputs with a person and record specific feedback. Improve the skill's general instructions, then rerun every case in a fresh iteration.

Uploaded `grading.json`, `timing.json`, `benchmark.json`, transcripts, and claimed pass rates are supplied data. Their presence does not authenticate execution, grading, or improvement, and this evaluator does not promote them into verified measurements.
