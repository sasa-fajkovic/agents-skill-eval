# MCP policy (check 3.7)

Single source of truth for how the evaluator treats Model Context
Protocol (MCP) tools in skills. All other docs link here instead of
duplicating.

## Why MCP at all?

An MCP server exposes tools to an agent as JSON-schema calls. That
costs extra tokens per invocation versus shelling out to a CLI, and
ties the skill to a runtime that exposes that server. Sometimes
there's no good alternative (Figma, Slack, org-internal services).
Sometimes there's a strictly better one (`gh`, `acli`, plain `curl`).

## Namespaces

| Namespace prefix        | Verdict | Suggested alternative                       |
|-------------------------|---------|---------------------------------------------|
| `mcp__figma_`           | Allow   | (MCP is the only good path)                 |
| `mcp__slack_`           | Allow   | no good public CLI                          |
| *(org-internal prefix)* | Allow   | internal direction — leave extensible       |
| `mcp__github_`          | ERROR   | `gh` CLI or GitHub REST API                 |
| `mcp__atlassian_`       | ERROR   | `acli` CLI or Atlassian REST API            |
| anything else `mcp__*`  | WARN    | case-by-case review                         |
| generic MCP prose       | WARN    | case-by-case review                         |

Generic prose references (`MCP server`, `Model Context Protocol`,
etc.) are also WARN when they appear as positive instructions.
Negative context ("do not use MCP", "prefer gh instead") suppresses
the finding.

## Adding to Allowed or Blocked

Both lists live in `scripts/scoring_config.json` under the
`mcp_policy` key. Edit the lists to change policy. The suggestion
string on the blocked map becomes part of the error message.

Defaults (used when the config key is missing):

```json
{
  "mcp_policy": {
    "allowed_namespaces": ["mcp__figma_", "mcp__slack_"],
    "blocked_namespaces": {
      "mcp__github_": "the `gh` CLI or the GitHub REST API",
      "mcp__atlassian_": "the `acli` CLI or the Atlassian REST API"
    }
  }
}
```

Downstream forks can extend the lists (e.g. adding org-internal MCP
prefixes to `allowed_namespaces`) without modifying any Python code.

## Rationale summary

- We don't ban MCP outright — that conflicts with MCP-only services
  (Figma, Slack, internal tooling).
- We do block MCP where a widely-installed CLI is strictly cheaper.
- New MCP usage lands via WARN + human review so the maintainer
  classifies each new namespace as Allowed or Blocked.
- MCP lives in Tier 3 (token efficiency), not Tier 2 (security).
