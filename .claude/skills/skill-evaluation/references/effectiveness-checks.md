# Effectiveness Checks (4.1-4.7)

LLM-assessed checks. These require reading and reasoning about the skill body — they cannot be purely mechanical.

## 4.1: ambiguous instructions

**What to flag**: Instructions where the agent must guess what's intended.

**Ambiguity indicators**:
- Weasel words: "appropriately", "as needed", "relevant", "suitable", "proper", "reasonable"
- Unbounded scope: "handle errors", "clean up", "prepare the environment"
- Context-dependent terms without definition: "the correct format", "the standard approach"
- Conditional without specifying the condition: "if applicable", "when necessary"
- Subjective quality terms without examples: "concise", "clear", "well-structured"

**Not ambiguous**:
- Weasel words with immediate context: "Use concise language (1-2 sentences per point)"
- Domain terms defined earlier in the skill
- Instructions that reference concrete artifacts: "Format as the example in the Output section"

**Assessment approach**: Read each instruction and ask: "Could two different agents interpret this differently?" If yes, flag it.

**Fix suggestion**: Replace vague term with specific criteria. Example: "Write a clear summary" → "Write a 2-3 sentence summary covering: what changed, why, and what to test."

## 4.2: missing concrete examples

**When to flag**: The skill specifies output format or style requirements but provides no input/output examples.

**Indicators that examples are needed**:
- Output section describes a format without showing it
- Style rules like "use natural language" without a before/after
- Template references without the actual template
- Tone guidance without examples of correct vs incorrect tone

**Not missing**:
- Skills that produce standard outputs (exit codes, file paths, URLs) — format is self-evident
- Skills that delegate formatting to another tool (e.g., "use gh pr create" — gh handles format)

**Assessment approach**: Find all output/format/style instructions. For each, check if a concrete example exists within 20 lines. If the skill has an Output section, it must contain at least one example.

**Fix suggestion**: `Output format specified at line <N> but no example provided. Add 1-2 input/output examples showing the expected format.`

## 4.3: negative-only framing

**What to flag**: Instructions that say what NOT to do without specifying what TO do.

**Negative-only patterns**:
- "Don't use verbose language" (what to use instead?)
- "Never include implementation details" (what to include instead?)
- "Avoid jargon" (what register to use instead?)
- "Do not modify files outside the project" (which files CAN be modified?)

**Acceptable negative framing** (has a positive counterpart):
- "Use 1-2 sentence summaries, not paragraphs" (positive first, negative as contrast)
- "Write in imperative mood. Do not use passive voice." (positive + negative pair)
- Anti-pattern lists with a corresponding pattern list

**Assessment approach**: For each negative instruction ("don't", "never", "avoid", "do not"), check if a positive alternative is specified within 3 lines.

**Fix suggestion**: `Line <N> says what not to do ("Don't <X>") but not what to do instead. Add the desired behavior: "Do <Y>, not <X>."`

## 4.4: missing default behaviors

**When to flag**: Skill accepts arguments or flags but doesn't specify default behavior when they're omitted.

**Indicators**:
- Input section lists arguments/flags
- Body references `$ARGUMENTS` or `$0`, `$1` etc.
- Body mentions flags like `--flag` or `-f`
- Body has conditional behavior based on input

**For each argument/flag, check**:
1. Is there a default value specified? ("defaults to main", "if omitted, uses current branch")
2. Is there behavior for the missing case? ("if no argument provided, analyze all changed files")
3. Is the argument marked as required? ("required: the branch name")

If none of the above → flag.

**Assessment approach**: Extract all arguments and flags from the Input section. For each, trace through the Process/Behavior sections to find default handling.

**Fix suggestion**: `Flag "<flag>" has no default behavior specified. Add: "If <flag> is omitted, <default behavior>."`

## 4.6: no success criteria

**What to flag**: Skill has no definition of what "done" looks like.

**Indicators of success criteria present**:
- An "Output" section describing what the skill produces
- Explicit completion statement: "Done when...", "Complete when..."
- Final step in Process that produces a specific artifact
- Expected output format (even if brief)

**Indicators of missing success criteria**:
- Process section ends with an open-ended instruction ("continue as needed")
- No Output section
- No final step that produces something concrete
- Ambiguous ending: "ensure everything is correct"

**Assessment approach**: Check for Output section or completion criteria in the last 2 steps of the Process section. If neither exists, flag.

**Fix suggestion**: `No success criteria found. Add an Output section defining what the skill produces when done, or add a completion condition to the final process step.`

## 4.5: not idempotent

**What to flag**: Skills or scripts that would fail or cause problems if run twice.

Per [agentskills.io best practices](https://agentskills.io/skill-creation/using-scripts#designing-scripts-for-agentic-use), agents may retry commands. "Create if not exists" is safer than "create and fail on duplicate."

**Idempotency violation indicators**:
- "Create" without "if not exists" or existence check
- Append operations without deduplication
- Counter increments without idempotency keys
- File writes that don't check for existing content
- API calls that create resources without checking for prior existence

**Acceptable non-idempotent patterns**:
- Explicitly documented as non-idempotent with a reason
- Has a `--force` flag to handle re-runs
- The operation is inherently one-shot (e.g., "send notification") and documented as such

**Assessment approach**: For each action in the Process section, ask: "If the agent runs this step twice, does something break or produce duplicates?" If yes and no guard exists, flag it.

**Fix suggestion**: `Step <N> is not idempotent — running twice would <consequence>. Add an existence check or use "create if not exists" pattern.`

## 4.7: scripts lack meaningful exit codes

**What to flag**: Skills that reference scripts but don't document exit codes, or scripts that only use 0/1.

Per agentskills.io best practices, use distinct exit codes for different failure types (not found, invalid arguments, auth failure) and document them in --help.

**Assessment approach**: Check if the skill or its --help output documents exit codes beyond 0/1. Check if scripts use distinct exit codes for different error types.

**Fix suggestion**: `Document exit codes in --help output. Use distinct codes for different failures (e.g., 1=invalid args, 2=not found, 3=auth failure).`

