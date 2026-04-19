from __future__ import annotations

import re

from common import (
    AMBIGUOUS_LANGUAGE,
    DEFAULT_BEHAVIOR,
    IDEMPOTENT_GUARD,
    NEGATIVE_ONLY,
    NON_IDEMPOTENT_OP,
    OUTPUT_OR_FORMAT,
    POSITIVE_ALTERNATIVE,
    SUCCESS_CRITERIA,
    Finding,
)
from extract import entrypoint_scripts, extract_output_section, has_nearby_example, read_text_file


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
    for script_path, display in entrypoint_scripts(skill_dir):
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


def run_tier4_checks(body: str, skill_dir: str) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(check_4_1(body))
    findings.extend(check_4_2(body))
    findings.extend(check_4_3(body))
    findings.extend(check_4_4(body))
    findings.extend(check_4_5(body))
    findings.extend(check_4_6(body))
    findings.extend(check_4_7(skill_dir))
    return findings
