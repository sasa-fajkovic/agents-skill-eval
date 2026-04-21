# CI Thresholds

Severity-to-CI-behavior mapping for the `--ci` output mode.

## Severity levels

| Severity | CI behavior | Exit code | Meaning |
|----------|-------------|-----------|---------|
| ERROR | Fail | 1 | Spec violation or cross-platform breakage. Must fix before merge. |
| WARN | Warn | 2 | Quality/efficiency/portability issue. Should fix, won't block. |

## Exit code logic

```
If any ERROR findings → exit 1 (FAIL)
Else if any WARN findings → exit 2 (WARN)
Else → exit 0 (PASS)
```

## Check severity mapping

### ERROR (blocks merge)
- 1.1: name format violation
- 1.2: description missing, empty, or over 1024 chars
- 1.3: non-standard field in frontmatter (Claude Code extension or unknown field)
- 1.4: field type/value violation
- 1.6: SKILL.md over 500 lines
- 1.8: scripts missing --help
- 1.10: scripts use interactive prompts
- 1.11: scripts use unrecognized scripting language (not .py, .sh, or another known runtime)

### WARN (reported, does not block)
- 1.2: description missing "when" clause
- 1.3: experimental agentskills.io field, or Claude Code extension placed in metadata
- 1.5: metadata key duplicates git history (per-key, not blanket)
- 1.7: script has no matching test — eval.py always emits WARN; LLM escalates to ERROR for scripts >30 lines or with real logic
- 1.9: scripts lack structured output
- 1.11: scripts use discouraged but recognized language (e.g., JavaScript — works but Python/Bash preferred)
- 2.1: unscoped tool usage in body
- 2.2: destructive operations without safeguards (suppressed when skill-level Rules section contains a global safeguard)
- 3.1-3.7: all token efficiency checks
- 4.1-4.7: all effectiveness checks

## CI output format

```
SKILL_EVAL_RESULT=FAIL|WARN|PASS
SKILL_EVAL_ERRORS=<count>
SKILL_EVAL_WARNINGS=<count>

[ERROR] <ID>: <message>
[WARN] <ID>: <message>
```

Each finding on its own line. Grouped by severity (ERRORs first, then WARNs).

## Human output format

```markdown
## Skill: <name>
Baseline: <N> lines, ~<N> tokens

### Errors (<count>)
- **1.3**: field "model" is not in the agentskills.io stable spec (Claude Code extension)
  Fix: Remove from frontmatter. Express model preference in skill body if needed.

### Warnings (<count>)
- **3.2**: lines 45-90 contain a 46-row team lookup table (~138 tokens)
  Fix: Move to references/team-lookup.md and load on demand.

### Result: FAIL | WARN | PASS
```
