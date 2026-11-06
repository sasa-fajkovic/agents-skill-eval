# Check Rationale

Why every check exists — the real-world failure mode it prevents.

## Tier 1: Specification and evaluator policy

### 1.1 — name format
**What**: Required string, 1–64 characters, lowercase Unicode alphanumeric characters or single interior hyphens, matching the parent directory after NFKC normalization. No word-count limit.
**Why**: The current specification and linked skills-ref validator define discoverable skill identifiers. Matching normalized names avoids mismatches caused by equivalent Unicode spellings.

### 1.2 — description
**What**: Required non-empty string, at most 1024 characters (ERROR). Optional evaluator heuristics warn above 350 characters and for missing recognized trigger phrases.
**Why**: Descriptions help agents select skills. The optimization warnings are project heuristics, not additional specification limits or required literal wording.

### 1.3 — documented fields and extensions
**What**: Accept all six documented fields, including experimental `allowed-tools`. Known platform extensions WARN; unknown top-level fields ERROR.
**Why**: Experimental support can vary without making a field invalid. Runtime-specific extension behavior can reduce portability. Custom string data belongs in metadata.

### 1.4 — field types
**What**: Validate documented field types and length constraints. `allowed-tools` is a space-separated string. Metadata is an arbitrary string-to-string mapping.
**Why**: Safe YAML parsing preserves types so malformed structures can be diagnosed. Quote numbers, dates, and boolean-like metadata to keep them strings.

### 1.5 — metadata compatibility placeholder
**What**: No findings; the ID remains for compatibility.
**Why**: The specification permits arbitrary string-to-string metadata and explicitly illustrates author and version. Penalizing these keys would reject valid examples.

### 1.6 — recommended SKILL.md length
**What**: WARN at 500 or more lines, including reference skills.
**Why**: The specification recommends under 500 lines for progressive disclosure. This is a recommendation, not a hard validity limit or an 800-line exception.

### 1.8 — entrypoint help (evaluator policy)
**What**: ERROR when entrypoint scripts lack static help indicators.
**Why**: Help lets agents discover interfaces without reading source. This static quality heuristic is not a format requirement.

### 1.9 — structured output (evaluator policy)
**What**: WARN when output lacks structured-format indicators.
**Why**: Structured output is easier to compose and validate. Human-readable output can still be intentional and specification-valid.

### 1.10 — interactive prompts (evaluator policy)
**What**: ERROR for prompt patterns without nearby fallback indicators.
**Why**: Unattended execution can hang on interactive input. This is execution quality policy, not an Agent Skills format requirement.

### 1.11 — runtime portability (evaluator policy)
**What**: Prefer `.py` and `.sh`; WARN on other script extensions, with no language ERROR.
**Why**: Runtime availability depends on the environment. The specification permits supported languages. Document dependencies in compatibility rather than classifying a language as invalid. The legacy `unsupported_script_types` field preserves its existing runtime set for consumers.

## Tier 2: Security (evaluator policy)

### 2.1 — body references tools without scoping
**What**: Skill body instructs the agent to use tools (Bash, shell, file operations) without specifying which operations are allowed.
**Why**: The experimental `allowed-tools` field has implementation-dependent support, so body instructions should also explain intended tool scope. Instructions like "run any necessary commands" give the agent carte blanche. Instructions like "use only git status, git diff, and git log" constrain behavior through prose across runtimes.
**Value**: Skill limits agent capabilities regardless of platform, reducing blast radius of errors.

### 2.2 — destructive operations without safeguards
**What**: Skill instructs destructive actions (delete, overwrite, force-push, drop) without confirmation steps, dry-run options, or guardrails.
**Why**: A skill that says "delete the old files" without "first confirm with the user" or "create a backup branch" can cause irreversible damage. The agent follows instructions literally — if the skill doesn't say "check first", the agent won't check.
**Value**: Destructive operations have explicit safety nets. Users don't lose work.

### 2.4 — hardcoded user home directory paths
**What**: Skill body or scripts contain absolute paths to a specific user's home directory (e.g., `/Users/john/`, `/home/dev/`).
**Why**: Hardcoded user paths break portability across machines and users. The skill only works on the original author's machine. Use `$HOME`, `~`, or environment variables instead.
**Value**: Skills work on any machine regardless of username or OS.

## Tier 3: Token Efficiency (evaluator policy)

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

### 3.7 — MCP namespace policy
**What**: Scan for `mcp__<namespace>_*` tool tokens and generic MCP prose references. Allowed namespaces (e.g. Figma, Slack) pass. Blocked namespaces (e.g. GitHub, Atlassian) with strictly better CLI alternatives are ERROR. Everything else is WARN for per-case review.
**Why**: Some MCP servers (Figma, Slack, org-internal services) have no portable CLI alternative — banning them outright forces worse workflows. Others (GitHub, Atlassian) have strictly better, cheaper CLI tools (`gh`, `acli`). A namespace-aware policy lets the evaluator distinguish between the two. MCP lives in Tier 3 (token efficiency), not Tier 2 (security), because the concern is overhead and portability rather than safety. See [`mcp-policy.md`](mcp-policy.md) for the current namespace lists.
**Value**: Skills can use MCP where it's the only good path, are blocked from MCP where a CLI is strictly better, and new namespaces get a WARN for human review.

## Tier 4: Effectiveness (evaluator policy)

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
