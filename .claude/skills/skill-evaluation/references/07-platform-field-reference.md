# Platform field reference

Source: [Agent Skills specification](https://agentskills.io/specification#frontmatter).

## Documented frontmatter

| Field | Required | Type | Constraints |
| --- | --- | --- | --- |
| `name` | Yes | string | 1–64 characters; lowercase alphanumeric characters and single interior hyphens; matches parent directory; no word-count limit |
| `description` | Yes | string | Non-empty, at most 1024 characters |
| `license` | No | string | License name or bundled-file reference |
| `compatibility` | No | string | Non-empty, at most 500 characters; environment requirements |
| `metadata` | No | mapping | Arbitrary string-to-string pairs, including author and version |
| `allowed-tools` | No | string | Experimental; space-separated pre-approved tool identifiers |

The evaluator follows the specification's linked skills-ref validator for Unicode NFKC name normalization and Unicode alphanumeric validation. It normalizes both the name and parent directory before comparing them.

`allowed-tools: Bash(git:*) Bash(jq:*) Read` is valid and produces no finding. Runtime support may vary because the field is experimental. This field is part of the documented Agent Skills format, not a Claude-only extension. Lists, mappings, booleans, numbers, and null are type errors for this string field.

## Known platform extensions

The evaluator warns on these known top-level Claude Code extensions because their behavior is platform-specific: `argument-hint`, `disable-model-invocation`, `user-invocable`, `model`, `effort`, `context`, `agent`, `hooks`, `paths`, `shell`.

Source: [Claude Code skills reference](https://code.claude.com/docs/en/skills#frontmatter-reference). These warnings express evaluator portability policy; runtime behavior depends on the implementation. Unknown top-level fields produce errors. Arbitrary string keys inside `metadata` remain valid and do not activate platform extension behavior.

Use `compatibility` to describe environment and runtime dependencies. The specification permits script languages supported by the environment; `.py`/`.sh` preference and the legacy `unsupported_script_types` output are evaluator policy.

## Recommendations and heuristics

Under 500 SKILL.md lines is a specification recommendation, producing WARN without a reference-skill exception. Description warnings above 350 characters and for missing trigger phrases are optional evaluator heuristics; the hard description limit is 1024. Script interfaces, output style, interactivity, security, MCP, token efficiency, and effectiveness checks are quality policy rather than format requirements. Valid Agent Skills format does not guarantee a passing quality score.
