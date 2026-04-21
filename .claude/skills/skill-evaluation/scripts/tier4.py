from __future__ import annotations

import re

from common import (
    AMBIGUOUS_LANGUAGE,
    CONTEXT_QUALIFICATION,
    DEFAULT_BEHAVIOR,
    EXPLANATORY_NEGATIVE,
    IDEMPOTENT_GUARD,
    MIN_BODY_LINES,
    NEGATIVE_ONLY,
    NON_IDEMPOTENT_OP,
    OUTPUT_OR_FORMAT,
    POSITIVE_ALTERNATIVE,
    REDIRECT_SKILL,
    SECURITY_PROHIBITION,
    SUCCESS_CRITERIA,
    TOOL_DELEGATION,
    Finding,
)
from extract import entrypoint_scripts, extract_output_section, has_nearby_example, read_text_file


_SPECIFIC_VALUE_NEARBY = re.compile(
    r"`[^`]+`|"           # inline code
    r"\d+|"               # number
    r"https?://|"         # URL
    r"\bpath\b|"          # concrete reference to file path
    r"\bfile\b|"          # concrete reference to file
    r"\bformat\b"         # specific format reference
)


def check_4_1(body: str) -> list[Finding]:
    findings = []
    for line_num, line in enumerate(body.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "```", "|", "-")):
            continue
        # Skip numbered list items (e.g. "1. Do the appropriate thing")
        if re.match(r"^\d+\.\s", stripped):
            continue
        if AMBIGUOUS_LANGUAGE.search(stripped):
            if CONTEXT_QUALIFICATION.search(stripped):
                continue
            # Skip if the line also contains specific/concrete values nearby
            if _SPECIFIC_VALUE_NEARBY.search(stripped):
                continue
            findings.append(Finding("4.1", "WARN", f"line {line_num}: instruction may be ambiguous or underspecified"))
    return findings


def check_4_2(body: str) -> list[Finding]:
    findings = []
    lines = body.splitlines()
    output_line, output_section = extract_output_section(body)
    if output_section and not re.search(r"(?i)\bexample\b|```", output_section):
        if not TOOL_DELEGATION.search(output_section):
            findings.append(Finding("4.2", "WARN", f"line {output_line}: output requirements are present without a concrete example"))
    for index, line in enumerate(lines):
        if OUTPUT_OR_FORMAT.search(line) and not has_nearby_example(lines, index):
            context = "\n".join(lines[max(0, index - 2):min(len(lines), index + 3)])
            if TOOL_DELEGATION.search(context):
                continue
            findings.append(Finding("4.2", "WARN", f"line {index + 1}: output or formatting guidance lacks a nearby concrete example"))
    return findings


_BOLD_ONLY_LINE = re.compile(r"^\s*\*\*[^*]+\*\*\s*:?\s*$")


def check_4_3(body: str) -> list[Finding]:
    findings = []
    lines = body.splitlines()
    in_fence = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        # Skip lines inside fenced code blocks (code comments, wrong-pattern examples)
        if in_fence:
            continue
        if not NEGATIVE_ONLY.search(line):
            continue
        # Skip bold-only section headers like "**Do NOT:**"
        if _BOLD_ONLY_LINE.match(stripped):
            continue
        # Skip explanatory/descriptive negatives (3rd person subjects)
        if EXPLANATORY_NEGATIVE.search(line):
            continue
        # Skip security/safety prohibitions (valid constraints without alternatives)
        if SECURITY_PROHIBITION.search(line):
            continue
        # Widen context window to ±4 lines for positive alternative search
        context = "\n".join(lines[max(0, index - 4):index + 5])
        if not POSITIVE_ALTERNATIVE.search(context):
            findings.append(Finding("4.3", "WARN", f"line {index + 1}: negative-only instruction does not say what to do instead"))
    return findings


def _strip_inline_commands(text: str) -> str:
    """Remove multi-word inline code spans (command references) from *text*.

    Single-token inline code like `--target` is kept because it likely
    documents a flag the skill accepts.  Multi-word spans like
    `gh pr edit --add-reviewer @copilot` are stripped because they are
    command-line documentation, not flags needing default-behavior docs.
    """
    return re.sub(r"`[^`]*\s[^`]*`", "", text)


_TABLE_SEPARATOR = re.compile(r"^\|?[\s:|-]+\|[\s:|-]+\|?$")


def check_4_4(body: str) -> list[Finding]:
    findings = []
    lines = body.splitlines()
    in_fence = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # Skip headings — they describe sections, not flag usage
        if stripped.startswith("#"):
            continue
        # Skip markdown table separator rows (e.g. |-------|---------|)
        if _TABLE_SEPARATOR.match(stripped):
            continue
        # Strip multi-word inline code so command refs like `gh pr edit --flag` don't trigger
        cleaned = _strip_inline_commands(line)
        if "$ARGUMENTS" in cleaned or re.search(r"\$[0-9]+", cleaned) or re.search(r"--[a-z][a-z0-9-]*", cleaned, re.IGNORECASE):
            context = "\n".join(lines[max(0, index - 5): min(len(lines), index + 6)])
            if not DEFAULT_BEHAVIOR.search(context):
                findings.append(Finding("4.4", "WARN", f"line {index + 1}: input or flag is referenced without clear default behavior"))
    return findings


def check_4_5(body: str) -> list[Finding]:
    findings = []
    lines = body.splitlines()
    in_fence = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        # Skip markdown headings (e.g. "# GH PR Create" matching "gh pr create")
        if stripped.startswith("#"):
            continue
        if not NON_IDEMPOTENT_OP.search(line):
            continue
        # Use wider context for idempotency guard search when inside fenced code blocks
        window = 5 if in_fence else 2
        context = "\n".join(lines[max(0, index - window): min(len(lines), index + window + 1)])
        if not IDEMPOTENT_GUARD.search(context):
            findings.append(Finding("4.5", "WARN", f"line {index + 1}: action may not be idempotent if retried"))
    return findings


def check_4_6(body: str) -> list[Finding]:
    if SUCCESS_CRITERIA.search(body):
        return []
    return [Finding("4.6", "WARN", "no clear success criteria or output contract found")]


def check_4_7(skill_dir: str) -> list[Finding]:
    findings = []
    exit_code_doc = re.compile(r"(?i)(exit code|return code|exit status|exits? with)")
    for script_path, display in entrypoint_scripts(skill_dir):
        try:
            content = read_text_file(script_path)
        except (OSError, UnicodeDecodeError):
            continue
        has_exit_doc = bool(exit_code_doc.search(content))
        if script_path.suffix == ".py":
            if "sys.exit(" in content and not re.search(r"sys\.exit\((2|3|4|5|6|7|8|9)", content):
                if has_exit_doc:
                    continue
                findings.append(Finding("4.7", "WARN", f"{display} appears to use only basic exit codes; document and use more specific exit codes where meaningful"))
        elif script_path.suffix == ".sh":
            if "exit " in content and not re.search(r"\bexit\s+(2|3|4|5|6|7|8|9)\b", content):
                if has_exit_doc:
                    continue
                findings.append(Finding("4.7", "WARN", f"{display} appears to use only basic exit codes; document and use more specific exit codes where meaningful"))
    return findings


def check_4_8(body: str, has_scripts: bool) -> list[Finding]:
    """Minimum content/substance gate.

    Skills with very short bodies (< MIN_BODY_LINES non-blank lines) and no
    bundled scripts contain too little substance for an agent to act on.
    Redirect/pointer skills that just say "read docs at X" are flagged as
    errors since they provide no actionable content whatsoever.
    """
    non_blank = [line for line in body.splitlines() if line.strip()]
    if len(non_blank) >= MIN_BODY_LINES:
        return []
    if REDIRECT_SKILL.search(body) and len(non_blank) < MIN_BODY_LINES:
        return [Finding("4.8", "ERROR",
                        f"skill body is {len(non_blank)} non-blank lines and appears to be a redirect/pointer — "
                        "a skill must contain actionable instructions, not just a reference to external docs")]
    if not has_scripts and len(non_blank) < MIN_BODY_LINES:
        return [Finding("4.8", "WARN",
                        f"skill body is only {len(non_blank)} non-blank lines with no bundled scripts — "
                        "this is likely too thin to guide an agent effectively")]
    return []


def run_tier4_checks(body: str, skill_dir: str) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(check_4_1(body))
    findings.extend(check_4_2(body))
    findings.extend(check_4_3(body))
    findings.extend(check_4_4(body))
    findings.extend(check_4_5(body))
    findings.extend(check_4_6(body))
    findings.extend(check_4_7(skill_dir))
    has_scripts = bool(list(entrypoint_scripts(skill_dir)))
    findings.extend(check_4_8(body, has_scripts))
    return findings
