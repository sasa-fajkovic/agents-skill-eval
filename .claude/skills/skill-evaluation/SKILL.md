---
name: skill-evaluation
description: >
  Evaluate a SKILL.md against the agentskills.io open standard for spec
  compliance, security, token efficiency, and effectiveness. Use when
  reviewing or validating a skill for cross-platform portability.
compatibility: Designed for Claude Code. Uses Read, Glob, Grep, and Bash tools.
---

# Skill Evaluator

Validate a SKILL.md against the agentskills.io stable spec (5 fields only). Non-standard fields, experimental fields, platform-specific extensions, and MCP usage instructions are errors.

## Tool constraints

Use Read, Glob, Grep, and Bash tools. Do not modify any files. Bash is used only to run the deterministic evaluator script.

## Input

`/skill-evaluation <path-to-SKILL.md> [--ci] [--help]`

- `<path>`: Path to a SKILL.md file or a skill directory
- `--ci`: Machine-readable output with exit codes
- `--help`: Print the Input section and stop

## Process

1. **Resolve path**: If given a directory, look for SKILL.md inside it. If given a name, search `skills/` directories.

2. **Read the skill**: Read SKILL.md and list the skill directory contents (references/, scripts/, etc.).

3. **Measure baseline**: Count lines, estimate tokens (~4 tokens/line for code, ~0.75 tokens/word for prose). Count fenced code blocks and their total lines.

### Phase 1: Deterministic evaluation

4. **Run Tier 1-2 via eval.py**: Run `python3 scripts/eval.py <path>` via Bash (resolve the script path relative to this skill's directory). The script produces boxed, colored output for Tier 1 (Spec Compliance 1.1-1.11) and Tier 2 (Security 2.1-2.3). Skills must not instruct agents to use MCP servers or `mcp__*` tools; require CLI or direct API alternatives instead. **Important**: the tool output gets truncated by the harness UI. After running the script, reproduce its ENTIRE output as your own text response so the user sees all findings without needing to expand collapsed output.

### Phase 2: LLM evaluation

5. **Run Tier 3 checks (Token Efficiency)**: Print a blue separator line: `────┤ TIER 3 — TOKEN EFFICIENCY ├────`. Read the skill body and reason about 3.1-3.7 patterns. Load `references/token-checks.md` for thresholds and heuristics. Print each check result with colored status on one line.

6. **Run Tier 4 checks (Effectiveness)**: Print a blue separator line: `────┤ TIER 4 — EFFECTIVENESS ├────`. Read and reason about skill body for 4.1-4.7. Load `references/effectiveness-checks.md` for assessment guidance. Print each check result with colored status on one line.

   **1.7 escalation**: If Phase 1 reported any 1.7 warnings (missing tests), read each flagged script. If the script has conditional logic, loops, non-trivial parsing, or is >30 lines, re-classify as 🔴 ERROR 1.7 in the Final Report. If it is a thin wrapper (straight CLI calls, <20 lines, no branching), keep as 🟡 WARN 1.7.

### Final report

7. **Produce final summary**: Print a blue separator line: `━━━━━━━┤ FINAL RESULT ├━━━━━━━`. Combine ALL findings from all tiers (deterministic + LLM). Group errors first (🔴), then warnings (🟡). For each error, load `references/check-rationale.md` and include the WHY. End with a prominent result banner matching the style of the deterministic result banner.

## Output

The evaluator has two output modes.

### Machine-readable mode (`--ci`)

The evaluator must print exactly one JSON object to stdout and nothing else. No prose, markdown, banners, or trailing text.

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "skill_name": "string",
  "overall_score": 85,
  "quality_tier": "good",
  "summary": "One sentence summary of the skill quality",
  "deterministic": {
    "passed": 4,
    "failed": 2,
    "issues": [
      {
        "rule_id": "missing_trigger",
        "severity": "error",
        "message": "Missing trigger/activation criteria",
        "reason": "Trigger conditions define when the skill activates. Without them, the agent relies on guesswork."
      }
    ]
  },
  "llm_analysis": {
    "strengths": [
      {"finding": "string", "reason": "string"}
    ],
    "weaknesses": [
      {"finding": "string", "reason": "string"}
    ],
    "suggestions": [
      {"finding": "string", "reason": "string"}
    ],
    "security_flags": [
      {"finding": "string", "reason": "string"}
    ]
  },
  "metadata": {
    "file_count": 3,
    "line_count": 145,
    "has_scripts": true,
    "script_types": [".py", ".sh"],
    "unsupported_script_types": [".js"]
  }
}
```

Constraints:

- `schema_version` must be present so downstream agents can detect breaking changes.
- `status` must be `"ok"` for successful evaluation.
- `overall_score` must be an integer `0-100`.
- `quality_tier` must be one of `excellent`, `good`, `needs_work`, or `poor`.
- `severity` must be one of `error`, `warning`, or `info`.
- Every issue/finding must include a `reason` so no follow-up LLM call is needed to explain it.
- All arrays must always be present as `[]`, never `null`.
- `metadata.unsupported_script_types` must flag non-portable runtimes such as `.js`, `.ts`, or `.go`.

### Human mode (default)

Done when the report is printed with a final PASS, WARN, or FAIL result.

### Human mode (default)

Use colors throughout: 🟢 green = PASS, 🟡 yellow = WARN, 🔴 red = ERROR/FAIL. Use blue separator lines between phases.

Phase 1 output comes from eval.py (already formatted with boxes). For Phase 2, put the status emoji at the START of each line, not the end:

```
────┤ TIER 3 — TOKEN EFFICIENCY ├────

  🟢 **3.1**: <one-line assessment>
  🟡 **3.2**: <one-line assessment>
     Fix: <suggestion>
  🟢 **3.3**: <one-line assessment>

────┤ TIER 4 — EFFECTIVENESS ├────

  🟢 **4.1**: <one-line assessment>
  🟡 **4.5**: <one-line assessment>
     Fix: <suggestion>
  🔴 **4.7**: <one-line assessment>
     Fix: <suggestion>

━━━━━━━━━━━━━━━━━━┤ FINAL RESULT ├━━━━━━━━━━━━━━━━━━

Errors (<count>):
  🔴 **1.3**: "model" is a Claude Code extension
     Why: <rationale from check-rationale.md>

Warnings (<count>):
  🟡 **3.2**: 15-row lookup table inline
     Fix: Move to references/

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
              🟢 PASS | 🟡 WARN | 🔴 FAIL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## Rules

1. Be specific: cite exact line numbers and content.
2. Do not suggest changes that reduce reliability (e.g., removing hardcoded values the agent can't guess).
3. Do not nitpick formatting or markdown style — focus on spec compliance, security, tokens, and effectiveness.
4. Load reference files on demand, not upfront. Read only the reference file relevant to the current tier being evaluated.
5. Treat MCP usage in evaluated skills as disallowed, not merely suboptimal. Flag any positive instruction to use MCP servers or `mcp__*` tools as a security/portability failure and recommend CLI or direct API alternatives.
6. Prefer `.sh` and `.py` for bundled scripts. Treat `.js`, `.ts`, `.go`, and similar runtime-dependent script types as portability warnings and surface them in `metadata.unsupported_script_types`.
