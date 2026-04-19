from __future__ import annotations

import re

from common import DESTRUCTIVE_OPS, MCP_NEGATION, MCP_REFERENCE, SAFEGUARD, UNSCOPED_TOOL, Finding
from extract import entrypoint_scripts, read_text_file


def check_2_1(body: str) -> list[Finding]:
    findings = []
    for match in UNSCOPED_TOOL.finditer(body):
        line_num = body[:match.start()].count("\n") + 1
        findings.append(Finding("2.1", "WARN", f'line {line_num}: unscoped tool instruction "{match.group().strip()}"'))
    return findings


def _global_safeguard(body: str) -> bool:
    match = re.search(r"^#{1,3}\s+(?:core\s+)?rules?\s*$", body, re.MULTILINE | re.IGNORECASE)
    if not match:
        return False
    heading_end = body.find("\n", match.start())
    if heading_end == -1:
        return False
    section = body[heading_end + 1:]
    next_heading = re.search(r"^#{1,3}\s+", section, re.MULTILINE)
    if next_heading:
        section = section[: next_heading.start()]
    return bool(SAFEGUARD.search(section))


def check_2_2(body: str) -> list[Finding]:
    has_global = _global_safeguard(body)
    findings = []
    lines = body.split("\n")
    in_fence = False
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        code_segments = [line] if in_fence else re.findall(r"`([^`]+)`", line)
        if not code_segments:
            continue
        code_view = " | ".join(code_segments)
        for match in DESTRUCTIVE_OPS.finditer(code_view):
            context = "\n".join(lines[max(0, index - 5): min(len(lines), index + 6)])
            if not SAFEGUARD.search(context) and not has_global:
                findings.append(Finding("2.2", "WARN", f'line {index + 1}: destructive operation "{match.group()}" without safeguard'))
    return findings


def _find_mcp_findings(text: str, display: str) -> list[Finding]:
    findings = []
    lines = text.split("\n")
    for index, line in enumerate(lines):
        match = MCP_REFERENCE.search(line)
        if not match:
            continue
        context = "\n".join(lines[max(0, index - 1): min(len(lines), index + 2)])
        if MCP_NEGATION.search(context):
            continue
        findings.append(Finding("2.3", "ERROR", f'{display}{index + 1}: MCP usage/reference "{match.group().strip()}" is not allowed; use CLI or direct API alternatives instead'))
    return findings


def check_2_3(body: str, skill_dir: str) -> list[Finding]:
    findings = _find_mcp_findings(body, "line ")
    for script_path, display in entrypoint_scripts(skill_dir):
        try:
            content = read_text_file(script_path)
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(_find_mcp_findings(content, f"{display}:"))
    return findings


def run_tier2_checks(body: str, skill_dir: str) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(check_2_1(body))
    findings.extend(check_2_2(body))
    findings.extend(check_2_3(body, skill_dir))
    return findings
