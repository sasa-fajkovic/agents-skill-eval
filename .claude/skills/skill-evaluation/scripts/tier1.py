from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from common import (
    PREFERRED_SCRIPT_EXTENSIONS,
    CLAUDE_CODE_EXTENSIONS,
    Finding,
    HELP_PATTERNS,
    MAX_COMPAT_LEN,
    MAX_DESC_LEN,
    RECOMMENDED_MAX_LINES,
    MAX_NAME_LEN,
    NON_INTERACTIVE_FALLBACK,
    SPEC_FIELDS,
    STRUCTURED_OUTPUT,
    WHEN_PHRASES,
    INTERACTIVE_PROMPT,
)
from extract import entrypoint_scripts, read_text_file, script_analysis_issues, script_inventory

_TEMP_DIR_RE = re.compile(r"^eval-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def check_1_1(fm: dict, skill_dir: str) -> list[Finding]:
    findings = []
    if "name" not in fm:
        findings.append(Finding("1.1", "ERROR", "missing required field 'name'"))
        return findings
    name = fm["name"]
    if not isinstance(name, str):
        findings.append(Finding("1.1", "ERROR", f"name must be a string, got {type(name).__name__}; quote YAML scalar values interpreted as numbers or booleans"))
        return findings
    name = unicodedata.normalize("NFKC", name.strip())
    if not 1 <= len(name) <= MAX_NAME_LEN:
        findings.append(Finding("1.1", "ERROR", f"name is {len(name)} chars (must be 1-{MAX_NAME_LEN})"))
    if "--" in name:
        findings.append(Finding("1.1", "ERROR", f'name "{name}" contains consecutive hyphens; the spec requires single hyphens as separators'))
    elif name != name.lower() or name.startswith("-") or name.endswith("-") or not all(char.isalnum() or char == "-" for char in name):
        findings.append(Finding("1.1", "ERROR", f'name "{name}" must be lowercase alphanumeric + hyphens, no leading/trailing/consecutive hyphens'))
    dirname = Path(skill_dir).name
    # Skip directory-name comparison for synthetic upload directories (e.g. /tmp/eval-<uuid>).
    if not _TEMP_DIR_RE.match(dirname) and name != unicodedata.normalize("NFKC", dirname):
        findings.append(Finding("1.1", "ERROR", f'name "{name}" does not match directory "{dirname}"'))
    return findings


def check_1_2(fm: dict) -> list[Finding]:
    findings = []
    if "description" not in fm:
        findings.append(Finding("1.2", "ERROR", "missing required field 'description'"))
        return findings
    desc = fm["description"]
    if not isinstance(desc, str) or not desc.strip():
        findings.append(Finding("1.2", "ERROR", "description must be a non-empty string; quote YAML scalar values interpreted as numbers or booleans"))
        return findings
    if len(desc) > MAX_DESC_LEN:
        findings.append(Finding("1.2", "ERROR", f"description is {len(desc)} chars (max {MAX_DESC_LEN})"))
    if not WHEN_PHRASES.search(desc):
        findings.append(Finding("1.2", "WARN", 'optional evaluator heuristic: no recognized activation context; describe when the skill helps (wording is flexible)'))
    return findings


def check_1_3(fm: dict) -> list[Finding]:
    findings = []
    for key in fm:
        if key in SPEC_FIELDS:
            continue
        if key in CLAUDE_CODE_EXTENSIONS:
            findings.append(Finding("1.3", "WARN", f'"{key}" is a Claude Code extension (not in the agentskills.io specification) — functional in CC but reduces cross-platform portability'))
        else:
            findings.append(Finding("1.3", "ERROR", f'"{key}" is not a recognized agentskills.io field (move to metadata: if needed)'))
    return findings


def check_1_4(fm: dict) -> list[Finding]:
    findings = []
    if "license" in fm:
        if not isinstance(fm["license"], str):
            findings.append(Finding("1.4", "ERROR", f"license must be a string, got {type(fm['license']).__name__}"))
    if "compatibility" in fm:
        if not isinstance(fm["compatibility"], str):
            findings.append(Finding("1.4", "ERROR", f"compatibility must be a string, got {type(fm['compatibility']).__name__}"))
        elif not fm["compatibility"].strip():
            findings.append(Finding("1.4", "ERROR", "compatibility is present but empty"))
        elif len(fm["compatibility"]) > MAX_COMPAT_LEN:
            findings.append(Finding("1.4", "ERROR", f"compatibility is {len(fm['compatibility'])} chars (max {MAX_COMPAT_LEN})"))
    if "allowed-tools" in fm and not isinstance(fm["allowed-tools"], str):
        findings.append(Finding("1.4", "ERROR", f"allowed-tools must be a space-separated string, got {type(fm['allowed-tools']).__name__}"))
    if "metadata" in fm:
        meta = fm["metadata"]
        if not isinstance(meta, dict):
            findings.append(Finding("1.4", "ERROR", f"metadata must be a mapping, got {type(meta).__name__}"))
        else:
            for key, value in meta.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    findings.append(Finding("1.4", "ERROR", f"metadata entry has key type {type(key).__name__} and value type {type(value).__name__} — both key and value must be strings"))
    return findings


def check_1_5(fm: dict) -> list[Finding]:
    return []


def check_1_6(lines: list[str], description: str = "") -> list[Finding]:
    count = len(lines)
    if count >= RECOMMENDED_MAX_LINES:
        return [Finding("1.6", "WARN", f"SKILL.md is {count} lines; the spec recommends under {RECOMMENDED_MAX_LINES} lines (not a hard limit)")]
    return []


def check_1_8(skill_dir: str) -> list[Finding]:
    findings = []
    for script_path, display in entrypoint_scripts(skill_dir):
        try:
            content = read_text_file(script_path)
        except (OSError, UnicodeDecodeError):
            continue
        has_help = any(pattern.search(content) for pattern in HELP_PATTERNS)
        if not has_help:
            findings.append(Finding("1.8", "ERROR", f"evaluator policy: {display} does not implement --help — agents rely on --help to learn a script's interface"))
    return findings


def check_1_9(skill_dir: str) -> list[Finding]:
    findings = []
    for script_path, display in entrypoint_scripts(skill_dir):
        try:
            content = read_text_file(script_path)
        except (OSError, UnicodeDecodeError):
            continue
        has_output = any(token in content for token in ("print", "echo", "console.log", "printf"))
        has_structured = bool(STRUCTURED_OUTPUT.search(content))
        if has_output and not has_structured:
            findings.append(Finding("1.9", "WARN", f"evaluator policy: {display} — consider using structured output (JSON/CSV) instead of free-form text for agent composability"))
    return findings


def check_1_10(skill_dir: str) -> list[Finding]:
    findings = []
    for script_path, display in entrypoint_scripts(skill_dir):
        try:
            content = read_text_file(script_path)
        except (OSError, UnicodeDecodeError):
            continue
        # Check if the script has a non-interactive fallback pattern anywhere
        has_fallback = bool(NON_INTERACTIVE_FALLBACK.search(content))
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
            # If the script has a non-interactive fallback, also check local context
            if has_fallback:
                lines = content.splitlines()
                ctx_start = max(0, line_num - 6)
                ctx_end = min(len(lines), line_num + 5)
                context = "\n".join(lines[ctx_start:ctx_end])
                if NON_INTERACTIVE_FALLBACK.search(context):
                    continue
            findings.append(Finding("1.10", "ERROR", f'evaluator policy: {display}:{line_num} — interactive prompt detected ("{match.group().strip()}"). Agents cannot respond to prompts; use flags or env vars instead'))
    return findings


_DATA_EXTENSIONS = {".json", ".yaml", ".yml", ".toml", ".txt", ".md", ".csv", ".xml", ".ini", ".cfg", ".conf", ".env"}


def check_1_11(skill_dir: str) -> list[Finding]:
    findings = [Finding("1.11", "WARN", f"evaluator read policy: {display} could not be safely inspected; script and security analysis is incomplete (regular files up to 5 MiB, without symlinks, require supported safe file access).")
                for display in script_analysis_issues(skill_dir)]
    candidates, _issues = script_inventory(skill_dir)
    for file_path, display in candidates:
        if not display.startswith("scripts/") or file_path.suffix in _DATA_EXTENSIONS:
            continue
        if file_path.suffix and file_path.suffix not in PREFERRED_SCRIPT_EXTENSIONS:
            findings.append(Finding("1.11", "WARN", f"evaluator portability policy: {display} uses {file_path.suffix}; prefer Python (.py) or Bash (.sh) when available, or document the required runtime in compatibility. The spec permits runtime-supported languages"))
    return findings


def run_tier1_checks(fm: dict, lines: list[str], skill_dir: str) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(check_1_1(fm, skill_dir))
    findings.extend(check_1_2(fm))
    findings.extend(check_1_3(fm))
    findings.extend(check_1_4(fm))
    findings.extend(check_1_5(fm))
    desc = fm.get("description", "") if isinstance(fm.get("description"), str) else ""
    findings.extend(check_1_6(lines, desc))
    findings.extend(check_1_8(skill_dir))
    findings.extend(check_1_9(skill_dir))
    findings.extend(check_1_10(skill_dir))
    findings.extend(check_1_11(skill_dir))
    return findings
