#!/usr/bin/env python3
from __future__ import annotations

"""Deterministic skill evaluator (Tier 1-2). Implements checks 1.1-1.11, 2.1-2.3.

Based on the agentskills.io specification and script design best practices:
https://agentskills.io/specification
https://agentskills.io/skill-creation/using-scripts#designing-scripts-for-agentic-use
"""

import os
import json
import re
import sys
import difflib
from pathlib import Path

try:
    from PyPDF2 import PdfReader
except Exception:  # pragma: no cover - defensive import fallback
    PdfReader = None

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - exercised via subprocess tests
    class _FallbackYAMLModule:
        class YAMLError(Exception):
            pass

        @staticmethod
        def safe_load(text: str):
            data = {}
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                key, value = line.split(":", 1)
                data[key.strip()] = value.strip().strip('"\'')
            return data

    yaml = _FallbackYAMLModule()

# ANSI colors (disabled when NO_COLOR is set or stdout is not a TTY)
_use_color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
GREEN = "\033[32m" if _use_color else ""
YELLOW = "\033[33m" if _use_color else ""
RED = "\033[31m" if _use_color else ""
BLUE = "\033[34m" if _use_color else ""
DIM = "\033[2m" if _use_color else ""
BOLD = "\033[1m" if _use_color else ""
RESET = "\033[0m" if _use_color else ""

BOX_WIDTH = 60
ALL_CHECK_IDS = [
    "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8", "1.9", "1.10", "1.11",
    "2.1", "2.2", "2.3",
    "3.1", "3.2", "3.3", "3.4", "3.5", "3.6", "3.7",
    "4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.7",
]
MAX_SUPPORTING_CONTEXT_CHARS = 12000
MAX_FILE_EXCERPT_CHARS = 1500
ALLOWED_TEXT_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".json",
    ".py",
    ".sh",
    ".js",
    ".html",
    ".xml",
    ".xsd",
    ".yaml",
    ".yml",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
PDF_EXTENSIONS = {".pdf"}


def emit_progress(message: str) -> None:
    if os.environ.get("EVAL_PROGRESS_STDERR") == "1":
        print(message, file=sys.stderr, flush=True)


def print_separator(label: str) -> None:
    """Print a blue separator line with a centered label."""
    pad = BOX_WIDTH - len(label) - 4
    left = pad // 2
    right = pad - left
    print(f"{BLUE}{BOLD}{'─' * left}┤ {label} ├{'─' * right}{RESET}")


def print_box_top(title: str) -> None:
    """Print the top edge of a tier box."""
    inner = BOX_WIDTH - 2
    print(f"{BLUE}┌{'─' * inner}┐{RESET}")
    pad = inner - len(title)
    print(f"{BLUE}│{RESET} {BOLD}{title}{RESET}{' ' * (pad - 1)}{BLUE}│{RESET}")
    print(f"{BLUE}├{'─' * inner}┤{RESET}")


def print_box_line(text: str) -> None:
    """Print a line inside a tier box."""
    # Strip ANSI for length calc
    plain = re.sub(r"\033\[[0-9;]*m", "", text)
    pad = BOX_WIDTH - 2 - len(plain)
    if pad < 0:
        pad = 0
    print(f"{BLUE}│{RESET} {text}{' ' * (pad - 1)}{BLUE}│{RESET}")


def print_box_bottom() -> None:
    """Print the bottom edge of a tier box."""
    inner = BOX_WIDTH - 2
    print(f"{BLUE}└{'─' * inner}┘{RESET}")

STABLE_FIELDS = {"name", "description", "license", "compatibility", "metadata"}
CLAUDE_CODE_EXTENSIONS = {
    "allowed-tools", "argument-hint", "disable-model-invocation",
    "user-invocable", "model", "effort", "context", "agent",
    "hooks", "paths", "shell",
}
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ALLOWED_SCRIPT_EXTENSIONS = {".sh", ".py"}
DISCOURAGED_SCRIPT_EXTENSIONS = {".js", ".ts", ".go", ".rb", ".php", ".pl"}
MAX_NAME_LEN = 64
MAX_DESC_LEN = 1024
MAX_COMPAT_LEN = 500
MAX_LINES = 500

WHEN_PHRASES = re.compile(
    r"use when|use for|use if|use after|use before|use to|use whenever|"
    r"trigger|activate|invoke when|run when|run this when",
    re.IGNORECASE,
)
UNSCOPED_TOOL = re.compile(
    r"run (?:any|whatever|the necessary|required)\b|"
    r"execute (?:any|the required|necessary)\b|"
    r"use bash to\b(?!.*only)",
    re.IGNORECASE,
)
DESTRUCTIVE_OPS = re.compile(
    r"\brm\b|\brm -rf\b|--force\b|-f\b.*push|push --force|"
    r"reset --hard|checkout -- \.|restore \.|branch -D\b|"
    r"\bdelete\b|\bremove\b|\bdrop table\b|\btruncate\b|"
    r"\boverwrite\b|\bkill\b|\bpkill\b",
    re.IGNORECASE,
)
SAFEGUARD = re.compile(
    r"confirm|ask the user|AskUserQuestion|ask.*before|prompt for|"
    r"backup|back.?up|dry.?run|preview|"
    r"check first|verify before|only if.*explicitly",
    re.IGNORECASE,
)

# Patterns that indicate --help is handled
HELP_PATTERNS = [
    re.compile(r"""['"]--help['"]"""),
    re.compile(r"""\b(--help|-h)\b"""),
    re.compile(r"""argparse|ArgumentParser"""),
    re.compile(r"""getopts|getopt"""),
    re.compile(r"""usage\s*[=(]""", re.IGNORECASE),
    re.compile(r"""show_help|print_help|display_help"""),
    re.compile(r"""Usage:"""),
]

# Patterns indicating structured output (JSON, CSV, TSV)
STRUCTURED_OUTPUT = re.compile(
    r"json\.dumps|json\.dump|JSON\.stringify|to_json|"
    r"csv\.writer|csv\.DictWriter|"
    r"print.*json|echo.*json|printf.*json|"
    r"jq\s|ConvertTo-Json|"
    r"--format\s+json|--output-format|"
    r"-o\s+json",
    re.IGNORECASE,
)

# Patterns indicating interactive prompts (anti-pattern for agentic use)
INTERACTIVE_PROMPT = re.compile(
    r"\bread\s+-p\b|"
    r"(?<!\w)input\s*\(\s*['\"]|"
    r"(?<!\.)prompt\s*\(\s*['\"]|"
    r"\breadline\(\)|"
    r"\binquirer\b|"
    r"\benquirer\b|"
    r"read\s+-r\s+\w+\s*$",
    re.IGNORECASE | re.MULTILINE,
)

MCP_REFERENCE = re.compile(
    r"(?i)(mcp__\w+|model\s+context\s+protocol|mcp\s+server(?:s)?|"
    r"(?:github|gitlab|jira|atlassian|google\s+workspace|slack|figma)\s+mcp|\bmcp\b)"
)
MCP_NEGATION = re.compile(
    r"(?i)(do not|don't|never|avoid|instead of|not allowed|prohibited|"
    r"forbidden|disallow(?:ed)?|must not|ban(?:ned)?|use .* instead|prefer .* instead)"
)
VERBOSE_PROSE = re.compile(
    r"(?i)\b(first,? you need to|in order to|the next step is to|to accomplish this|"
    r"you should now|it is important to)\b"
)
STANDARD_TOOL_TUTORIAL = re.compile(
    r"(?i)(to check .* run `?git status`?|pipe the output to jq|"
    r"the `-[a-z]` flag|use `?\$\(.+?\)`? for command substitution|"
    r"send a post request with content-type: application/json)"
)
PRELOAD_REFERENCE = re.compile(
    r"(?i)(read all files in references/|load references/ first|start by reading every file in references/|"
    r"pre-load the following references|preload the following references)"
)
AMBIGUOUS_LANGUAGE = re.compile(
    r"(?i)\b(appropriately|as needed|relevant|suitable|proper|reasonable|"
    r"when necessary|if applicable|the correct format|the standard approach|concise|clear|well-structured)\b"
)
NEGATIVE_ONLY = re.compile(r"(?i)\b(don't|do not|never|avoid|must not)\b")
POSITIVE_ALTERNATIVE = re.compile(r"(?i)\b(use|write|prefer|instead|choose|return|format|do .* not)\b")
DEFAULT_BEHAVIOR = re.compile(r"(?i)(defaults to|if omitted|if not provided|required|when omitted|must provide|optional)")
IDEMPOTENT_GUARD = re.compile(r"(?i)(if not exists|if missing|already exists|mkdir -p|ensure|idempotent|skip if|update if)")
NON_IDEMPOTENT_OP = re.compile(r"(?i)(>>|\bmkdir\s+(?!-p)\S|\bcurl\s+.*-x\s+post|\bgh pr create\b|\bacli\s+jira\s+workitem\s+create\b)")
OUTPUT_OR_FORMAT = re.compile(r"(?i)(^#{1,3}\s+output\b|output format|format as|use the following format|template|tone guidance)")
SUCCESS_CRITERIA = re.compile(r"(?i)(^#{1,3}\s+output\b|done when|complete when|completion condition|returns?\b|produces?\b)")


class Finding:
    def __init__(self, check_id: str, severity: str, message: str):
        self.check_id = check_id
        self.severity = severity  # ERROR or WARN
        self.message = message

    def __str__(self):
        return f"[{self.severity}] {self.check_id}: {self.message}"

    @property
    def severity_key(self) -> str:
        if self.severity == "ERROR":
            return "error"
        if self.severity == "WARN":
            return "warning"
        return "info"

    @property
    def rule_id(self) -> str:
        rule_map = {
            "1.1": "invalid_name",
            "1.2": "missing_or_weak_description",
            "1.3": "non_standard_field",
            "1.4": "invalid_field_type",
            "1.5": "redundant_metadata",
            "1.6": "skill_too_long",
            "1.7": "missing_tests",
            "1.8": "missing_help",
            "1.9": "unstructured_output",
            "1.10": "interactive_prompt",
            "1.11": "runtime_dependency_required",
            "2.1": "unscoped_tool_usage",
            "2.2": "destructive_operation_without_safeguard",
            "2.3": "mcp_usage_disallowed",
        }
        return rule_map.get(self.check_id, f"rule_{self.check_id.replace('.', '_')}")

    @property
    def reason(self) -> str:
        reason_map = {
            "1.1": "Skill names must be stable, portable identifiers that match the containing directory.",
            "1.2": "Descriptions tell an agent when the skill should activate and what it is for.",
            "1.3": "Non-standard fields reduce portability across agent runtimes that implement the open skills standard.",
            "1.4": "Typed, predictable frontmatter fields keep the skill machine-readable across runtimes.",
            "1.5": "Metadata should not duplicate git history or hide runtime-specific behavior behind arbitrary keys.",
            "1.6": "Excessively long skills are harder for agents to load, inspect, and apply consistently.",
            "1.7": "Bundled scripts need tests so the skill remains reliable when scripts change.",
            "1.8": "Agents rely on --help to learn a script's interface safely and autonomously.",
            "1.9": "Structured output is easier for agents to parse, validate, and compose than free-form text.",
            "1.10": "Interactive prompts block autonomous execution because agents cannot respond inline.",
            "1.11": "Portable skills should prefer shell and Python scripts because those runtimes are commonly available without extra setup.",
            "2.1": "Broad tool instructions make execution behavior ambiguous and harder to bound safely.",
            "2.2": "Destructive operations need explicit safeguards to avoid irreversible damage.",
            "2.3": "MCP-specific instructions reduce portability and tie the skill to one runtime integration surface.",
        }
        return reason_map.get(self.check_id, "This issue reduces portability, clarity, or reliability of the skill definition.")

    def colored(self) -> str:
        if self.severity == "ERROR":
            return f"🔴 {BOLD}{self.check_id}{RESET}: {self.message}"
        return f"🟡 {BOLD}{self.check_id}{RESET}: {self.message}"


def parse_frontmatter(text: str) -> tuple[dict | None, str]:
    """Extract YAML frontmatter and body from SKILL.md content."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None, text

    end_line = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            end_line = i
            break

    if end_line is None:
        return None, text

    fm_text = "".join(lines[1:end_line]).strip()
    body = "".join(lines[end_line + 1:]).strip()
    try:
        fm = yaml.safe_load(fm_text)
        return fm if isinstance(fm, dict) else None, body
    except yaml.YAMLError:
        return None, body


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def read_pdf_file(path: Path) -> str:
    if PdfReader is None:
        return ""
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n".join(pages)


def collect_supporting_context(skill_path: Path) -> str:
    root = skill_path.parent
    paths = [path for path in root.rglob("*") if path.is_file() and path != skill_path]
    inventory = []
    parts = []
    used_chars = 0

    def sort_key(path: Path) -> tuple[int, str]:
        relative = str(path.relative_to(root)).lower()
        priority = 3
        if "/references/" in f"/{relative}" or relative.startswith("references/"):
            priority = 0
        elif relative.endswith("license.txt") or relative.endswith("readme.md") or relative.endswith("readme.txt"):
            priority = 1
        elif "/scripts/" in f"/{relative}" or relative.startswith("scripts/"):
            priority = 2
        return (priority, relative)

    for path in sorted(paths, key=sort_key):
        suffix = path.suffix.lower()
        relative = path.relative_to(root)
        inventory.append(str(relative))
        if suffix in ALLOWED_TEXT_EXTENSIONS:
            content = read_text_file(path)
        elif suffix in PDF_EXTENSIONS:
            content = read_pdf_file(path)
        elif suffix in IMAGE_EXTENSIONS:
            content = "[image file omitted from text analysis]"
        else:
            continue

        stripped = content.strip()
        if not stripped:
            continue

        remaining = MAX_SUPPORTING_CONTEXT_CHARS - used_chars
        if remaining <= 0:
            break

        excerpt = stripped[: min(MAX_FILE_EXCERPT_CHARS, remaining)]
        if len(stripped) > len(excerpt):
            excerpt += "\n[truncated]"
        block = f"--- FILE: {relative} ---\n{excerpt}"
        parts.append(block)
        used_chars += len(block) + 2

    summary = ["Supporting file inventory:"]
    summary.extend(f"- {item}" for item in inventory)
    if parts:
        summary.append("")
        summary.append("Supporting file excerpts:")
        summary.append("")
        summary.append("\n\n".join(parts))
    return "\n".join(summary)


def extract_frontmatter_string_field(fm: dict | None, field_name: str) -> str:
    if not isinstance(fm, dict):
        return ""
    value = fm.get(field_name)
    if isinstance(value, str):
        return value.strip()
    return ""


def extract_skill_name(skill_path: Path, fm: dict | None) -> str:
    name = extract_frontmatter_string_field(fm, "name")
    if name:
        return name
    return skill_path.parent.name if skill_path.name == "SKILL.md" else skill_path.stem


def extract_skill_description(skill_text: str, fm: dict | None) -> str:
    description = extract_frontmatter_string_field(fm, "description")
    if description:
        return description

    lines = skill_text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().lower() in {"## description", "# description"}:
            collected = []
            for candidate in lines[index + 1:]:
                stripped = candidate.strip()
                if not stripped:
                    collected.append("")
                    continue
                if stripped.startswith("#"):
                    break
                collected.append(stripped)
            while collected and not collected[0]:
                collected.pop(0)
            while collected and not collected[-1]:
                collected.pop()
            if collected:
                paragraphs = []
                current = []
                for item in collected:
                    if item:
                        current.append(item)
                        continue
                    if current:
                        paragraphs.append(" ".join(current))
                        current = []
                if current:
                    paragraphs.append(" ".join(current))
                if paragraphs:
                    return "\n\n".join(paragraphs)

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def extract_skill_compatibility(fm: dict | None) -> str:
    return extract_frontmatter_string_field(fm, "compatibility")


def fenced_code_blocks(body: str) -> list[tuple[int, int, str, str]]:
    lines = body.splitlines()
    blocks = []
    in_fence = False
    start = 0
    lang = ""
    collected: list[str] = []
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                in_fence = True
                start = index
                lang = stripped[3:].strip().lower()
                collected = []
            else:
                blocks.append((start, index, lang, "\n".join(collected)))
                in_fence = False
                lang = ""
                collected = []
            continue
        if in_fence:
            collected.append(line)
    return blocks


def heading_ranges(body: str) -> list[tuple[int, int, str, str]]:
    lines = body.splitlines()
    headings: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines, start=1):
        match = re.match(r"^(#{1,3})\s+(.+?)\s*$", line.strip())
        if match:
            headings.append((index, match.group(1), match.group(2).strip()))
    ranges = []
    for idx, (line_num, hashes, title) in enumerate(headings):
        end_line = len(lines)
        for next_line, next_hashes, _next_title in headings[idx + 1:]:
            if len(next_hashes) <= len(hashes):
                end_line = next_line - 1
                break
        ranges.append((line_num, end_line, hashes, title))
    return ranges


def extract_output_section(body: str) -> tuple[int, str]:
    lines = body.splitlines()
    for start, end, _hashes, title in heading_ranges(body):
        if title.lower() == "output":
            return start, "\n".join(lines[start:end])
    return 0, ""


def has_nearby_example(lines: list[str], index: int, window: int = 20) -> bool:
    start = max(0, index - window)
    end = min(len(lines), index + window)
    context = "\n".join(lines[start:end])
    return bool(re.search(r"(?i)\bexample\b|```", context))


def normalize_text_block(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def body_paragraphs(body: str) -> list[tuple[int, str]]:
    paragraphs = []
    current: list[str] = []
    start_line = 1
    lines = body.splitlines()
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append((start_line, " ".join(current)))
                current = []
            continue
        if not current:
            start_line = index
        if stripped.startswith("#") or stripped.startswith("```") or stripped.startswith("-") or stripped.startswith("*"):
            if current:
                paragraphs.append((start_line, " ".join(current)))
                current = []
            continue
        current.append(stripped)
    if current:
        paragraphs.append((start_line, " ".join(current)))
    return paragraphs


def check_1_1(fm: dict, skill_dir: str) -> list[Finding]:
    """1.1: name format validation."""
    findings = []
    if "name" not in fm:
        findings.append(Finding("1.1", "ERROR", "missing required field 'name'"))
        return findings
    name = fm["name"]
    if not isinstance(name, str):
        findings.append(Finding("1.1", "ERROR", f"name must be a string, got {type(name).__name__}"))
        return findings
    if len(name) > MAX_NAME_LEN:
        findings.append(Finding("1.1", "ERROR", f"name is {len(name)} chars (max {MAX_NAME_LEN})"))
    if not NAME_RE.match(name):
        findings.append(Finding("1.1", "ERROR", f'name "{name}" must be lowercase alphanumeric + hyphens, no leading/trailing/consecutive hyphens'))
    dirname = Path(skill_dir).name
    if name != dirname:
        findings.append(Finding("1.1", "ERROR", f'name "{name}" does not match directory "{dirname}"'))
    return findings


def check_1_2(fm: dict) -> list[Finding]:
    """1.2: description validation."""
    findings = []
    if "description" not in fm:
        findings.append(Finding("1.2", "ERROR", "missing required field 'description'"))
        return findings
    desc = fm["description"]
    if not isinstance(desc, str) or not desc.strip():
        findings.append(Finding("1.2", "ERROR", "description must be a non-empty string"))
        return findings
    if len(desc) > MAX_DESC_LEN:
        findings.append(Finding("1.2", "ERROR", f"description is {len(desc)} chars (max {MAX_DESC_LEN})"))
    if not WHEN_PHRASES.search(desc):
        findings.append(Finding("1.2", "WARN", 'description should include a "Use when..." clause'))
    return findings


def check_1_3(fm: dict) -> list[Finding]:
    """1.3: no non-standard fields."""
    findings = []
    for key in fm:
        if key in STABLE_FIELDS:
            continue
        if key == "allowed-tools":
            findings.append(Finding("1.3", "ERROR", f'"{key}" is experimental in agentskills.io (not stable spec)'))
        elif key in CLAUDE_CODE_EXTENSIONS:
            findings.append(Finding("1.3", "ERROR", f'"{key}" is a Claude Code extension (not in agentskills.io stable spec)'))
        else:
            findings.append(Finding("1.3", "ERROR", f'"{key}" is not a recognized agentskills.io field (move to metadata: if needed)'))
    return findings


def check_1_4(fm: dict) -> list[Finding]:
    """1.4: field type/value validation."""
    findings = []
    if "license" in fm and not isinstance(fm["license"], str):
        findings.append(Finding("1.4", "ERROR", f"license must be a string, got {type(fm['license']).__name__}"))
    if "compatibility" in fm:
        if not isinstance(fm["compatibility"], str):
            findings.append(Finding("1.4", "ERROR", f"compatibility must be a string, got {type(fm['compatibility']).__name__}"))
        elif len(fm["compatibility"]) > MAX_COMPAT_LEN:
            findings.append(Finding("1.4", "ERROR", f"compatibility is {len(fm['compatibility'])} chars (max {MAX_COMPAT_LEN})"))
    if "metadata" in fm:
        meta = fm["metadata"]
        if not isinstance(meta, dict):
            findings.append(Finding("1.4", "ERROR", f"metadata must be a mapping, got {type(meta).__name__}"))
        else:
            for k, v in meta.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    findings.append(Finding("1.4", "ERROR", f"metadata entry {k!r}: {v!r} — both key and value must be strings"))
    return findings


METADATA_REDUNDANT_KEYS = {
    "author", "maintainer", "email", "version", "semver",
    "created", "updated", "date", "last-modified", "last_modified",
    "tags", "tag", "category", "topic",
}


def check_1_5(fm: dict) -> list[Finding]:
    """1.5: warn on metadata keys that duplicate git history or launder CC extensions."""
    if "metadata" not in fm:
        return []
    meta = fm["metadata"]
    if not isinstance(meta, dict):
        return []
    findings = []
    for key in meta:
        if key in METADATA_REDUNDANT_KEYS:
            findings.append(Finding(
                "1.5", "WARN",
                f'metadata key "{key}" duplicates git history — remove it; metadata loads on every API call',
            ))
        elif key in CLAUDE_CODE_EXTENSIONS:
            findings.append(Finding(
                "1.3", "WARN",
                f'metadata key "{key}" is a Claude Code extension — '
                f"placing it in metadata hides it from portability checks but has no effect on non-CC platforms",
            ))
    return findings


def check_1_6(lines: list[str]) -> list[Finding]:
    """1.6: SKILL.md line count."""
    count = len(lines)
    if count > MAX_LINES:
        return [Finding("1.6", "ERROR", f"SKILL.md is {count} lines (max {MAX_LINES})")]
    return []


def _scripts_under(skill_dir: str) -> list[tuple[Path, str]]:
    """Return (absolute_path, display_path) for every script in the skill.

    Searches both the skill root and scripts/ subdirectory.
    display_path is "scripts/foo.sh" for scripts/ files, "foo.sh" for root-level.
    """
    root = Path(skill_dir)
    results = []
    # Root-level scripts
    for f in sorted(root.iterdir()):
        if f.is_file() and f.suffix in ALLOWED_SCRIPT_EXTENSIONS:
            results.append((f, f.name))
    # scripts/ subdirectory
    scripts_dir = root / "scripts"
    if scripts_dir.is_dir():
        for f in sorted(scripts_dir.iterdir()):
            if f.is_file() and f.suffix in ALLOWED_SCRIPT_EXTENSIONS:
                results.append((f, f"scripts/{f.name}"))
    return results


def check_1_7(skill_dir: str) -> list[Finding]:
    """1.7: per-script test matching (WARN — LLM escalates based on complexity)."""
    scripts = _scripts_under(skill_dir)
    if not scripts:
        return []
    tests_dir = Path(skill_dir) / "tests"
    findings = []
    for script_path, display in scripts:
        stem = script_path.stem
        if script_path.suffix == ".sh":
            expected = tests_dir / f"{stem}.bats"
        else:
            expected = tests_dir / f"test_{stem}.py"
        if not expected.exists():
            findings.append(Finding(
                "1.7", "WARN",
                f"{display} has no matching test file (expected {expected.relative_to(Path(skill_dir))})",
            ))
    return findings


def check_1_8(skill_dir: str) -> list[Finding]:
    """1.8: scripts must implement --help."""
    findings = []
    for script_path, display in _scripts_under(skill_dir):
        try:
            content = script_path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        has_help = any(p.search(content) for p in HELP_PATTERNS)
        if not has_help:
            findings.append(Finding(
                "1.8", "ERROR",
                f"{display} does not implement --help — "
                f"agents rely on --help to learn a script's interface",
            ))
    return findings


def check_1_9(skill_dir: str) -> list[Finding]:
    """1.9: scripts should use structured output."""
    findings = []
    for script_path, display in _scripts_under(skill_dir):
        try:
            content = script_path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        has_output = bool(re.search(r"\bprint\b|\becho\b|\bconsole\.log\b|\bprintf\b", content))
        has_structured = bool(STRUCTURED_OUTPUT.search(content))
        if has_output and not has_structured:
            findings.append(Finding(
                "1.9", "WARN",
                f"{display} — consider using structured output (JSON/CSV) "
                f"instead of free-form text for agent composability",
            ))
    return findings


def check_1_10(skill_dir: str) -> list[Finding]:
    """1.10: scripts should not use interactive prompts."""
    findings = []
    for script_path, display in _scripts_under(skill_dir):
        try:
            content = script_path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for match in INTERACTIVE_PROMPT.finditer(content):
            line_num = content[:match.start()].count("\n") + 1
            line = content.splitlines()[line_num - 1] if line_num <= len(content.splitlines()) else ""
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            if stripped.startswith(('r"', "r'", '"', "'", 'f"', "f'")):
                continue
            if "re.compile" in line or "re.search" in line or "re.match" in line:
                continue
            findings.append(Finding(
                "1.10", "ERROR",
                f"{display}:{line_num} — interactive prompt detected "
                f'("{match.group().strip()}"). Agents cannot respond to prompts; '
                f"use flags or env vars instead",
            ))
    return findings


def check_1_11(skill_dir: str) -> list[Finding]:
    """1.11: only Python and bash scripts allowed."""
    findings = []
    for script_path, display in _scripts_under(skill_dir):
        # _scripts_under already filters to ALLOWED_SCRIPT_EXTENSIONS — nothing to flag
        pass
    # Also flag non-script, non-allowed files that look like executables in scripts/
    scripts_dir = Path(skill_dir) / "scripts"
    if scripts_dir.is_dir():
        for f in sorted(scripts_dir.iterdir()):
            if f.is_dir():
                continue
            if f.suffix and f.suffix not in ALLOWED_SCRIPT_EXTENSIONS:
                severity = "WARN" if f.suffix in DISCOURAGED_SCRIPT_EXTENSIONS else "ERROR"
                findings.append(Finding(
                    "1.11", severity,
                    f"scripts/{f.name} — only Python (.py) and bash (.sh) scripts "
                    f"are allowed. {f.suffix} requires an additional runtime",
                ))
    return findings


def collect_metadata(skill_path: Path) -> dict:
    skill_dir = skill_path.parent
    files = [p for p in skill_dir.rglob("*") if p.is_file()]
    script_types = sorted({p.suffix for p in files if p.suffix in ALLOWED_SCRIPT_EXTENSIONS})
    unsupported_script_types = sorted({p.suffix for p in files if p.suffix in DISCOURAGED_SCRIPT_EXTENSIONS})
    return {
        "file_count": len(files),
        "line_count": len(read_text_file(skill_path).splitlines()) if skill_path.exists() else 0,
        "has_scripts": any(p.suffix in ALLOWED_SCRIPT_EXTENSIONS or p.suffix in DISCOURAGED_SCRIPT_EXTENSIONS for p in files),
        "script_types": script_types,
        "unsupported_script_types": unsupported_script_types,
    }


def quality_tier_for(findings: list[Finding]) -> str:
    errors = sum(1 for f in findings if f.severity == "ERROR")
    warnings = sum(1 for f in findings if f.severity == "WARN")
    if errors == 0 and warnings == 0:
        return "excellent"
    if errors == 0 and warnings <= 2:
        return "good"
    if errors <= 2:
        return "needs_work"
    return "poor"


def overall_score_for(findings: list[Finding]) -> int:
    errors = sum(1 for f in findings if f.severity == "ERROR")
    warnings = sum(1 for f in findings if f.severity == "WARN")
    score = 100 - min(errors * 14, 70) - min(warnings * 3, 24)
    return max(0, min(100, score))


def summary_for(skill_name: str, findings: list[Finding]) -> str:
    errors = [f for f in findings if f.severity == "ERROR"]
    warnings = [f for f in findings if f.severity == "WARN"]
    if errors:
        return f"{skill_name} has {len(errors)} blocking issue(s); the primary problem is {errors[0].message.lower()}."
    if warnings:
        return f"{skill_name} is mostly portable, but it has {len(warnings)} warning(s), led by {warnings[0].message.lower()}."
    return f"{skill_name} is portable, well-structured, and passes the deterministic evaluator."


def issue_payload(finding: Finding) -> dict:
    return {
        "rule_id": finding.rule_id,
        "severity": finding.severity_key,
        "message": finding.message,
        "reason": finding.reason,
    }


def build_json_result(skill_path: Path, findings: list[Finding]) -> dict:
    skill_text = read_text_file(skill_path)
    fm, _body = parse_frontmatter(skill_text)
    skill_name = extract_skill_name(skill_path, fm)
    metadata = collect_metadata(skill_path)
    errors = [f for f in findings if f.severity == "ERROR"]
    warnings = [f for f in findings if f.severity == "WARN"]
    overall_tier = quality_tier_for(findings)
    overall_score = overall_score_for(findings)
    summary = summary_for(skill_name, findings)
    strengths = []
    if not findings:
        strengths.append({
            "finding": "Deterministic checks passed",
            "reason": "The skill satisfies the current spec and security checks without warnings or errors.",
        })
    elif not errors:
        strengths.append({
            "finding": "No blocking deterministic failures",
            "reason": "The skill remains structurally valid even though it has warnings to address.",
        })

    weaknesses = [
        {"finding": f.message, "reason": f.reason}
        for f in errors[:3]
    ]
    suggestions = [
        {"finding": f.message, "reason": f"Address rule {f.rule_id} to improve portability and evaluator confidence."}
        for f in findings[:3]
    ]
    security_flags = [
        {"finding": f.message, "reason": f.reason}
        for f in findings if f.check_id.startswith("2.")
    ]

    status = "ok"
    return {
        "schema_version": "1.0",
        "status": status,
        "skill_name": skill_name,
        "skill_description": extract_skill_description(skill_text, fm),
        "skill_compatibility": extract_skill_compatibility(fm),
        "skill_content": skill_text,
        "supporting_context": collect_supporting_context(skill_path),
        "overall_score": overall_score,
        "overall_tier": overall_tier,
        "quality_tier": overall_tier,
        "summary": summary,
        "deterministic": {
            "passed": max(0, len(ALL_CHECK_IDS) - len(findings)),
            "failed": len(findings),
            "file_count": metadata["file_count"],
            "line_count": metadata["line_count"],
            "issues": [issue_payload(f) for f in findings],
        },
        "llm_analysis": {
            "strengths": strengths,
            "weaknesses": weaknesses,
            "suggestions": suggestions,
            "security_flags": security_flags,
        },
        "metadata": metadata,
    }


def check_2_1(body: str) -> list[Finding]:
    """2.1: unscoped tool usage in body."""
    findings = []
    for match in UNSCOPED_TOOL.finditer(body):
        line_num = body[:match.start()].count("\n") + 1
        findings.append(Finding("2.1", "WARN", f'line {line_num}: unscoped tool instruction "{match.group().strip()}"'))
    return findings


def _global_safeguard(body: str) -> bool:
    """Return True if the skill has a Rules section containing a safeguard pattern."""
    m = re.search(r"^#{1,3}\s+(?:core\s+)?rules?\s*$", body, re.MULTILINE | re.IGNORECASE)
    if not m:
        return False
    # Skip past the heading line itself before searching for content
    heading_end = body.find("\n", m.start())
    if heading_end == -1:
        return False
    section = body[heading_end + 1:]
    next_heading = re.search(r"^#{1,3}\s+", section, re.MULTILINE)
    if next_heading:
        section = section[: next_heading.start()]
    return bool(SAFEGUARD.search(section))


def check_2_2(body: str) -> list[Finding]:
    """2.2: destructive operations without safeguards.

    Only flags occurrences inside fenced code blocks or inline backticks.
    Skips if the skill's Rules section contains a global safeguard pattern.
    """
    has_global = _global_safeguard(body)
    findings = []
    lines = body.split("\n")
    in_fence = False
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue

        code_segments = []
        if in_fence:
            code_segments.append(line)
        else:
            code_segments.extend(re.findall(r"`([^`]+)`", line))
        if not code_segments:
            continue
        code_view = " | ".join(code_segments)

        for match in DESTRUCTIVE_OPS.finditer(code_view):
            context_start = max(0, i - 5)
            context_end = min(len(lines), i + 6)
            context = "\n".join(lines[context_start:context_end])
            if not SAFEGUARD.search(context) and not has_global:
                findings.append(Finding(
                    "2.2", "WARN",
                    f'line {i + 1}: destructive operation "{match.group()}" without safeguard',
                ))
    return findings


def _find_mcp_findings(text: str, display: str) -> list[Finding]:
    """Return MCP usage findings, skipping clearly prohibitive guidance."""
    findings = []
    lines = text.split("\n")
    for i, line in enumerate(lines):
        match = MCP_REFERENCE.search(line)
        if not match:
            continue
        context_start = max(0, i - 1)
        context_end = min(len(lines), i + 2)
        context = "\n".join(lines[context_start:context_end])
        if MCP_NEGATION.search(context):
            continue
        findings.append(Finding(
            "2.3", "ERROR",
            f'{display}{i + 1}: MCP usage/reference "{match.group().strip()}" is not allowed; use CLI or direct API alternatives instead',
        ))
    return findings


def check_2_3(body: str, skill_dir: str) -> list[Finding]:
    """2.3: MCP usage is not allowed in skills."""
    findings = _find_mcp_findings(body, "line ")
    for script_path, display in _scripts_under(skill_dir):
        try:
            content = script_path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(_find_mcp_findings(content, f"{display}:"))
    return findings


def check_3_1(body: str) -> list[Finding]:
    findings = []
    for start, end, lang, content in fenced_code_blocks(body):
        lines = [line for line in content.splitlines() if line.strip()]
        if len(lines) <= 5:
            continue
        if lang in {"json", "yaml", "yml", "text", "output"}:
            continue
        findings.append(Finding("3.1", "WARN", f"code block at lines {start}-{end} is {len(lines)} lines; move long executable logic into scripts/"))
    return findings


def check_3_2(body: str) -> list[Finding]:
    findings = []
    lines = body.splitlines()
    table_start = None
    table_rows = 0
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            table_start = table_start or index
            table_rows += 1
        else:
            if table_start and table_rows > 12:
                findings.append(Finding("3.2", "WARN", f"table at lines {table_start}-{index - 1} has {table_rows - 2} data rows; move large reference material to references/"))
            table_start = None
            table_rows = 0
    if table_start and table_rows > 12:
        findings.append(Finding("3.2", "WARN", f"table at lines {table_start}-{len(lines)} has {table_rows - 2} data rows; move large reference material to references/"))

    for start, end, _lang, content in fenced_code_blocks(body):
        block_lines = [line for line in content.splitlines() if line.strip()]
        if len(block_lines) > 15 and any(line.strip().startswith(("{", "}", "[", "]")) or ":" in line for line in block_lines[:5]):
            findings.append(Finding("3.2", "WARN", f"mapping-style block at lines {start}-{end} is {len(block_lines)} lines; move dense reference data to references/"))
    return findings


def check_3_3(body: str) -> list[Finding]:
    findings = []
    for line_num, line in enumerate(body.splitlines(), start=1):
        if STANDARD_TOOL_TUTORIAL.search(line):
            findings.append(Finding("3.3", "WARN", f"line {line_num}: explains standard tool usage the agent likely already knows"))
    return findings


def check_3_4(body: str) -> list[Finding]:
    findings = []
    blocks = fenced_code_blocks(body)
    normalized = [(start, end, normalize_text_block(content)) for start, end, _lang, content in blocks]
    seen_pairs = set()
    for i, (start_a, end_a, block_a) in enumerate(normalized):
        if not block_a:
            continue
        for start_b, end_b, block_b in normalized[i + 1:]:
            if not block_b:
                continue
            key = (start_a, start_b)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            similarity = difflib.SequenceMatcher(None, "\n".join(block_a), "\n".join(block_b)).ratio()
            if similarity >= 0.8:
                findings.append(Finding("3.4", "WARN", f"code blocks at lines {start_a}-{end_a} and {start_b}-{end_b} are duplicated or near-duplicated"))
    return findings


def check_3_5(body: str) -> list[Finding]:
    findings = []
    for line_num, paragraph in body_paragraphs(body):
        sentence_count = max(1, len(re.findall(r"[.!?](?:\s|$)", paragraph)))
        if sentence_count > 3 or VERBOSE_PROSE.search(paragraph):
            findings.append(Finding("3.5", "WARN", f"line {line_num}: verbose prose could likely be condensed without losing meaning"))
    return findings


def check_3_6(body: str) -> list[Finding]:
    findings = []
    for line_num, line in enumerate(body.splitlines(), start=1):
        if PRELOAD_REFERENCE.search(line):
            findings.append(Finding("3.6", "WARN", f"line {line_num}: instructs preloading references instead of conditional loading"))
    return findings


def check_3_7(body: str, findings_so_far: list[Finding]) -> list[Finding]:
    findings = []
    tier_2_mcp = any(f.check_id == "2.3" for f in findings_so_far)
    if tier_2_mcp:
        return findings
    for line_num, line in enumerate(body.splitlines(), start=1):
        if MCP_REFERENCE.search(line) and not MCP_NEGATION.search(line):
            findings.append(Finding("3.7", "WARN", f"line {line_num}: MCP references waste tokens and should be removed"))
    return findings


def check_4_1(body: str) -> list[Finding]:
    findings = []
    for line_num, line in enumerate(body.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "```")):
            continue
        if AMBIGUOUS_LANGUAGE.search(stripped):
            findings.append(Finding("4.1", "WARN", f"line {line_num}: instruction may be ambiguous or underspecified"))
    return findings


def check_4_2(body: str) -> list[Finding]:
    findings = []
    lines = body.splitlines()
    output_line, output_section = extract_output_section(body)
    if output_section and not re.search(r"(?i)\bexample\b|```", output_section):
        findings.append(Finding("4.2", "WARN", f"line {output_line}: output requirements are present without a concrete example"))
    for index, line in enumerate(lines):
        if OUTPUT_OR_FORMAT.search(line) and not has_nearby_example(lines, index):
            findings.append(Finding("4.2", "WARN", f"line {index + 1}: output or formatting guidance lacks a nearby concrete example"))
    return findings


def check_4_3(body: str) -> list[Finding]:
    findings = []
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if not NEGATIVE_ONLY.search(line):
            continue
        context = "\n".join(lines[index:index + 3])
        if not POSITIVE_ALTERNATIVE.search(context):
            findings.append(Finding("4.3", "WARN", f"line {index + 1}: negative-only instruction does not say what to do instead"))
    return findings


def check_4_4(body: str) -> list[Finding]:
    findings = []
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if "$ARGUMENTS" in line or re.search(r"\$[0-9]+", line) or re.search(r"--[a-z0-9-]+", line, re.IGNORECASE):
            context = "\n".join(lines[max(0, index - 2): min(len(lines), index + 3)])
            if not DEFAULT_BEHAVIOR.search(context):
                findings.append(Finding("4.4", "WARN", f"line {index + 1}: input or flag is referenced without clear default behavior"))
    return findings


def check_4_5(body: str) -> list[Finding]:
    findings = []
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if not NON_IDEMPOTENT_OP.search(line):
            continue
        context = "\n".join(lines[max(0, index - 2): min(len(lines), index + 3)])
        if not IDEMPOTENT_GUARD.search(context):
            findings.append(Finding("4.5", "WARN", f"line {index + 1}: action may not be idempotent if retried"))
    return findings


def check_4_6(body: str) -> list[Finding]:
    if SUCCESS_CRITERIA.search(body):
        return []
    return [Finding("4.6", "WARN", "no clear success criteria or output contract found")]


def check_4_7(skill_dir: str) -> list[Finding]:
    findings = []
    for script_path, display in _scripts_under(skill_dir):
        try:
            content = read_text_file(script_path)
        except (OSError, UnicodeDecodeError):
            continue
        if script_path.suffix == ".py":
            if "sys.exit(" in content and not re.search(r"sys\.exit\((2|3|4|5|6|7|8|9)", content):
                findings.append(Finding("4.7", "WARN", f"{display} appears to use only basic exit codes; document and use more specific exit codes where meaningful"))
        elif script_path.suffix == ".sh":
            if "exit " in content and not re.search(r"\bexit\s+(2|3|4|5|6|7|8|9)\b", content):
                findings.append(Finding("4.7", "WARN", f"{display} appears to use only basic exit codes; document and use more specific exit codes where meaningful"))
    return findings


def evaluate(path: str) -> list[Finding]:
    """Run all checks on a SKILL.md file."""
    skill_path = Path(path)
    if skill_path.is_dir():
        skill_path = skill_path / "SKILL.md"
    if not skill_path.exists():
        return [Finding("--", "ERROR", f"file not found: {skill_path}")]

    emit_progress("Locating primary skill file...")
    emit_progress("Reading skill content...")
    text = read_text_file(skill_path)
    lines = text.splitlines()
    skill_dir = str(skill_path.parent)
    fm, body = parse_frontmatter(text)

    emit_progress("Running deterministic checks...")
    findings = []
    if fm is None:
        findings.append(Finding("1.1", "ERROR", "no valid YAML frontmatter found"))
        findings.extend(check_1_6(lines))
        findings.extend(check_2_1(body))
        findings.extend(check_2_2(body))
        return findings

    findings.extend(check_1_1(fm, skill_dir))
    findings.extend(check_1_2(fm))
    findings.extend(check_1_3(fm))
    findings.extend(check_1_4(fm))
    findings.extend(check_1_5(fm))
    findings.extend(check_1_6(lines))
    findings.extend(check_1_7(skill_dir))
    findings.extend(check_1_8(skill_dir))
    findings.extend(check_1_9(skill_dir))
    findings.extend(check_1_10(skill_dir))
    findings.extend(check_1_11(skill_dir))
    findings.extend(check_2_1(body))
    findings.extend(check_2_2(body))
    findings.extend(check_2_3(body, skill_dir))
    findings.extend(check_3_1(body))
    findings.extend(check_3_2(body))
    findings.extend(check_3_3(body))
    findings.extend(check_3_4(body))
    findings.extend(check_3_5(body))
    findings.extend(check_3_6(body))
    findings.extend(check_3_7(body, findings))
    findings.extend(check_4_1(body))
    findings.extend(check_4_2(body))
    findings.extend(check_4_3(body))
    findings.extend(check_4_4(body))
    findings.extend(check_4_5(body))
    findings.extend(check_4_6(body))
    findings.extend(check_4_7(skill_dir))
    emit_progress("Collecting supporting context...")
    _ = collect_supporting_context(skill_path)
    return findings


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print("Usage: eval.py <path-to-SKILL.md|skill-dir> [--ci]")
        print()
        print("Deterministic skill evaluator (Tier 1-2).")
        print("Validates SKILL.md against agentskills.io spec and script best practices.")
        print()
        print("Checks:")
        print("  1.1-1.6   Spec compliance (frontmatter, name, description, line count)")
        print("  1.7       Scripts require tests")
        print("  1.8       Scripts must implement --help")
        print("  1.9       Scripts should use structured output (JSON/CSV)")
        print("  1.10      Scripts must not use interactive prompts")
        print("  1.11      Only Python (.py) and bash (.sh) scripts allowed")
        print("  2.1-2.3   Security (unscoped tools, destructive ops, no MCP)")
        print()
        print("Options:")
        print("  --ci    Machine-readable output with exit codes")
        print("  --help  Show this help and exit")
        print()
        print("Exit codes:")
        print("  0  PASS (no errors, no warnings)")
        print("  1  FAIL (errors present)")
        print("  2  WARN (warnings only)")
        sys.exit(0)

    if len(sys.argv) < 2:
        print("Usage: eval.py <path-to-SKILL.md|skill-dir> [--ci]", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    ci_mode = "--ci" in sys.argv

    findings = evaluate(path)
    errors = [f for f in findings if f.severity == "ERROR"]
    warnings = [f for f in findings if f.severity == "WARN"]

    if ci_mode:
        resolved = Path(path) / "SKILL.md" if Path(path).is_dir() else Path(path)
        print(json.dumps(build_json_result(resolved, findings), indent=None, separators=(",", ":")))
    else:
        resolved = Path(path) / "SKILL.md" if Path(path).is_dir() else Path(path)
        skill_name = resolved.parent.name if resolved.name == "SKILL.md" else resolved.stem
        line_count = len(resolved.read_text().splitlines()) if resolved.exists() else 0

        print()
        print(f"  {BOLD}Skill: {skill_name}{RESET}")
        print(f"  Baseline: {line_count} lines")
        print()

        # Phase 1: Deterministic
        print_separator("DETERMINISTIC EVALUATION")
        print()

        # Tier 1 box: Spec Compliance
        spec_checks = [f for f in findings if f.check_id.startswith("1.")]
        print_box_top("Tier 1 — Spec Compliance (1.1-1.11)")
        if spec_checks:
            for f in spec_checks:
                icon = "🔴" if f.severity == "ERROR" else "🟡"
                print_box_line(f"{icon} {BOLD}{f.check_id}{RESET}: {f.message[:40]}")
        else:
            print_box_line("🟢 All checks passed")
        print_box_bottom()
        print()

        # Tier 2 box: Security
        sec_checks = [f for f in findings if f.check_id.startswith("2.")]
        print_box_top("Tier 2 — Security (2.1-2.3)")
        if sec_checks:
            for f in sec_checks:
                icon = "🔴" if f.severity == "ERROR" else "🟡"
                print_box_line(f"{icon} {BOLD}{f.check_id}{RESET}: {f.message[:40]}")
        else:
            print_box_line("🟢 All checks passed")
        print_box_bottom()
        print()

        token_checks = [f for f in findings if f.check_id.startswith("3.")]
        print_box_top("Tier 3 — Token Efficiency (3.1-3.7)")
        if token_checks:
            for f in token_checks:
                icon = "🔴" if f.severity == "ERROR" else "🟡"
                print_box_line(f"{icon} {BOLD}{f.check_id}{RESET}: {f.message[:40]}")
        else:
            print_box_line("🟢 All checks passed")
        print_box_bottom()
        print()

        effectiveness_checks = [f for f in findings if f.check_id.startswith("4.")]
        print_box_top("Tier 4 — Effectiveness (4.1-4.7)")
        if effectiveness_checks:
            for f in effectiveness_checks:
                icon = "🔴" if f.severity == "ERROR" else "🟡"
                print_box_line(f"{icon} {BOLD}{f.check_id}{RESET}: {f.message[:40]}")
        else:
            print_box_line("🟢 All checks passed")
        print_box_bottom()
        print()

        # Summary — deterministic findings
        print_separator("DETERMINISTIC RESULTS")
        print()
        result = "FAIL" if errors else ("WARN" if warnings else "PASS")
        if errors:
            print(f"  {RED}{BOLD}Errors ({len(errors)}){RESET}")
            for f in errors:
                print(f"    {f.colored()}")
            print()
        if warnings:
            print(f"  {YELLOW}{BOLD}Warnings ({len(warnings)}){RESET}")
            for f in warnings:
                print(f"    {f.colored()}")
            print()
        if not errors and not warnings:
            print("  🟢 No issues found.")
            print()

        # Result banner
        inner = BOX_WIDTH - 2
        if result == "PASS":
            label = "🟢 PASS"
            color = GREEN
        elif result == "WARN":
            label = "🟡 WARN"
            color = YELLOW
        else:
            label = "🔴 FAIL"
            color = RED
        banner_text = f"Tier 1-2 Result: {label}"
        pad = inner - len(banner_text)
        left = pad // 2
        right = pad - left
        print(f"  {color}{BOLD}{'━' * BOX_WIDTH}{RESET}")
        print(f"  {color}{BOLD}{' ' * left}{banner_text}{' ' * right}{RESET}")
        print(f"  {color}{BOLD}{'━' * BOX_WIDTH}{RESET}")
        print()

    exit_code = 1 if errors else (2 if warnings else 0)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
