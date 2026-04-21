from __future__ import annotations

import difflib
import re

from common import MCP_NEGATION, MCP_REFERENCE, PRELOAD_REFERENCE, SCRIPT_WORTHINESS, STANDARD_TOOL_TUTORIAL, VERBOSE_PROSE, Finding
from extract import body_paragraphs, fenced_code_blocks, normalize_text_block

_TEMPLATE_INDICATOR = re.compile(
    r"^\s*#{1,3}\s|"           # markdown headings inside the block
    r"^\s*- \[[ x]\]|"        # checkbox items
    r"\[.*?\.{3}.*?\]|"       # bracket placeholders like [2-3 sentences...]
    r"\[.*?steps.*?\]|"        # [steps], [remaining steps]
    r"\{%|<%|{{",              # template engine syntax
    re.IGNORECASE | re.MULTILINE,
)


def check_3_1(body: str) -> list[Finding]:
    executable_langs = {"bash", "sh", "shell", "python", "py", "zsh", "fish", ""}
    findings = []
    for start, end, lang, content in fenced_code_blocks(body):
        lines = [line for line in content.splitlines() if line.strip()]
        if len(lines) <= 5:
            continue
        if lang in {"json", "yaml", "yml", "text", "output", "markdown", "md"}:
            continue
        if lang not in executable_langs and not SCRIPT_WORTHINESS.search(content):
            continue
        # Skip template/documentation blocks (markdown headings, checkboxes, bracket placeholders)
        template_lines = sum(1 for l in lines if _TEMPLATE_INDICATOR.search(l))
        if template_lines >= max(2, len(lines) * 0.3):
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
    for index, (start_a, end_a, block_a) in enumerate(normalized):
        if not block_a or len(block_a) <= 2:
            continue
        for start_b, end_b, block_b in normalized[index + 1:]:
            if not block_b or len(block_b) <= 2:
                continue
            key = (start_a, start_b)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            similarity = difflib.SequenceMatcher(None, "\n".join(block_a), "\n".join(block_b)).ratio()
            if similarity >= 0.8:
                findings.append(Finding("3.4", "WARN", f"code blocks at lines {start_a}-{end_a} and {start_b}-{end_b} are duplicated or near-duplicated"))

    paragraphs = body_paragraphs(body)
    long_paras = [(line_num, text) for line_num, text in paragraphs if len(text.split()) >= 10]
    seen_prose = set()
    for index, (line_a, text_a) in enumerate(long_paras):
        for line_b, text_b in long_paras[index + 1:]:
            key = (line_a, line_b)
            if key in seen_prose:
                continue
            seen_prose.add(key)
            similarity = difflib.SequenceMatcher(None, text_a.lower(), text_b.lower()).ratio()
            if similarity >= 0.8:
                findings.append(Finding("3.4", "WARN", f"prose near line {line_a} and line {line_b} are near-duplicated"))

    return findings


def check_3_5(body: str) -> list[Finding]:
    import re

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


def run_tier3_checks(body: str, findings_so_far: list[Finding]) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(check_3_1(body))
    findings.extend(check_3_2(body))
    findings.extend(check_3_3(body))
    findings.extend(check_3_4(body))
    findings.extend(check_3_5(body))
    findings.extend(check_3_6(body))
    findings.extend(check_3_7(body, findings_so_far + findings))
    return findings
