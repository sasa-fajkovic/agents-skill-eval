# Check Rationale

Why every check exists — the real-world failure mode it prevents.

## Tier 1: Specification Compliance

### 1.1 — name format
**What**: name must be present, 1-64 chars, `[a-z0-9-]`, no leading/trailing/consecutive hyphens, match parent directory.
**Why**: Broken name = broken discovery. Agents match skills by name. A name like `My Skill` or `mySkill` fails pattern matching. A name that doesn't match the directory means the skill can't be found by convention.
**Value**: Skill is discoverable and activatable across all platforms.

### 1.2 — description
**What**: Present, non-empty, max 1024 chars, describes what AND when.
**Why**: The description is the single most important field. It determines whether an agent loads the skill at all. Agents scan descriptions to match tasks — if the description only says "what" but not "when to use it", the agent can't decide whether to activate it. Descriptions are truncated to ~250 chars in listings, so front-loading matters.
**Value**: Skill gets activated for the right tasks, not missed or mis-triggered.

### 1.3 — no non-standard fields
**What**: Only the 5 stable agentskills.io fields allowed in frontmatter.
**Why**: Using `model: sonnet` works in Claude Code but is silently ignored by OpenCode and Codex. Using `allowed-tools: Read Grep` works in Claude Code but is experimental in the base spec and ignored by other platforms. The skill behaves differently across platforms with no error or warning. Use `compatibility:` to signal platform requirements and handle tool scoping in body prose.
**Value**: Skill works identically on every platform that implements agentskills.io.

### 1.4 — field type/value validation
**What**: Each of the 5 stable fields has correct type and constraints.
**Why**: `metadata: "version 1.0"` instead of `metadata: { version: "1.0" }` silently breaks. `compatibility` over 500 chars gets truncated or rejected. Type errors surface as mysterious behavior, not clear error messages.
**Value**: Frontmatter parses correctly on all implementations.

### 1.5 — metadata redundant keys
**What**: WARN per key for metadata entries that duplicate git history (`author`, `version`, `tags`, etc.). Runtime-relevant keys (`argument-hint`, custom keys) are not flagged. CC extensions placed inside metadata emit a 1.3 WARN instead.
**Why**: Every frontmatter token loads on every API call. `author` and `version` are already in git blame and tags. `argument-hint` inside metadata is Claude Code UI sugar — it's useful but placing a CC extension inside `metadata` is a portability smell since it silently does nothing on other platforms.
**Value**: Per-call cost stays minimal. Authors consciously justify each metadata entry rather than copying boilerplate.

### 1.6 — SKILL.md over 500 lines
**What**: ERROR when file exceeds 500 lines.
**Why**: Official skill authoring guidance recommends under 500 lines / 5000 tokens. The full SKILL.md loads when the skill activates. A 1000-line skill costs twice as much per activation and leaves less context window for actual work. Long skills also tend to contain content that belongs in references/ or scripts/.
**Value**: Predictable context cost. Forces content into the right tier of progressive disclosure.

### 1.7 — scripts without matching tests
**What**: Each script (in `scripts/` or skill root) must have a named test file — `.sh` → `tests/<stem>.bats`, `.py` → `tests/test_<stem>.py`. eval.py always emits WARN; the LLM escalates to ERROR for scripts with real logic (>30 lines, conditionals, loops). Thin wrappers stay WARN.
**Why**: Scripts run in production. Untested scripts break silently — a bad jq filter returns empty, a regex misses an edge case. But thin CLI wrappers (just calling `gh api` or `curl`) have little logic to test and mocking the whole external API produces low-confidence tests. The LLM escalation distinguishes the two cases.
**Value**: Complex scripts get verified. Thin wrappers don't accumulate test overhead that adds no confidence.

### 1.8 — scripts must implement --help
**What**: Every script in scripts/ must handle --help.
**Why**: Per agentskills.io, --help is the primary way an agent discovers a script's interface. Without it, the agent must read the source code to understand flags, arguments, and behavior — wasting context window and increasing error rate. A script with good --help is self-documenting.
**Value**: Agents can learn script interfaces without reading source code. Reduces context window usage and trial-and-error.

### 1.9 — structured output preferred
**What**: Scripts should output JSON/CSV/TSV rather than free-form text.
**Why**: Structured output is composable — agents can pipe it through jq, cut, awk. Free-form text requires parsing heuristics that are fragile and model-dependent. JSON is unambiguous; "whitespace-aligned columns" is not. Structured output also enables agent-to-agent data passing.
**Value**: Script output is reliably parseable. Agents can chain scripts and extract specific fields without guessing.

### 1.10 — no interactive prompts
**What**: Scripts must not block on TTY input (read -p, input(), prompt dialogs).
**Why**: Agents run in non-interactive shells. A script that blocks on interactive input hangs indefinitely with no error message. This is a hard constraint of the agent execution environment. All input must come via flags, env vars, or stdin.
**Value**: Scripts never hang. Agents can always run scripts to completion without human intervention.

### 1.11 — only Python and bash scripts
**What**: Scripts must be .py or .sh — no JS/TS, Go, Ruby, etc.
**Why**: Python and bash are pre-installed on every CI runner (Ubuntu, macOS) and every developer machine. Node.js, Go, Ruby, Deno, and Bun are not guaranteed. A skill with a `.ts` script silently fails when Node.js isn't installed. Restricting to Python+bash means skills work everywhere with zero setup.
**Value**: Skills are portable. No "install Node.js first" surprises.

## Tier 2: Security

### 2.1 — body references tools without scoping
**What**: Skill body instructs the agent to use tools (Bash, shell, file operations) without specifying which operations are allowed.
**Why**: Since `allowed-tools` is not part of the stable spec, the security boundary must come from the skill body itself. Instructions like "run any necessary commands" give the agent carte blanche. Instructions like "use only git status, git diff, and git log" constrain behavior through prose — the only portable way to scope tool access.
**Value**: Skill limits agent capabilities regardless of platform, reducing blast radius of errors.

### 2.2 — destructive operations without safeguards
**What**: Skill instructs destructive actions (delete, overwrite, force-push, drop) without confirmation steps, dry-run options, or guardrails.
**Why**: A skill that says "delete the old files" without "first confirm with the user" or "create a backup branch" can cause irreversible damage. The agent follows instructions literally — if the skill doesn't say "check first", the agent won't check.
**Value**: Destructive operations have explicit safety nets. Users don't lose work.

### 2.3 — MCP usage is not allowed
**What**: Skill instructs the agent to use MCP servers or `mcp__*` tools.
**Why**: MCP tool names, schemas, and server availability are platform-specific, so the skill silently stops being portable. They also add persistent token overhead and create another execution surface that the skill author often does not constrain well. If a workflow can be expressed with `gh`, `git`, `acli`, `curl`, or another concrete CLI/API, the portable skill should use that instead.
**Value**: Skills stay portable, cheaper to run, and easier to audit.

### 2.4 — hardcoded user home directory paths
**What**: Skill body or scripts contain absolute paths to a specific user's home directory (e.g., `/Users/john/`, `/home/dev/`).
**Why**: Hardcoded user paths break portability across machines and users. The skill only works on the original author's machine. Use `$HOME`, `~`, or environment variables instead.
**Value**: Skills work on any machine regardless of username or OS.

### 3.1 — inline code should be script
**What**: Code blocks >5 lines should be in scripts/ directory.
**Why**: Script code in SKILL.md loads into context on every activation. The same script in scripts/ only costs tokens when executed — and only the stdout enters context, not the source code. A 30-line bash script inline costs ~100 tokens/call. In scripts/, it costs 0 tokens until run, then only the output cost.
**Value**: Biggest single token savings opportunity. Also improves reliability — scripts are testable, versioned, and don't get reinterpreted by the LLM.

### 3.2 — content belongs in references
**What**: Large tables (>10 rows), ID mappings, lookup data, or rarely-needed details inline in SKILL.md.
**Why**: References load on demand — zero token cost until the agent actually reads them. A 100-row team lookup table in SKILL.md costs ~300 tokens on every call. In references/, it costs 0 unless the agent needs a team ID.
**Value**: Progressive disclosure working as designed. Context window reserved for what's actually needed.

### 3.3 — instructions the agent already knows
**What**: CLI tutorials (how to use git, curl, jq), bash syntax explanations, standard API patterns.
**Why**: The agent already knows how to use standard tools. Explaining `git diff --cached` or `curl -X POST` wastes tokens. The test: "Would the agent make a mistake without this line?" If the agent would do the right thing anyway, the line is waste.
**Value**: Leaner skills that trust the agent's built-in knowledge.

### 3.4 — duplicated content
**What**: Same code block, query, or instruction appearing more than once.
**Why**: Direct waste. If the same GraphQL query appears in two places, that's double the tokens for zero additional information.
**Value**: Each piece of information appears exactly once.

### 3.5 — verbose prose where terse works
**What**: Multi-sentence explanations of simple actions. "First, you need to check the current status of the pull request by running the following command, which will query the GitHub API and return the current state..." vs "Check PR status:".
**Why**: Research shows 40-60% token reduction is possible through concise prompting with no loss in effectiveness. Agents parse terse instructions as well as verbose ones.
**Value**: Lower per-call cost. Faster processing. More context window for actual work.

### 3.6 — references preload instructions
**What**: SKILL.md tells the agent to "read all files in references/" or "load references/ first".
**Why**: Claude Code's progressive disclosure model lazy-loads references by default. Explicitly instructing preload defeats this — the agent reads everything upfront, paying the full token cost regardless of whether it's needed.
**Value**: Preserves the built-in lazy loading optimization.

### 3.7 — MCP references waste tokens even before 2.3 fails the skill
**What**: Skill still discusses or depends on MCP concepts instead of using a concrete CLI/API path.
**Why**: Tier 2.3 already treats MCP usage as a hard failure. Even before portability and security concerns, MCP tool definitions impose ongoing token overhead that portable skills should avoid entirely.
**Value**: Reinforces that removing MCP improves both correctness and cost.

## Tier 4: Effectiveness

### 4.1 — ambiguous instructions
**What**: Vague terms ("appropriately", "as needed", "relevant"), context-dependent language, underspecified edge cases.
**Why**: Ambiguity is the #1 cause of poor LLM output. When an instruction says "handle errors appropriately", every invocation may handle errors differently. Specification defects (ambiguous instructions) are easier to fix than model limitations, and the fix compounds — every future invocation benefits.
**Value**: Consistent, predictable skill behavior across invocations.

### 4.2 — missing concrete examples
**What**: Abstract rules without input/output pairs when the skill has output formatting requirements.
**Why**: Research shows 1-3 concrete examples anchor output format more reliably than lengthy instruction lists. A rule like "use concise technical language" is subjective. An example showing the exact output format is unambiguous. Examples covering normal case + edge case + common mistake give the agent clear patterns to follow.
**Value**: Output format matches expectations on first attempt, not after iteration.

### 4.3 — negative-only framing
**What**: Instructions that say "Don't do X" without specifying what to do instead.
**Why**: Negative framing creates ambiguity — the agent knows what's forbidden but not what's desired. "Don't use verbose language" leaves infinite options. "Use 1-2 sentence summaries with technical specifics" is actionable. Combined framing ("Do X, not Y, because Z") is most effective.
**Value**: Agent knows exactly what to do, not just what to avoid.

### 4.4 — missing default behaviors
**What**: Skill accepts arguments or flags but doesn't specify what happens when they're omitted.
**Why**: When defaults aren't explicit, the model fills in assumptions. Those assumptions may differ between models, versions, or even invocations. "If no branch specified, use current branch" is deterministic. Leaving it unspecified means the agent might ask, guess, or use main — unpredictably.
**Value**: Predictable behavior regardless of how the skill is invoked.

### 4.5 — not idempotent
**What**: Skills or scripts that would fail or produce duplicates if run twice.
**Why**: Agents retry. Network timeouts, tool errors, and context window compaction all trigger re-execution. A skill that creates a Jira ticket without checking if one already exists will create duplicates on retry. "Create if not exists" is always safer than "create and fail on duplicate."
**Value**: Skills are safe to retry. No duplicate resources, no crashes on second run.

### 4.6 — no success criteria
**What**: Skill doesn't define what "done" looks like or what output to produce.
**Why**: Without explicit completion criteria, the agent doesn't know when to stop. It may do too little (stop after the first step) or too much (keep iterating). "Done when: PR is created and URL is printed" is clear. No success criteria means the agent decides — and it may decide wrong.
**Value**: Skill completes reliably at the right point with the expected output.

### 4.7 — scripts lack meaningful exit codes
**What**: Scripts that only return 0/1 without documenting what different codes mean.
**Why**: Distinct exit codes let agents make branching decisions without parsing stderr. "Exit 2 = not found, retry with different query" is actionable. "Exit 1 = something failed" forces the agent to parse error output and guess. Combined with --help documentation, exit codes become a reliable API.
**Value**: Agents can react to specific failure types programmatically.

### 4.8 — minimum content / substance gate
**What**: Skill body has fewer than 10 non-blank lines and no bundled scripts. Redirect/pointer skills that just say "read docs elsewhere" get an ERROR.
**Why**: A skill with 4-5 lines of body text cannot meaningfully guide an agent. Redirect skills are particularly egregious — they provide zero actionable instructions and just point to external docs that the agent may not be able to access. Skills should be self-contained with enough context for the agent to act autonomously.
**Value**: Skills contain enough substance to actually guide agent behavior. Stubs and redirects are surfaced instead of silently scoring high.
