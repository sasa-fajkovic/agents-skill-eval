# CI Thresholds

Severity-to-CI-behavior mapping for the `--ci` output mode.

## Severity levels

| Severity | CI behavior | Exit code | Meaning |
|----------|-------------|-----------|---------|
| ERROR | Fail | 1 | Format violation or evaluator policy error. |
| WARN | Warn | 2 | Quality/efficiency/portability issue. Should fix, won't block. |
| Dependency error | Fail | 3 | Required PyYAML is missing; no skill evaluation was performed. |

## Exit code logic

```
If any ERROR findings → exit 1 (FAIL)
Else if any WARN findings → exit 2 (WARN)
Else → exit 0 (PASS)
```

## Check severity mapping

### ERROR (exit 1)
- 1.1: name format violation (1–64 characters after NFKC normalization; no word-count limit)
- 1.2: description missing, empty, non-string, or over 1024 characters
- 1.3: unknown top-level field (not one of the six documented fields or a known platform extension)
- 1.4: field type/value violation
- 1.8: entrypoint scripts missing --help (evaluator policy)
- 1.10: scripts use interactive prompts without fallback indicators (evaluator policy)
- 3.7: MCP tool from a blocked namespace (has a strictly better CLI alternative; see [`mcp-policy.md`](mcp-policy.md))
- 4.8: redirect/pointer skill with no actionable instructions (evaluator policy; body format is unrestricted by the specification)

### WARN (exit 2)
- 1.2: description missing recognized activation context (optional evaluator heuristic; flexible wording)
- 1.3: known platform extension (portability policy); a valid experimental allowed-tools string produces no finding
- 1.6: SKILL.md at 500 or more lines (specification recommendation, including reference skills)
- 1.9: scripts lack structured-output indicators (evaluator policy)
- 1.11: other script extensions than .py/.sh, or incomplete script/security analysis under the safe bounded read policy (no format ERROR)
- 2.1: unscoped tool usage in body
- 2.2: destructive operations without safeguards (suppressed when skill-level Rules section contains a global safeguard)
- 2.4: hardcoded user home directory paths
- 3.1-3.6: token efficiency checks (inline code, reference data, tutorials, duplication, verbose prose, preload)
- 3.7: MCP tool from an unknown namespace or generic MCP prose (per-case review; see [`mcp-policy.md`](mcp-policy.md))
- 4.1-4.7: all effectiveness checks
- 4.8: thin skill body (few non-blank lines, no scripts)
- 4.9: malformed provided trigger-query fixtures (optional evaluator policy; absence is unpenalized)

## CI output format

Exactly one JSON object on stdout. See [the output schema](08-output-schema.md). Errors and warnings are separate arrays with numeric rule IDs, messages, and reasons. Missing required dependencies terminate on stderr with installation instructions before evaluation.

Arbitrary string-to-string metadata is valid and check 1.5 remains a no-op compatibility placeholder. Script, security, MCP, token-efficiency, and effectiveness checks are evaluator quality policy. A policy error can fail CI without implying invalid Agent Skills format.

## Human output format

```markdown
## Skill: <name>
Baseline: <N> lines, ~<N> tokens

### Errors (<count>)
- **1.4**: allowed-tools must be a space-separated string, got list
  Fix: Use a string such as `allowed-tools: Bash(git:*) Read`.

### Warnings (<count>)
- **3.2**: lines 45-90 contain a 46-row team lookup table (~138 tokens)
  Fix: Move to references/team-lookup.md and load on demand.

### Result: FAIL | WARN | PASS
```
