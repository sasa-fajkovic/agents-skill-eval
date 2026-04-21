from __future__ import annotations

import difflib
import re

from common import MCP_ALLOWED_NAMESPACES, MCP_BLOCKED_NAMESPACES, MCP_NEGATION, MCP_REFERENCE, PRELOAD_REFERENCE, SCRIPT_WORTHINESS, STANDARD_TOOL_TUTORIAL, VERBOSE_PROSE, Finding
from extract import body_paragraphs, entrypoint_scripts, fenced_code_blocks, normalize_text_block, read_text_file

_TEMPLATE_INDICATOR = re.compile(
    r"^\s*#{1,3}\s|"           # markdown headings inside the block
    r"^\s*- \[[ x]\]|"        # checkbox items
    r"\[.*?\.{3}.*?\]|"       # bracket placeholders like [2-3 sentences...]
    r"\[.*?steps.*?\]|"        # [steps], [remaining steps]
    r"\(.*?(?:optional|describe|sentence|link|placeholder|specify|fill).*?\)|"  # parenthetical placeholders
    r"<[A-Z][A-Z _/-]*>|"     # uppercase angle-bracket placeholders like <URL>, <DESCRIPTION>
    r"\{%|<%|{{",              # template engine syntax
    re.IGNORECASE | re.MULTILINE,
)


_CONFIG_CONTENT_SNIFF = re.compile(
    r"^\s*[\{\[\]]|"                      # starts with { [ ]
    r"^\s*---\s*$|"                        # YAML document delimiter
    r'^\s*"[^"]+"\s*:|'                    # JSON key
    r"^\s*[a-zA-Z_][a-zA-Z0-9_-]*\s*:|"   # YAML key
    r"^\s*//|"                             # JSON comments (jsonc)
    r"^\s*<[a-zA-Z]|"                      # XML/HTML tag
    r"^\s*\+[-+]+\+\s*$|"                 # ASCII table border
    r"^\s*\|.*\|.*\|\s*$",                # ASCII table row
    re.MULTILINE,
)

_ASKUSERQUESTION_BLOCK = re.compile(r"(?i)askuserquestion|ask_user_question|AskUser")


def check_3_1(body: str) -> list[Finding]:
    executable_langs = {"bash", "sh", "shell", "python", "py", "zsh", "fish", ""}
    findings = []
    for start, end, lang, content in fenced_code_blocks(body):
        lines = [line for line in content.splitlines() if line.strip()]
        if len(lines) <= 5:
            continue
        if lang in {"json", "jsonc", "json5", "yaml", "yml", "text", "output", "markdown", "md", "xml", "html", "css", "toml", "ini", "conf", "cfg", "properties", "env", "graphql", "gql", "proto", "protobuf", "sql"}:
            continue
        if lang not in executable_langs and not SCRIPT_WORTHINESS.search(content):
            continue
        # For unlabeled blocks, sniff content to skip JSON/YAML/config/ASCII-art
        if lang == "":
            first_nonempty = next((l.strip() for l in content.splitlines() if l.strip()), "")
            config_line_count = sum(1 for l in lines if _CONFIG_CONTENT_SNIFF.match(l))
            if config_line_count >= len(lines) * 0.5:
                continue
            # Skip AskUserQuestion JSON blocks
            if _ASKUSERQUESTION_BLOCK.search(content) and first_nonempty.startswith("{"):
                continue
        # Skip template/documentation blocks (markdown headings, checkboxes, bracket placeholders)
        template_lines = sum(1 for l in lines if _TEMPLATE_INDICATOR.search(l))
        if template_lines >= max(2, len(lines) * 0.3):
            continue
        findings.append(Finding("3.1", "WARN", f"code block at lines {start}-{end} is {len(lines)} lines; move long executable logic into scripts/"))
    return findings


_CORE_CONTENT_HEADING = re.compile(
    r"(?i)^#{1,4}\s+(?:.*?)("
    r"test\s*(?:matrix|cases?|plan|scenarios?)|"
    r"api\s*(?:endpoint|route|interface)|"
    r"rule|check|validation|requirement|"
    r"error\s*code|status\s*code|"
    r"mapping|lookup|reference|"
    r"workflow|pipeline|step|"
    r"endpoint|route|command"
    r")",
    re.MULTILINE,
)


def _is_under_core_heading(body: str, line_num: int) -> bool:
    """Check if *line_num* falls under a heading that indicates core skill content."""
    lines = body.splitlines()
    for i in range(line_num - 2, -1, -1):
        if i < len(lines) and lines[i].strip().startswith("#"):
            return bool(_CORE_CONTENT_HEADING.match(lines[i].strip()))
    return False


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
                # Skip if under a core content heading
                if not _is_under_core_heading(body, table_start):
                    findings.append(Finding("3.2", "WARN", f"table at lines {table_start}-{index - 1} has {table_rows - 2} data rows; move large reference material to references/"))
            table_start = None
            table_rows = 0
    if table_start and table_rows > 12:
        if not _is_under_core_heading(body, table_start):
            findings.append(Finding("3.2", "WARN", f"table at lines {table_start}-{len(lines)} has {table_rows - 2} data rows; move large reference material to references/"))

    for start, end, _lang, content in fenced_code_blocks(body):
        block_lines = [line for line in content.splitlines() if line.strip()]
        if len(block_lines) > 15 and any(line.strip().startswith(("{", "}", "[", "]")) or ":" in line for line in block_lines[:5]):
            if not _is_under_core_heading(body, start):
                findings.append(Finding("3.2", "WARN", f"mapping-style block at lines {start}-{end} is {len(block_lines)} lines; move dense reference data to references/"))
    return findings


def check_3_3(body: str) -> list[Finding]:
    findings = []
    for line_num, line in enumerate(body.splitlines(), start=1):
        if STANDARD_TOOL_TUTORIAL.search(line):
            findings.append(Finding("3.3", "WARN", f"line {line_num}: explains standard tool usage the agent likely already knows"))
    return findings


_CONTRAST_HEADING = re.compile(
    r"(?i)(wrong|correct|bad|good|before|after|"
    r"mode\s*[a-z]|option\s*[a-z0-9]|approach\s*[a-z0-9]|"
    r"example\s*[0-9]|variant|alternative|"
    r"do\b|don'?t\b|avoid|prefer)"
)

_REST_ENDPOINT_BLOCK = re.compile(
    r"(?i)(curl\s|https?://|GET\s+/|POST\s+/|PUT\s+/|PATCH\s+/|DELETE\s+/)"
)


def _context_around_block(body: str, start_line: int, end_line: int, radius: int = 3) -> str:
    """Return the text of *radius* lines before *start_line* in *body*."""
    lines = body.splitlines()
    ctx_start = max(0, start_line - 1 - radius)
    ctx_end = min(len(lines), start_line - 1)
    return "\n".join(lines[ctx_start:ctx_end])


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
                # Check if surrounding headings indicate these are contrast/comparison pairs
                ctx_a = _context_around_block(body, start_a, end_a)
                ctx_b = _context_around_block(body, start_b, end_b)
                if _CONTRAST_HEADING.search(ctx_a) and _CONTRAST_HEADING.search(ctx_b):
                    continue
                # Skip similar REST endpoint/curl blocks (structurally similar API calls)
                orig_a = blocks[index][3]
                orig_b_idx = next(i for i, (s, _, _, _) in enumerate(blocks) if s == start_b)
                orig_b = blocks[orig_b_idx][3]
                if _REST_ENDPOINT_BLOCK.search(orig_a) and _REST_ENDPOINT_BLOCK.search(orig_b):
                    continue
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


_MCP_TOOL_TOKEN = re.compile(r"(?i)\bmcp__[a-z0-9_]+")
_CLI_FALLBACK = re.compile(
    r"(?i)(instead\s+use|use\s+.*\s+instead|prefer\s+cli|"
    r"fallback\s*:|alternative\s*:|"
    r"\bacli\b|\bgh\b|\bcurl\b|\bkubectl\b|\bjq\b|\brest\s+api\b|"
    r"command[- ]line\s+alternative|"
    r"cli\s+(?:fallback|alternative|equivalent))"
)


def _namespace_status(token: str) -> tuple[str, str | None]:
    """Classify an ``mcp__*`` token as allowed, blocked, or other."""
    lowered = token.lower()
    for prefix in MCP_ALLOWED_NAMESPACES:
        if lowered.startswith(prefix):
            return ("allowed", None)
    for prefix, suggestion in MCP_BLOCKED_NAMESPACES.items():
        if lowered.startswith(prefix):
            return ("blocked", suggestion)
    return ("other", None)


def _scan_mcp(text: str, display_prefix: str) -> list[Finding]:
    """Walk *text* for MCP tool tokens and generic MCP prose references."""
    findings: list[Finding] = []
    lines = text.split("\n")
    for index, line in enumerate(lines):
        # Try specific mcp__<ns> token first
        token_match = _MCP_TOOL_TOKEN.search(line)
        if token_match:
            token = token_match.group()
            status, suggestion = _namespace_status(token)
            if status == "allowed":
                continue
            # Check negation / CLI-fallback in ±3 lines
            ctx_start = max(0, index - 3)
            ctx_end = min(len(lines), index + 4)
            context = "\n".join(lines[ctx_start:ctx_end])
            if MCP_NEGATION.search(context) or _CLI_FALLBACK.search(context):
                continue
            if status == "blocked":
                findings.append(Finding(
                    "3.7", "ERROR",
                    f'{display_prefix}{index + 1}: MCP tool "{token}" has a portable alternative — use {suggestion} instead',
                ))
            else:
                findings.append(Finding(
                    "3.7", "WARN",
                    f'{display_prefix}{index + 1}: MCP tool "{token}" — review whether a CLI/API alternative exists',
                ))
            continue
        # Fall back to generic MCP prose reference
        prose_match = MCP_REFERENCE.search(line)
        if not prose_match:
            continue
        ctx_start = max(0, index - 3)
        ctx_end = min(len(lines), index + 4)
        context = "\n".join(lines[ctx_start:ctx_end])
        if MCP_NEGATION.search(context) or _CLI_FALLBACK.search(context):
            continue
        findings.append(Finding(
            "3.7", "WARN",
            f'{display_prefix}{index + 1}: generic MCP reference "{prose_match.group().strip()}" — review whether a CLI/API alternative exists',
        ))
    return findings


def check_3_7(body: str, skill_dir: str) -> list[Finding]:
    findings = _scan_mcp(body, "line ")
    for script_path, display in entrypoint_scripts(skill_dir):
        try:
            content = read_text_file(script_path)
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(_scan_mcp(content, f"{display}:"))
    return findings


def run_tier3_checks(body: str, skill_dir: str) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(check_3_1(body))
    findings.extend(check_3_2(body))
    findings.extend(check_3_3(body))
    findings.extend(check_3_4(body))
    findings.extend(check_3_5(body))
    findings.extend(check_3_6(body))
    findings.extend(check_3_7(body, skill_dir))
    return findings
