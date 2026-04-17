# Security Checks (2.1-2.2)

Detection methods and examples. Since `allowed-tools` is not part of the stable agentskills.io spec, security scoping must come from the skill body prose.

## 2.1: body references tools without scoping

**What**: Skill body instructs the agent to use tools without specifying which operations are allowed.

**Check (WARN)**: Scan the skill body for tool usage instructions that lack constraints.

**Unscoped patterns (flag these)**:
- "run any necessary commands"
- "execute the required operations"
- "use Bash to..." (without specifying which commands)
- "run shell commands" (without listing allowed commands)
- References to `Bash` or `shell` without a following constraint list

**Scoped patterns (do NOT flag these)**:
- "Use only git status, git diff, and git log commands"
- "Run: `git status`" (specific command)
- "Execute `scripts/deploy.sh` with the branch name"
- A "Tools" or "Allowed operations" section listing specific commands

**Detection heuristic**:
```
Scan body for tool-related keywords: "bash", "shell", "command", "execute", "run"
For each match, check surrounding context (within 3 lines):
  If a specific command list, tool name list, or constraint follows → PASS
  If the instruction is open-ended → WARN
```

**Examples**:

```markdown
# WARN: unscoped
Run whatever commands are needed to check the repository state.

# WARN: unscoped
Use Bash to complete the task.

# PASS: scoped in prose
Use only the following commands: git status, git diff, git log.
Do not use git push, git reset, or any destructive operations.

# PASS: specific command
Run: `gh pr view --json title,body`
```

**Fix suggestion**: `Add a tool constraints section listing exactly which commands/tools the skill may use. Example: "Use only git status, git diff, and git log. Do not modify files or push changes."`

## 2.2: destructive operations without safeguards

**What**: Skill instructs destructive actions without confirmation, dry-run, or backup steps.

**Check (WARN)**: Scan body for destructive operation patterns without nearby safeguard patterns.

**Destructive patterns**:
- `rm`, `rm -rf`, `delete`, `remove`
- `git push --force`, `git push -f`
- `git reset --hard`
- `git checkout -- .`, `git restore .`
- `git branch -D`
- `drop table`, `truncate`
- `overwrite`, `replace` (in context of files/data)
- `kill`, `pkill`, `kill -9`

**Safeguard patterns (within 5 lines of destructive pattern)**:
- "confirm with the user", "ask the user", "prompt for confirmation"
- "create a backup", "backup branch", "save a copy"
- "dry-run", "preview", "--dry-run"
- "check first", "verify before"
- "only if the user explicitly"

**Detection heuristic**:
```
For each destructive pattern found:
  Search within +-5 lines for safeguard patterns
  If no safeguard found → WARN
  If safeguard found → PASS
```

**Examples**:

```markdown
# WARN: destructive without safeguard
Delete the old branch after merge.

# PASS: destructive with safeguard
Create a backup branch, then delete the old branch after merge.

# PASS: destructive with confirmation
Ask the user to confirm before running git push --force.

# PASS: dry-run available
Support --dry-run flag to preview changes without executing.
```

**Fix suggestion**: `Destructive operation "<operation>" found without safeguards. Add a confirmation step, backup, or dry-run option before: <quoted line>.`
