---
name: skill-evaluation
description: >
  Evaluate a SKILL.md against the agentskills.io open standard for spec
  compliance, security, token efficiency, and effectiveness. Use when
  reviewing or validating a skill for cross-platform portability.
compatibility: Designed for Claude Code. Uses Read, Glob, Grep, and Bash tools.
---

# Skill Evaluator

Validate a SKILL.md against the agentskills.io stable spec (5 fields only). Non-standard fields, experimental fields, and platform-specific extensions are an error. MCP usage is evaluated per the namespace policy in `references/mcp-policy.md`.

## Tool constraints

Use Read, Glob, Grep, and Bash tools. Do not modify any files. Bash is used only to run the deterministic evaluator script.

## Input

`/skill-evaluation <path-to-SKILL.md> [--ci] [--help]`

- `<path>`: Path to a SKILL.md file or a skill directory (required)
- `--ci`: Machine-readable JSON output with exit codes. If omitted, defaults to human-readable mode.
- `--help`: Print the Input section and stop. If omitted, runs the evaluation.

## Process

1. **Resolve path**: If given a directory, look for SKILL.md inside it. If given a name, search `skills/` directories.

2. **Read the skill**: Read SKILL.md and list the skill directory contents (references/, scripts/, etc.).

3. **Measure baseline**: Count lines, estimate tokens (~4 tokens/line for code, ~0.75 tokens/word for prose). Count fenced code blocks and their total lines.

### Phase 1: Deterministic evaluation

4. **Run all tiers via eval.py**: Run `python3 scripts/eval.py <path>` via Bash (resolve the script path relative to this skill's directory). The script runs all four tiers deterministically:
   - **Tier 1** (Spec Compliance 1.1-1.11): Frontmatter validation, script checks, test coverage. Check 1.7 automatically escalates to ERROR for complex untested scripts (>30 lines or conditional logic).
   - **Tier 2** (Security 2.1-2.2, 2.4): Tool scoping, destructive operations, hardcoded user paths.
   - **Tier 3** (Token Efficiency 3.1-3.7): Inline code, reference data, duplication, verbose prose, preload instructions, MCP namespace policy (see `references/mcp-policy.md`).
   - **Tier 4** (Effectiveness 4.1-4.8): Ambiguity, examples, negative framing, defaults, idempotency, success criteria, exit codes, minimum content gate.

   Skills must apply the MCP namespace policy from `references/mcp-policy.md`: allowed namespaces (e.g. Figma, Slack) pass, blocked namespaces (e.g. GitHub, Atlassian) with strictly better CLI alternatives are ERRORs, and everything else is a WARN needing per-case review. **Important**: the tool output gets truncated by the harness UI. After running the script, reproduce its ENTIRE output as your own text response so the user sees all findings without needing to expand collapsed output.

### Phase 2: LLM review (optional)

5. **Review edge cases**: If the deterministic output contains warnings that may be false positives (e.g., 4.1 ambiguity flags on lines that have nearby context), briefly note which findings you agree with and which you'd dismiss. Do not re-run the checks — add judgment only where the regex-based approach is insufficient.

### Final report

6. **Produce final summary**: Print a blue separator line: `━━━━━━━┤ FINAL RESULT ├━━━━━━━`. Reproduce the full eval.py output, then add any LLM-only observations from Phase 2. Group errors first (🔴), then warnings (🟡). For each error, load `references/05-check-rationale.md` and include the WHY. End with a prominent result banner matching the style of the deterministic result banner.

## Output

The evaluator has two output modes.

### Machine-readable mode (`--ci`)

The evaluator must print exactly one JSON object to stdout. See `references/08-output-schema.md` for the full schema with example. Key fields: `schema_version`, `status`, `skill_name`, `overall_score` (0-100), `overall_tier`, `summary`, `checks_overview`, `findings` (grouped into `error_findings` and `warning_findings`), and `metadata`.

Constraints:

- `overall_tier` must be one of `excellent`, `good`, `needs_work`, or `poor`.
- `rule_id` uses numeric check IDs (e.g., `"1.3"`, `"4.6"`).
- Every finding must include a `reason` so no follow-up LLM call is needed to explain it.
- All findings arrays must always be present as `[]`, never `null`.
- `metadata.unsupported_script_types` must flag non-portable runtimes such as `.js`, `.ts`, or `.go`.

### Human mode (default)

Done when the report is printed with a final PASS, WARN, or FAIL result.

Use colors throughout: 🟢 green = PASS, 🟡 yellow = WARN, 🔴 red = ERROR/FAIL. Use blue separator lines between phases.

All tier output comes from eval.py (already formatted with boxes). The LLM adds judgment only for edge-case review in Phase 2.

## Rules

1. Be specific: cite exact line numbers and content.
2. Do not suggest changes that reduce reliability (e.g., removing hardcoded values the agent can't guess).
3. Do not nitpick formatting or markdown style — focus on spec compliance, security, tokens, and effectiveness.
4. Load reference files on demand, not upfront. Read only the reference file relevant to the current tier being evaluated.
5. Apply the MCP namespace policy from `references/mcp-policy.md`: allowed namespaces pass, blocked namespaces are ERRORs with a suggested CLI, everything else is a WARN needing per-case review.
6. Prefer `.sh` and `.py` for bundled scripts. Treat `.js`, `.ts`, `.go`, and similar runtime-dependent script types as portability warnings and surface them in `metadata.unsupported_script_types`.
