# Machine-readable output schema (`--ci`)

The evaluator must print exactly one JSON object to stdout and nothing else. No prose, markdown, banners, or trailing text.

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "skill_name": "string",
  "overall_score": 85,
  "overall_tier": "good",
  "summary": "One sentence summary of the skill quality",
  "checks_overview": {
    "checks_total": 30,
    "checks_passed": 28,
    "checks_failed": 2
  },
  "findings": {
    "total": 2,
    "error_findings": [
      {
        "rule_id": "1.3",
        "message": "\"model\" is not a recognized agentskills.io field",
        "reason": "Non-standard fields break portability across platforms."
      }
    ],
    "warning_findings": [
      {
        "rule_id": "4.6",
        "message": "no clear success criteria or output contract found",
        "reason": "Without success criteria the agent cannot determine when the task is complete."
      }
    ]
  },
  "metadata": {
    "file_count": 3,
    "line_count": 145,
    "has_scripts": true,
    "script_types": [".py", ".sh"],
    "unsupported_script_types": [".js"]
  },
  "skill_content": "raw SKILL.md body (stripped by web app before returning to client)",
  "supporting_context": "concatenated supporting files (stripped by web app)"
}
```

## Field constraints

- `schema_version` must be present so downstream agents can detect breaking changes.
- `status` must be `"ok"` for successful evaluation.
- `overall_score` must be an integer `0-100`.
- `overall_tier` must be one of `excellent`, `good`, `needs_work`, or `poor`.
- `checks_overview` groups `checks_total`, `checks_passed`, and `checks_failed`.
- `findings` groups `error_findings` and `warning_findings` as arrays, with a `total` count.
- `rule_id` uses numeric check IDs (e.g., `"1.3"`, `"4.6"`).
- Every finding must include a `reason` so no follow-up LLM call is needed to explain it.
- All findings arrays must always be present as `[]`, never `null`.
- `metadata.unsupported_script_types` retains the legacy evaluator-policy set `.js`, `.ts`, `.go`, `.rb`, `.php`, and `.pl`. This field does not classify a language as invalid under the specification. Other extensions can receive 1.11 portability warnings without appearing in this legacy field.

## Trigger-query metadata

`metadata.trigger_evals` contains `measurement: "not_run"` and a `files` array. Each recognized file has `path`, `valid`, `query_count`, `should_trigger_count`, and `should_not_trigger_count`. Invalid files retain zero counts and emit rule 4.9 warnings. With no fixtures, `files` is empty. Counts describe valid fixture structure only; the evaluator does not install or invoke uploaded skills, inspect client invocation logs, or calculate trigger rates. This is separate from output-quality test cases.
