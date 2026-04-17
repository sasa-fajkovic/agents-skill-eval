#!/usr/bin/env python3
"""Deterministic skill evaluator (Tier 1-2). Implements checks 1.1-1.11, 2.1-2.2.

Based on the agentskills.io specification and script design best practices:
https://agentskills.io/specification
https://agentskills.io/skill-creation/using-scripts#designing-scripts-for-agentic-use
"""

import os
import re
import sys
import yaml
from pathlib import Path

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


class Finding:
    def __init__(self, check_id: str, severity: str, message: str):
        self.check_id = check_id
        self.severity = severity  # ERROR or WARN
        self.message = message

    def __str__(self):
        return f"[{self.severity}] {self.check_id}: {self.message}"

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
                findings.append(Finding(
                    "1.11", "ERROR",
                    f"scripts/{f.name} — only Python (.py) and bash (.sh) scripts "
                    f"are allowed. {f.suffix} requires an additional runtime",
                ))
    return findings


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


def evaluate(path: str) -> list[Finding]:
    """Run all checks on a SKILL.md file."""
    skill_path = Path(path)
    if skill_path.is_dir():
        skill_path = skill_path / "SKILL.md"
    if not skill_path.exists():
        return [Finding("--", "ERROR", f"file not found: {skill_path}")]

    text = skill_path.read_text()
    lines = text.splitlines()
    skill_dir = str(skill_path.parent)
    fm, body = parse_frontmatter(text)

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
        print("  2.1-2.2   Security (unscoped tools, destructive ops)")
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
        result = "FAIL" if errors else ("WARN" if warnings else "PASS")
        print(f"SKILL_EVAL_RESULT={result}")
        print(f"SKILL_EVAL_ERRORS={len(errors)}")
        print(f"SKILL_EVAL_WARNINGS={len(warnings)}")
        print()
        for f in errors:
            print(str(f))
        for f in warnings:
            print(str(f))
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
        print_box_top("Tier 2 — Security (2.1-2.2)")
        if sec_checks:
            for f in sec_checks:
                icon = "🔴" if f.severity == "ERROR" else "🟡"
                print_box_line(f"{icon} {BOLD}{f.check_id}{RESET}: {f.message[:40]}")
        else:
            print_box_line("🟢 All checks passed")
        print_box_bottom()
        print()

        # Phase 2: LLM (placeholder — the LLM tiers run outside this script)
        print_separator("LLM EVALUATION (Tier 3-4)")
        print(f"  {DIM}Tier 3 (Token Efficiency) and Tier 4 (Effectiveness)")
        print(f"  are evaluated by the LLM after this script completes.{RESET}")
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
