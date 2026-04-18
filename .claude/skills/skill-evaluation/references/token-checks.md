# Token Checks (3.1-3.7)

Detection heuristics, thresholds, and examples. Token estimate: ~0.75 tokens per word, ~4 tokens per line of code.

## 3.1: inline code should be script

**Threshold**: Code blocks (fenced with ```) exceeding 5 lines.

**Detection**:
```
Find all fenced code blocks in SKILL.md body (after frontmatter)
For each block:
  Count lines between opening and closing fences
  If > 5 lines → WARN
  Estimate savings: lines * 4 tokens
```

**Indicators of script-worthy code**:
- Hardcoded values, URLs, IDs
- Multi-step pipelines (pipes, &&)
- curl/wget calls with headers
- jq/yq processing chains
- Loop constructs
- Error handling logic

**Do NOT flag**:
- Example output format blocks (showing expected output, not executable code)
- YAML/JSON schema examples (documentation, not execution)
- Single-line commands shown as reference

**Examples**:

````markdown
# WARN: 8-line script inline
```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
TICKET=$(echo "$BRANCH" | grep -oP 'PTECH-\d+')
STATUS=$(curl -s -H "Authorization: Bearer $TOKEN" \
  "https://jira.example.com/rest/api/3/issue/$TICKET" | \
  jq -r '.fields.status.name')
echo "Ticket: $TICKET"
echo "Status: $STATUS"
```

# PASS: 3-line command reference
```bash
git status
git diff --cached
git log --oneline -5
```
````

**Fix suggestion**: `Code block at lines <start>-<end> (<N> lines, ~<N> tokens) should be a script. Move to scripts/<suggested-name>.sh and reference as: Run scripts/<suggested-name>.sh`

## 3.2: content belongs in references

**Threshold**: Tables with >10 rows, mapping blocks >15 lines, or dense reference data sections.

**Detection**:
```
Scan for markdown tables: count rows (lines starting with |)
  If > 10 data rows (excluding header + separator) → WARN

Scan for YAML/JSON mapping blocks in code fences:
  If > 15 lines → WARN

Scan for list-style lookup data:
  Repeated "- key: value" or "| key | value |" patterns > 10 entries → WARN
```

**Common offenders**:
- Team/user ID lookup tables
- API field documentation
- Error code mappings
- Status transition tables with IDs
- Configuration reference tables

**Do NOT flag**:
- Behavior tables showing flag/input → action mappings (these ARE the skill logic)
- Small reference tables (<= 10 rows) that are needed on every invocation

**Fix suggestion**: `Lines <start>-<end> contain a <N>-row lookup table (~<N> tokens). Move to references/<suggested-name>.md and load on demand.`

## 3.3: instructions the agent already knows

**Detection heuristic**: Scan for patterns that explain standard tool usage.

**Flag these**:
- Step-by-step explanations of CLI tools: "To check the status of your git repository, run `git status`"
- Bash syntax tutorials: "Use `$(...)` for command substitution"
- Standard API patterns: "Send a POST request with Content-Type: application/json"
- Tool option explanations: "The `-s` flag makes curl silent"
- Piping explanations: "Pipe the output to jq to parse JSON"

**Do NOT flag**:
- Non-standard tool usage: custom scripts, internal APIs, unusual flags
- Domain-specific commands: company-internal CLIs, custom workflows
- Hardcoded values the agent can't guess: API endpoints, project IDs, field names
- Exact command sequences that must be run in order (the order IS the value)

**Test**: For each flagged line, ask: "If I removed this line, would the agent do the wrong thing?"

**Fix suggestion**: `Lines <start>-<end> explain standard <tool> usage that the agent already knows. Remove or condense to just the command.`

## 3.4: duplicated content

**Detection**:
```
Extract all fenced code blocks
Compare each pair:
  Exact match → WARN (definite duplicate)
  >80% similar (by line) → WARN (near duplicate)

Extract all multi-line instruction blocks (>3 lines)
Compare for repeated phrases or near-identical wording
```

**Fix suggestion**: `Code block at line <N> duplicates block at line <M>. Keep one instance and reference it.`

## 3.5: verbose prose where terse works

**Detection heuristic**: Scan for verbose patterns.

**Flag these**:
- Paragraphs > 3 sentences that describe a single action
- Phrases like "First, you need to...", "In order to...", "The next step is to..."
- Explanatory clauses before commands: "To accomplish this, run the following command which will..."
- Restating what a code block already shows

**Do NOT flag**:
- Decision logic (if/then explanations for complex branching)
- Edge case documentation (these ARE the value)
- Examples with context (the context helps the agent)

**Fix suggestion**: `Lines <start>-<end> use <N> words to describe a simple action. Condense. Example: "<original>" → "<suggested>"`

## 3.6: references preload instructions

**Detection**: Scan body for patterns that instruct preloading.

**Flag these**:
- "Read all files in references/"
- "Load references/ first"
- "Start by reading every file in references/"
- "Pre-load the following references:"
- Any instruction to read ALL reference files before starting work

**Do NOT flag**:
- "Read references/teams.md when you need a team ID" (conditional, on-demand)
- "See references/ for details" (informational, not a preload instruction)
- "If X, read references/Y.md" (conditional loading)

**Fix suggestion**: `Line <N> instructs preloading all references, defeating lazy loading. Change to conditional loading: "Read references/<file>.md when <condition>."`

## 3.7: MCP references waste tokens and should be removed

**MCP tool overhead** (tool definitions load on every API call):
| MCP Server | Approx. overhead/call | CLI Alternative | CLI cost |
|------------|----------------------|-----------------|----------|
| GitHub | ~55K tokens | `gh` CLI | ~500 tokens |
| Atlassian/Jira | ~10K tokens | `curl` + REST API | ~500 tokens |
| Google Workspace | ~2-4K tokens | Platform-specific CLIs | Variable |

**Detection**:
```
Scan body for MCP tool references:
  Patterns: "mcp__", "MCP", tool names matching known MCP servers
If Tier 2.3 already flagged MCP usage:
  Do not add a duplicate warning unless the skill also spends significant space explaining MCP setup
Else if the skill still discusses MCP concepts or setup cost:
  WARN that the content is wasting tokens on a disallowed integration path
```

**Fix suggestion**: `Remove MCP references and setup guidance entirely. Use a concrete CLI or direct API workflow instead so the skill stays portable and avoids MCP overhead.`
