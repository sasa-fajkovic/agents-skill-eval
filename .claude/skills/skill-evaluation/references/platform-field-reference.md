# Platform Field Reference

Cross-platform compatibility for SKILL.md frontmatter fields.

## agentskills.io stable spec (5 fields)

These are the ONLY fields guaranteed to work across all platforms.

| Field | Required | Type | Constraints | Portable |
|-------|----------|------|-------------|----------|
| `name` | Yes | string | 1-64 chars, `[a-z0-9-]`, no leading/trailing/consecutive hyphens, must match parent directory | Yes |
| `description` | Yes | string | 1-1024 chars, non-empty | Yes |
| `license` | No | string | License name or reference to bundled file | Yes |
| `compatibility` | No | string | Max 500 chars, environment requirements | Yes |
| `metadata` | No | mapping | String-to-string key-value pairs | Yes |

Source: https://agentskills.io/specification#frontmatter

## Experimental fields

| Field | Status | Notes |
|-------|--------|-------|
| `allowed-tools` | Experimental | "Support for this field may vary between agent implementations." Not part of stable spec. |

`allowed-tools` is supported by Claude Code but ignored by OpenCode (uses `opencode.json` permissions) and Codex (uses `agents/openai.yaml`). Because it's not stable, we treat it the same as platform-specific extensions: **ERROR** if present.

Source: https://agentskills.io/specification#frontmatter — "Experimental" label

## Claude Code extensions (11 fields)

These work ONLY in Claude Code. Other platforms silently ignore them.

| Field | Type | Purpose |
|-------|------|---------|
| `allowed-tools` | string/list | Pre-approved tools (also experimental in base spec) |
| `argument-hint` | string | Autocomplete hint |
| `disable-model-invocation` | boolean | Prevent auto-triggering |
| `user-invocable` | boolean | Hide from / menu |
| `model` | string | Model override (sonnet/opus/haiku) |
| `effort` | string | Effort level (low/medium/high/max) |
| `context` | string | Execution context (fork) |
| `agent` | string | Subagent type for fork |
| `hooks` | mapping | Skill-scoped lifecycle hooks |
| `paths` | string/list | Auto-activation glob patterns |
| `shell` | string | Shell override (bash/powershell) |

Source: https://code.claude.com/docs/en/skills#frontmatter-reference

## Other platforms

**OpenCode**: Recognizes only the 5 stable fields. Tool permissions configured in `opencode.json`. Unknown frontmatter fields are ignored.
Source: https://opencode.ai/docs/skills/

**Codex**: Recognizes `name` and `description` in frontmatter. Agent configuration goes in `agents/openai.yaml`.
Source: https://developers.openai.com/codex/skills

## Portability rule

A SKILL.md that uses only the 5 stable fields works on Claude Code, OpenCode, and Codex. Every non-standard field added reduces portability with zero warning — platforms silently ignore what they don't recognize, causing the skill to behave differently across environments.

Use `compatibility:` to signal platform-specific requirements:
```yaml
compatibility: Designed for Claude Code. Uses gh CLI and git.
```

Scope tool usage, model preferences, and execution context in the skill body prose — this is the only portable mechanism.
