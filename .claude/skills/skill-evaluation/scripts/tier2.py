from __future__ import annotations

import re

from common import DESTRUCTIVE_OPS, HARDCODED_USER_PATH, SAFEGUARD, SCOPED_TOOL_CONTEXT, UNSCOPED_TOOL, Finding
from extract import entrypoint_scripts, read_text_file


def check_2_1(body: str) -> list[Finding]:
    findings = []
    lines = body.split("\n")
    for match in UNSCOPED_TOOL.finditer(body):
        line_num = body[:match.start()].count("\n") + 1
        context_start = max(0, line_num - 1)
        context_end = min(len(lines), line_num + 3)
        context = "\n".join(lines[context_start:context_end])
        if SCOPED_TOOL_CONTEXT.search(context):
            continue
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


_SAFE_DESTRUCTIVE_CONTEXT = re.compile(
    r"(?i)("
    r"/tmp/|/tmp\b|\$TMPDIR|\$TEMP_DIR|\btmpdir\b|\btempdir\b|"       # temp directories
    r"temp(?:orary)?\s+(?:file|dir|folder|branch)|"                     # "temporary file/branch"
    r"2>/dev/null|2>\s*/dev/null|"                                      # suppressed errors
    r"cleanup|clean[- ]?up|teardown|tear[- ]?down|"                     # cleanup phases
    r"fresh\s+(?:branch|clone|copy|checkout|start)|"                    # fresh setup
    r"scratch\s+(?:branch|dir|folder)|"                                 # scratch resources
    r"disposable|ephemeral"                                             # ephemeral contexts
    r")"
)


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
                # Skip if context shows safe usage (temp files, cleanup, suppressed errors)
                if _SAFE_DESTRUCTIVE_CONTEXT.search(context):
                    continue
                findings.append(Finding("2.2", "WARN", f'line {index + 1}: destructive operation "{match.group()}" without safeguard'))
    return findings


def check_2_4(body: str, skill_dir: str) -> list[Finding]:
    """Detect hardcoded user home directory paths."""
    findings: list[Finding] = []
    lines = body.split("\n")
    for index, line in enumerate(lines):
        match = HARDCODED_USER_PATH.search(line)
        if match:
            findings.append(Finding("2.4", "WARN", f"line {index + 1}: hardcoded user path \"{match.group().rstrip('/')}\" — use $HOME, ~, or environment variables instead"))
    for script_path, display in entrypoint_scripts(skill_dir):
        try:
            content = read_text_file(script_path)
        except (OSError, UnicodeDecodeError):
            continue
        for index, line in enumerate(content.split("\n")):
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            match = HARDCODED_USER_PATH.search(line)
            if match:
                findings.append(Finding("2.4", "WARN", f"{display}:{index + 1}: hardcoded user path \"{match.group().rstrip('/')}\" — use $HOME, ~, or environment variables instead"))
    return findings


def run_tier2_checks(body: str, skill_dir: str) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(check_2_1(body))
    findings.extend(check_2_2(body))
    findings.extend(check_2_4(body, skill_dir))
    return findings
