# Specification checks and evaluator policy (1.1–1.11)

Source: [current Agent Skills specification](https://agentskills.io/specification). Name normalization follows the [linked skills-ref validator](https://github.com/agentskills/agentskills/tree/main/skills-ref).

## 1.1: name (ERROR)

The required name is a string of 1–64 characters, with no word-count limit. Following skills-ref, trim surrounding whitespace, normalize the name with Unicode NFKC, require lowercase, and allow only Unicode alphanumeric characters or hyphens. Leading, trailing, and consecutive hyphens are invalid. Compare the normalized name with the NFKC-normalized parent directory name. Synthetic flat-upload directories named `eval-<uuid>` skip only the directory comparison. Quote numeric or boolean-like string fields, such as `name: "2048"` or `name: "yes"`.

Valid examples include `skill-eval`, `café`, and `技能`. Uppercase `My-Skill`, `my--skill`, and `-skill` fail. A decomposed directory spelling of `café` matches its composed name after normalization.

## 1.2: description

The required description must be a non-empty string of at most 1024 characters (ERROR otherwise). It should explain what the skill does and when to use it.

The evaluator additionally warns above 350 characters and when no recognized trigger phrase such as "Use when" is present. These are optional evaluator heuristics, not format limits or a required literal phrase. Descriptions of 351–1024 characters remain valid under the specification.

```yaml
description: >
  Create pull requests from branch context. Use when opening a PR
  or updating an existing PR's title and description.
```

## 1.3: documented fields and platform extensions

The specification documents six fields: `name`, `description`, `license`, `compatibility`, `metadata`, and experimental `allowed-tools`.

A valid `allowed-tools` string produces no finding. Its experimental status means runtime support may vary, not that the field violates the specification. Known Claude Code extensions (`argument-hint`, `disable-model-invocation`, `user-invocable`, `model`, `effort`, `context`, `agent`, `hooks`, `paths`, `shell`) produce portability WARNs. Other unknown top-level fields produce ERRORs; custom data belongs in `metadata`.

```yaml
allowed-tools: Bash(git:*) Bash(jq:*) Read
metadata:
  author: platform-team
  version: "2.1"
```

## 1.4: optional field types (ERROR)

| Field | Type | Constraints |
| --- | --- | --- |
| `license` | string | License name or bundled-file reference; no minimum length enforced |
| `compatibility` | string | Non-empty, at most 500 characters |
| `metadata` | mapping | Arbitrary string keys and string values; empty mapping is valid |
| `allowed-tools` | string | Space-separated tool identifiers; no minimum length enforced |

PyYAML's safe loader parses YAML rather than approximating it. Folded strings, quoted colons and hashes, and nested string-to-string metadata work. Malformed YAML and non-mapping frontmatter fail. YAML numbers, booleans, dates, sequences, and mappings are not strings: quote metadata versions, dates, and boolean-like values. Unsafe Python object tags are rejected.

Evaluator parser resource policy rejects excessive nesting, recursive merges, and merge expansion above 10,000 inherited mapping entries before construction. Benign YAML merges remain supported. Invalid scalar constructors produce structured errors, and metadata diagnostics report types without expanding alias-backed values.

## 1.5: metadata (retired, no findings)

This check ID is retained without findings. Arbitrary string-to-string metadata is specification-valid, including author, version, tags, and keys named like platform extensions. Metadata names do not create top-level extension behavior.

## 1.6: recommended SKILL.md length (WARN)

The specification recommends keeping SKILL.md under 500 lines. At 500 or more lines, warn rather than reject the skill. There is no 800-line reference-skill exception. Move detailed material into `references/` and reusable code into `scripts/` when useful.

## Script checks are evaluator policy

The specification allows scripts in languages supported by the runtime. The following checks apply project quality policy informed by agentic script guidance; their severities are not Agent Skills format requirements.

### 1.8: entrypoint help (ERROR)

Entrypoint scripts should expose `--help`. The evaluator searches for help strings, argparse, getopts/getopt, usage functions, and related indicators. Helper modules without entrypoints are excluded. This static heuristic does not execute scripts or prove help works.

### 1.9: structured output (WARN)

When an entrypoint prints output but lacks JSON/CSV or related format indicators, suggest structured output for composability. This is a heuristic; human-readable output can be intentional.

### 1.10: interactive prompts (ERROR)

Prompt patterns without nearby non-interactive fallback indicators can block unattended execution. Prefer flags, environment variables, or stdin. Static detection can need contextual review.

### 1.11: script portability (WARN)

Evaluator policy prefers `.py` and `.sh` when those runtimes are available. Other script extensions receive portability warnings, including JavaScript, TypeScript, Go, Ruby, PHP, Perl, and extensions outside that list. No language is rejected as a specification error. Document required runtimes in `compatibility`.

Data/config files (`.json`, `.yaml`, `.yml`, `.toml`, `.txt`, `.md`, `.csv`, `.xml`, `.ini`, `.cfg`, `.conf`, `.env`), directories, and extensionless files are skipped by this extension heuristic.

`metadata.unsupported_script_types` preserves the legacy set `.js`, `.ts`, `.go`, `.rb`, `.php`, `.pl` for downstream compatibility. It is a policy signal, not a specification ban. See [output schema](08-output-schema.md).

## Other tiers

Security, MCP namespace policy, token efficiency, and effectiveness checks are evaluator quality policy. The specification imposes no body-format restrictions. In particular, the minimum-body-content gate is a project heuristic, not a specification requirement. Policy errors can fail the evaluator even when the skill's format is valid.
