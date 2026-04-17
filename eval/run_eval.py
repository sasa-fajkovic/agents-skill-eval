import json
import os
import re
import sys
import warnings
from pathlib import Path

import anthropic

try:
    from PyPDF2 import PdfReader
except Exception:  # pragma: no cover - defensive import fallback
    PdfReader = None


INPUT_DIR = Path("/input")
MAX_LLM_CHARS = 20000
DEFAULT_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
DEFAULT_MAX_OUTPUT_TOKENS = int(os.environ.get("ANTHROPIC_MAX_TOKENS", "2500"))
SYSTEM_PROMPT = """You are an expert evaluator of Claude agent skills (SKILL.md files).
Analyze the provided skill definition and return ONLY a JSON object with this exact structure:
{
  \"strengths\": [\"...\"],
  \"weaknesses\": [\"...\"],
  \"suggestions\": [\"...\"],
  \"security_flags\": [\"...\"],
  \"quality_tier\": \"good\"
}
quality_tier must be one of: excellent, good, needs_work, poor
security_flags should list any prompt injection risks, overly broad permissions, or unsafe patterns.
Be specific and actionable. No preamble, no markdown, just the JSON."""
QUALITY_SCORES = {
    "excellent": 95,
    "good": 80,
    "needs_work": 60,
    "poor": 35,
}
ALLOWED_TEXT_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".json",
    ".py",
    ".sh",
    ".js",
    ".yaml",
    ".yml",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
PDF_EXTENSIONS = {".pdf"}


def progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def discover_files(root: Path) -> list[Path]:
    if not root.exists():
        raise FileNotFoundError("/input directory not found")
    return sorted(path for path in root.rglob("*") if path.is_file())


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


def collect_supporting_context(paths: list[Path]) -> str:
    parts = []
    for path in paths:
        suffix = path.suffix.lower()
        relative = path.relative_to(INPUT_DIR)
        if suffix in ALLOWED_TEXT_EXTENSIONS:
            content = read_text_file(path)
        elif suffix in PDF_EXTENSIONS:
            content = read_pdf_file(path)
        elif suffix in IMAGE_EXTENSIONS:
            content = "[image file omitted from text analysis]"
        else:
            continue

        if not content.strip():
            continue

        parts.append(f"--- FILE: {relative} ---\n{content.strip()}")
    return "\n\n".join(parts)


def find_primary_skill(paths: list[Path]) -> Path:
    for path in paths:
        if path.name in {"SKILL.md", "skill.md"}:
            return path
    raise FileNotFoundError("No skill.md or SKILL.md found in upload")


def deterministic_checks(skill_content: str, all_files: list[Path]) -> dict:
    issues = []
    lowered = skill_content.lower()

    has_description = "## description" in lowered or "description:" in lowered
    has_trigger = "trigger" in lowered or "when to use" in lowered
    has_examples = "example" in lowered or "```" in skill_content
    has_error_handling = "error" in lowered or "fail" in lowered

    if not has_description:
        issues.append("Missing description section")
    if not has_trigger:
        issues.append("Missing trigger/activation criteria")
    if not has_examples:
        issues.append("No examples provided")
    if not has_error_handling:
        issues.append("No error handling guidance")
    if len(skill_content) < 200:
        issues.append("Skill definition is very short (< 200 chars)")
    if len(skill_content) > 50000:
        issues.append("Skill definition is very long (> 50k chars) - consider splitting")

    return {
        "has_description": has_description,
        "has_trigger_section": has_trigger,
        "has_examples": has_examples,
        "has_error_handling": has_error_handling,
        "file_count": len(all_files),
        "line_count": skill_content.count("\n") + (1 if skill_content else 0),
        "issues": issues,
    }


def extract_skill_name(skill_path: Path, skill_content: str) -> str:
    frontmatter_match = re.match(r"^---\n(.*?)\n---\n", skill_content, re.DOTALL)
    if frontmatter_match:
        for line in frontmatter_match.group(1).splitlines():
            if line.lower().startswith("name:"):
                name = line.split(":", 1)[1].strip().strip('"\'')
                if name:
                    return name

    for line in skill_content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading

    return skill_path.stem


def summarize_issues(deterministic: dict, llm_analysis: dict) -> str:
    if deterministic["issues"]:
        primary_issue = deterministic["issues"][0]
        return f"Skill evaluated with {len(deterministic['issues'])} deterministic issue(s). Primary issue: {primary_issue}."

    strengths = llm_analysis.get("strengths") or []
    if strengths:
        return f"Skill evaluated successfully. Key strength: {strengths[0]}"
    return "Skill evaluated successfully with no deterministic issues detected."


def compute_overall_score(deterministic: dict, llm_analysis: dict) -> int:
    tier = llm_analysis.get("quality_tier", "needs_work")
    score = QUALITY_SCORES.get(tier, QUALITY_SCORES["needs_work"])
    score -= min(len(deterministic["issues"]) * 5, 30)
    return max(0, min(100, score))


def extract_message_text(response: anthropic.types.message.Message) -> str:
    text_parts = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text_parts.append(block.text)
    if not text_parts:
        raise ValueError("Anthropic response did not contain text content")
    return "\n".join(text_parts).strip()


def parse_llm_json(raw_text: str) -> dict:
    text = raw_text.strip()
    if not text:
        raise ValueError("LLM returned empty output")

    fenced_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced_match:
        text = fenced_match.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError("LLM returned invalid JSON")


def run_llm_analysis(skill_content: str, supporting_context: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    combined_content = skill_content.strip()
    if supporting_context.strip():
        combined_content = f"{combined_content}\n\nSupporting files:\n\n{supporting_context.strip()}"
    if len(combined_content) > MAX_LLM_CHARS:
        combined_content = combined_content[:MAX_LLM_CHARS]

    progress("LLM evaluation...")
    client = anthropic.Anthropic(api_key=api_key)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        response = client.messages.create(
            model=DEFAULT_ANTHROPIC_MODEL,
            max_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Evaluate this skill:\n\n{combined_content}"}],
        )
    llm_result = parse_llm_json(extract_message_text(response))

    return {
        "strengths": list(llm_result.get("strengths") or []),
        "weaknesses": list(llm_result.get("weaknesses") or []),
        "suggestions": list(llm_result.get("suggestions") or []),
        "security_flags": list(llm_result.get("security_flags") or []),
        "quality_tier": llm_result.get("quality_tier", "needs_work"),
    }


def run_evaluation() -> dict:
    progress("Scanning uploaded files...")
    all_files = discover_files(INPUT_DIR)
    progress(f"Discovered {len(all_files)} file(s).")

    progress("Locating primary skill file...")
    skill_path = find_primary_skill(all_files)

    progress("Reading skill content...")
    skill_content = read_text_file(skill_path)

    progress("Running deterministic checks...")
    deterministic = deterministic_checks(skill_content, all_files)

    progress("Collecting supporting context...")
    supporting_context = collect_supporting_context([path for path in all_files if path != skill_path])

    llm_analysis = run_llm_analysis(skill_content, supporting_context)

    progress("Preparing final evaluation result...")
    summary = summarize_issues(deterministic, llm_analysis)

    return {
        "status": "ok",
        "skill_name": extract_skill_name(skill_path, skill_content),
        "overall_score": compute_overall_score(deterministic, llm_analysis),
        "summary": summary,
        "deterministic": deterministic,
        "llm_analysis": llm_analysis,
    }


def main() -> None:
    try:
        result = run_evaluation()
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
