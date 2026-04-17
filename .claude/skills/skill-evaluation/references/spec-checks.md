# Spec Checks (1.1-1.11)

Detection methods, fix suggestions, and examples.

## 1.1: name format

**Regex**: `^[a-z0-9]+(-[a-z0-9]+)*$`

**Checks (all ERROR)**:
1. `name` key exists in frontmatter
2. Value is a string, 1-64 characters
3. Matches regex (lowercase alphanumeric + hyphens, no leading/trailing/consecutive hyphens)
4. Value matches parent directory name

**Detection**:
```
Parse YAML frontmatter between --- delimiters
Extract name value
Apply regex
Compare to basename of skill directory (dirname of SKILL.md path)
```

**Examples**:
```yaml
# ERROR: missing name
---
description: Does something
---

# ERROR: uppercase
name: My-Skill

# ERROR: consecutive hyphens
name: my--skill

# ERROR: leading hyphen
name: -my-skill

# ERROR: name doesn't match directory (skill is in skills/helpers/eval/)
name: skill-eval

# PASS
name: skill-eval  # in skills/helpers/skill-eval/
```

**Fix suggestion**: `Set name to "<dirname>" to match the parent directory.`

## 1.2: description

**Checks**:
1. `description` key exists (ERROR if missing)
2. Value is non-empty string (ERROR if empty)
3. Length <= 1024 chars (ERROR if over)
4. Contains "when" context — heuristic: look for trigger phrases like "use when", "use for", "triggered by", "activate when", or a second sentence describing context (WARN if missing)

**Detection**:
```
Parse frontmatter
Check presence + type + length
Scan for trigger phrases (case-insensitive):
  "use when", "use after", "use before", "use to", "use for",
  "use if", "use whenever", "trigger", "activate",
  "invoke when", "run when", "run this when"
If none found and description is a single clause, WARN
```

**Examples**:
```yaml
# ERROR: missing
---
name: my-skill
---

# ERROR: over 1024 chars
description: [very long string...]

# WARN: no "when" context
description: Creates pull requests from branch context.

# PASS
description: >
  Create pull requests from branch context. Use when you need
  to open a PR or update an existing PR's title and description.
```

**Fix suggestion**: `Add a "Use when..." clause to help agents know when to activate this skill.`

## 1.3: no non-standard fields

**Allowlist (5 stable fields)**:
- `name`
- `description`
- `license`
- `compatibility`
- `metadata`

**Checks (all ERROR)**:
1. Every top-level frontmatter key must be in the allowlist
2. `allowed-tools` flagged as ERROR with note: "experimental in agentskills.io, not part of stable spec"
3. Claude Code extensions flagged as ERROR with note: "Claude Code extension, not portable"

**Detection**:
```
Parse frontmatter keys
For each key not in allowlist:
  If key == "allowed-tools":
    ERROR: "allowed-tools" is experimental in agentskills.io (not stable spec)
  Else if key in CLAUDE_CODE_EXTENSIONS:
    ERROR: "<key>" is a Claude Code extension (not in agentskills.io)
  Else:
    ERROR: "<key>" is not a recognized field
```

Where `CLAUDE_CODE_EXTENSIONS` = {`argument-hint`, `disable-model-invocation`, `user-invocable`, `model`, `effort`, `context`, `agent`, `hooks`, `paths`, `shell`}

**Examples**:
```yaml
# ERROR: model is Claude Code extension
model: sonnet

# ERROR: allowed-tools is experimental
allowed-tools: Read Grep

# ERROR: version is not recognized (should be in metadata)
version: "1.0"

# PASS: custom data in metadata where it belongs
metadata:
  version: "1.0"
  author: team-name
```

**Fix suggestions**:
- For `allowed-tools`: `Move tool scoping into the skill body prose. Use compatibility: to note platform requirements.`
- For Claude Code extensions: `"<key>" is a Claude Code-specific field. Remove from frontmatter. If the behavior is needed, express it in the skill body.`
- For unknown fields: `"<key>" is not in the agentskills.io spec. Move to metadata: if needed, or remove.`

## 1.4: field type/value validation

**Type rules for the 5 stable fields**:
| Field | Type | Constraints |
|-------|------|-------------|
| `name` | string | See 1.1 |
| `description` | string | See 1.2 |
| `license` | string | No constraints beyond being a string |
| `compatibility` | string | Max 500 chars |
| `metadata` | mapping | All keys and values must be strings |

**Checks (all ERROR)**:
1. `license` is a string (not a list, not a mapping)
2. `compatibility` is a string, <= 500 chars
3. `metadata` is a YAML mapping with string keys and string values
4. No field has a null/empty value when present (except metadata which can be empty)

**Detection**:
```
For each present field:
  Check YAML type matches expected type
  Check constraints (length, format)
  For metadata: iterate keys and values, verify all are strings
```

**Examples**:
```yaml
# ERROR: metadata value is not string
metadata:
  tags: [testing, ci]  # arrays are not string-to-string

# ERROR: compatibility over 500 chars
compatibility: [501+ character string...]

# ERROR: license is a list
license:
  - MIT
  - Apache-2.0

# PASS
metadata:
  author: platform-team
  version: "2.1"
license: Apache-2.0
compatibility: Requires git and gh CLI. Designed for Claude Code.
```

**Fix suggestion**: Type-specific message, e.g. `metadata values must be strings. Convert tags list to a comma-separated string.`

## 1.5: metadata redundant keys

**Check (WARN — per key)**:
For each key in the `metadata` mapping, warn if it duplicates information available in git history.

**Redundant keys** (always warn): `author`, `maintainer`, `email`, `version`, `semver`, `created`, `updated`, `date`, `last-modified`, `last_modified`, `tags`, `tag`, `category`, `topic`

**Not flagged** (runtime-relevant): `argument-hint`, `min-version`, or any custom key the skill actually reads at runtime.

**CC extension laundering (1.3 WARN)**: If a key in `metadata` matches a Claude Code extension (`argument-hint`, `disable-model-invocation`, `model`, etc.), emit a 1.3 WARN — placing CC extensions inside `metadata` hides them from the portability check but has no effect on non-CC platforms.

**Detection**:
```
For each key in metadata:
  If key in REDUNDANT_KEYS → WARN 1.5: duplicates git history
  If key in CLAUDE_CODE_EXTENSIONS → WARN 1.3: CC extension in metadata
```

**Fix suggestion**: `metadata key "<key>" duplicates git history — remove it; metadata loads on every API call`

## 1.6: SKILL.md over 500 lines

**Check (ERROR)**:
1. Total line count of SKILL.md > 500

**Detection**: Count newlines in the file.

**Fix suggestion**: `SKILL.md is <N> lines (limit: 500). Move reference data to references/, code to scripts/, and trim verbose prose. See 3.1, 3.2, 3.5 for specific opportunities.`

## 1.7: scripts without matching tests

**Check (WARN — LLM may escalate to ERROR)**:
Each script must have a matching test file. Scripts are searched in both `scripts/` and the skill root.

**Expected test file per script**:
- `scripts/foo.sh` or `foo.sh` → `tests/foo.bats` (bats-core)
- `scripts/bar.py` or `bar.py` → `tests/test_bar.py` (pytest)

**Severity**: Always emitted as WARN by eval.py. The LLM phase escalates to ERROR if the script has conditional logic, loops, non-trivial parsing, or is >30 lines. Thin wrappers (<20 lines, straight CLI calls) stay WARN.

**Detection**:
```
Scan scripts/ directory and skill root for .sh and .py files
For each script:
  Compute expected test path (tests/<stem>.bats or tests/test_<stem>.py)
  If test file missing → WARN 1.7
```

**Examples**:
```
# WARN (thin wrapper): jira-create.sh at root, no tests/jira-create.bats
# ERROR (complex): yolo.sh (105 lines, loops, conditionals), no tests/yolo.bats

# PASS
├── scripts/fetch-data.sh
└── tests/fetch-data.bats
```

**Fix suggestion**: `<script> has no matching test — expected <path> (<framework>)`

## 1.8: scripts must implement --help

Per [agentskills.io best practices](https://agentskills.io/skill-creation/using-scripts#designing-scripts-for-agentic-use), `--help` output is the primary way an agent learns a script's interface.

**Check (ERROR)**:
1. Every script in `scripts/` must handle `--help`
2. Help output should include: brief description, available flags, usage examples

**Detection**:
```
For each script file in scripts/:
  Search for --help handling patterns:
    - "--help" or '--help' string literals
    - argparse / ArgumentParser (Python)
    - getopts / getopt (bash)
    - usage() function definition
    - "Usage:" string
  If none found → ERROR
```

**Examples**:
```bash
# ERROR: no --help handling
#!/usr/bin/env bash
curl -s "https://api.example.com/$1" | jq .

# PASS: handles --help
#!/usr/bin/env bash
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "Usage: fetch-data.sh <endpoint>"
  echo ""
  echo "Fetches data from the API and outputs JSON."
  echo ""
  echo "Options:"
  echo "  --help  Show this help"
  exit 0
fi
```

**Fix suggestion**: `Add --help flag handling that prints: description, available flags, usage examples, and exit codes.`

## 1.9: scripts should use structured output

Per agentskills.io best practices, prefer JSON/CSV/TSV over free-form text. Structured formats are composable and parseable by both agents and standard tools.

**Check (WARN)**:
1. Script produces output (has print/echo statements)
2. No evidence of structured format (JSON, CSV patterns)

**Detection**:
```
For each script:
  Check for output statements (print, echo, console.log, printf)
  Check for structured output indicators:
    - json.dumps, JSON.stringify, jq, ConvertTo-Json
    - csv.writer, csv.DictWriter
    - --format json, --output-format flags
  If has output but no structured format → WARN
```

**Fix suggestion**: `Script produces output but no structured format detected. Use JSON for complex data. Separate data (stdout) from diagnostics (stderr).`

## 1.10: scripts must not use interactive prompts

Agents operate in non-interactive shells. Scripts that block on TTY input will hang indefinitely.

**Check (ERROR)**:
1. Script contains interactive prompt patterns

**Detection**:
```
Scan for:
  - read -p (bash interactive read)
  - input() (Python)
  - prompt() (Node.js)
  - readline (interactive line reading)
  - inquirer / enquirer (TUI prompt libraries)
```

**Fix suggestion**: `Interactive prompt detected. Agents cannot respond to prompts. Accept all input via command-line flags, environment variables, or stdin.`

## 1.11: only Python and bash scripts allowed

Python and bash are pre-installed on all CI runners and macOS/Linux dev machines. Other languages (JS/TS, Go, Ruby) require additional runtimes that may not be present.

**Check (ERROR)**:
1. Every file in `scripts/` must have a `.py` or `.sh` extension

**Detection**:
```
For each file in scripts/:
  If extension is not .py or .sh → ERROR
```

**Examples**:
```
# ERROR: requires Node.js runtime
scripts/fetch-data.js
scripts/process.ts

# PASS: no additional runtime needed
scripts/fetch-data.sh
scripts/process.py
```

**Fix suggestion**: `Only Python (.py) and bash (.sh) scripts are allowed. Rewrite in Python or bash to avoid additional runtime dependencies.`
