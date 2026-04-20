from __future__ import annotations

import os
import re
import sys
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
    r"\boverwrite\b|\breplace\b|\bkill\b|\bpkill\b",
    re.IGNORECASE,
)
SAFEGUARD = re.compile(
    r"confirm|ask the user|AskUserQuestion|ask.*before|prompt for|"
    r"backup|back.?up|dry.?run|preview|"
    r"check first|verify before|only if.*explicitly",
    re.IGNORECASE,
)
HELP_PATTERNS = [
    re.compile(r"""['"]--help['"]"""),
    re.compile(r"""\b(--help|-h)\b"""),
    re.compile(r"""argparse|ArgumentParser"""),
    re.compile(r"""getopts|getopt"""),
    re.compile(r"""usage\s*[=(]""", re.IGNORECASE),
    re.compile(r"""show_help|print_help|display_help"""),
    re.compile(r"""Usage:"""),
]
STRUCTURED_OUTPUT = re.compile(
    r"json\.dumps|json\.dump|JSON\.stringify|to_json|"
    r"csv\.writer|csv\.DictWriter|"
    r"print.*json|echo.*json|printf.*json|"
    r"jq\s|ConvertTo-Json|"
    r"--format\s+json|--output-format|"
    r"-o\s+json",
    re.IGNORECASE,
)
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
    r"send a post request with content-type: application/json|"
    r"to clone .* run `?git clone`?|use `?grep`? to search|"
    r"use `?curl`? to (?:fetch|download|make)|"
    r"the `?--\w+`? (?:flag|option) (?:makes?|causes?|tells?|enables?)|"
    r"redirect (?:stdout|stderr|output) (?:to|with|using)|"
    r"use `?chmod`? to (?:change|set|make)|"
    r"use `?mkdir`? to create (?:a )?director)"
)
PRELOAD_REFERENCE = re.compile(
    r"(?i)(read all files in references/|load references/ first|start by reading every file in references/|"
    r"pre-load the following references|preload the following references)"
)
AMBIGUOUS_LANGUAGE = re.compile(
    r"(?i)\b(appropriately|as needed|relevant|suitable|proper|reasonable|"
    r"when necessary|if applicable|the correct format|the standard approach|concise|clear|well-structured|"
    r"handle errors|clean up|prepare the environment|set up the environment)\b"
)
CONTEXT_QUALIFICATION = re.compile(
    r"\([^)]*\d[^)]*\)|\bexample\b|:\s*`|:\s*\"|\bsuch as\b|\bi\.e\.\b|\be\.g\.\b|\bspecifically\b"
)
NEGATIVE_ONLY = re.compile(r"(?i)\b(don't|do not|never|avoid|must not)\b")
POSITIVE_ALTERNATIVE = re.compile(r"(?i)\b(use|write|prefer|instead|choose|return|format|do .* not)\b")
DEFAULT_BEHAVIOR = re.compile(r"(?i)(defaults to|if omitted|if not provided|required|when omitted|must provide|optional)")
IDEMPOTENT_GUARD = re.compile(r"(?i)(if not exists|if missing|already exists|mkdir -p|ensure|idempotent|skip if|update if)")
NON_IDEMPOTENT_OP = re.compile(r"(?i)(>>|\bmkdir\s+(?!-p)\S|\bcurl\s+.*-x\s+post|\bgh pr create\b|\bacli\s+jira\s+workitem\s+create\b|\binsert\s+into\b|\bPOST\s+/|\btouch\s+>|\becho\s+.*>>)")
OUTPUT_OR_FORMAT = re.compile(r"(?i)(^#{1,3}\s+output\b|output format|format as|use the following format|template|tone guidance)")
SUCCESS_CRITERIA = re.compile(r"(?i)(^#{1,3}\s+output\b|done when|complete when|completion condition|returns?\b|produces?\b)")
SCOPED_TOOL_CONTEXT = re.compile(
    r"(?i)(only (?:use |the following |these )|"
    r"(?:allowed|permitted) (?:commands|tools|operations)|"
    r":\s*`[^`]+`|"
    r"(?:use|run)\s+`[^`]+`|"
    r"limited to\b|restricted to\b|"
    r"(?:do not|don't|never|must not) (?:use|run|execute)\b)"
)
TOOL_DELEGATION = re.compile(
    r"(?i)(use `?(?:gh|git|curl|jq|acli|kubectl|docker|npm|yarn)\b|"
    r"delegates? (?:to|formatting|output)|"
    r"handled by|"
    r"exit code|return code|exit status)"
)
SCRIPT_COMPLEXITY = re.compile(
    r"(?m)(^\s*(?:if|elif|else|for|while|case|match)\b|"
    r"\bdef\s+\w+|"
    r"try:|except:|"
    r"\bsed\b.*\bs/|"
    r"\bawk\b|"
    r"\beval\b|"
    r"\$\{.*[#%/])"
)
SCRIPT_WORTHINESS = re.compile(
    r"(\||&&|curl\s|wget\s|\bfor\b|\bwhile\b|\bif\b|\bawk\b|\bsed\b|\bjq\b|\byq\b)"
)


class Finding:
    def __init__(self, check_id: str, severity: str, message: str):
        self.check_id = check_id
        self.severity = severity
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
            "3.1": "Long inline code blocks bloat the skill with tokens the agent must read on every call; moving them to scripts saves tokens and enables caching.",
            "3.2": "Large lookup tables and reference data inflate the context window; moving them to reference files allows lazy on-demand loading.",
            "3.3": "Explaining standard tool usage wastes tokens on knowledge the agent already has, adding noise without value.",
            "3.4": "Duplicated content doubles the token cost for the same information and risks inconsistency when one copy is updated.",
            "3.5": "Verbose prose that could be a single sentence wastes tokens and buries the actual instruction.",
            "3.6": "Preloading all reference files defeats lazy loading, consuming tokens for data that may never be needed in a given invocation.",
            "3.7": "MCP tool definitions add thousands of overhead tokens per API call; CLI alternatives cost a fraction and keep the skill portable.",
            "4.1": "Ambiguous instructions force the agent to guess intent, leading to inconsistent or wrong behavior across runs.",
            "4.2": "Without concrete input/output examples the agent must infer the expected format, increasing the chance of malformed output.",
            "4.3": "Negative-only instructions tell the agent what to avoid but not what to do, leaving correct behavior undefined.",
            "4.4": "Missing default behavior for optional flags means the agent has no defined action when the flag is omitted.",
            "4.5": "Non-idempotent operations fail or produce duplicates when retried, and agents commonly retry on transient errors.",
            "4.6": "Without success criteria the agent cannot determine when the task is complete, risking premature exit or infinite loops.",
            "4.7": "Uniform exit codes (0/1 only) prevent the agent from distinguishing failure types and choosing the right recovery strategy.",
        }
        return reason_map.get(self.check_id, "This issue reduces portability, clarity, or reliability of the skill definition.")

    def colored(self) -> str:
        if self.severity == "ERROR":
            return f"🔴 {BOLD}{self.check_id}{RESET}: {self.message}"
        return f"🟡 {BOLD}{self.check_id}{RESET}: {self.message}"


def emit_progress(message: str) -> None:
    if os.environ.get("EVAL_PROGRESS_STDERR") == "1":
        print(message, file=sys.stderr, flush=True)


def print_separator(label: str) -> None:
    pad = BOX_WIDTH - len(label) - 4
    left = pad // 2
    right = pad - left
    print(f"{BLUE}{BOLD}{'─' * left}┤ {label} ├{'─' * right}{RESET}")


def print_box_top(title: str) -> None:
    inner = BOX_WIDTH - 2
    print(f"{BLUE}┌{'─' * inner}┐{RESET}")
    pad = inner - len(title)
    print(f"{BLUE}│{RESET} {BOLD}{title}{RESET}{' ' * (pad - 1)}{BLUE}│{RESET}")
    print(f"{BLUE}├{'─' * inner}┤{RESET}")


def print_box_line(text: str) -> None:
    plain = re.sub(r"\033\[[0-9;]*m", "", text)
    pad = BOX_WIDTH - 2 - len(plain)
    if pad < 0:
        pad = 0
    print(f"{BLUE}│{RESET} {text}{' ' * (pad - 1)}{BLUE}│{RESET}")


def print_box_bottom() -> None:
    inner = BOX_WIDTH - 2
    print(f"{BLUE}└{'─' * inner}┘{RESET}")


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def parse_frontmatter(text: str) -> tuple[dict | None, str]:
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
