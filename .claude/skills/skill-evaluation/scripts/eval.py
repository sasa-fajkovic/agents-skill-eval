#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import (
    ALL_CHECK_IDS,
    BLUE,
    BOLD,
    BOX_WIDTH,
    DIM,
    GREEN,
    RED,
    RESET,
    YELLOW,
    Finding,
    diagnose_frontmatter_failure,
    emit_progress,
    parse_frontmatter,
    print_box_bottom,
    print_box_line,
    print_box_top,
    print_separator,
    read_text_file,
)
from extract import (
    collect_metadata,
    collect_supporting_context,
    extract_skill_name,
)
from scoring import apply_score_caps, deterministic_score, tier_for_score
from tier1 import check_1_6, run_tier1_checks
from tier2 import check_2_1, check_2_2, run_tier2_checks
from tier3 import run_tier3_checks
from tier4 import run_tier4_checks


def overall_score_for(findings: list[Finding]) -> int:
    errors = sum(1 for finding in findings if finding.severity == "ERROR")
    warnings = sum(1 for finding in findings if finding.severity == "WARN")
    score = deterministic_score(errors, warnings)
    error_ids = [f.check_id for f in findings if f.severity == "ERROR"]
    return apply_score_caps(score, error_ids)


def quality_tier_for(score: int) -> str:
    return tier_for_score(score)


def summary_for(skill_name: str, findings: list[Finding]) -> str:
    errors = [finding for finding in findings if finding.severity == "ERROR"]
    warnings = [finding for finding in findings if finding.severity == "WARN"]
    if errors:
        return f"{skill_name} has {len(errors)} blocking issue(s); the primary problem is {errors[0].message}."
    if warnings:
        return f"{skill_name} is mostly portable, but it has {len(warnings)} warning(s), led by {warnings[0].message}."
    return f"{skill_name} is portable, well-structured, and passes the deterministic evaluator."


def finding_payload(finding: Finding) -> dict:
    """Produce a JSON-serializable dict for a single finding (no severity — caller groups by severity)."""
    return {
        "rule_id": finding.check_id,
        "message": finding.message,
        "reason": finding.reason,
    }


def build_json_result(skill_path: Path, findings: list[Finding]) -> dict:
    skill_text = read_text_file(skill_path)
    fm, _body = parse_frontmatter(skill_text)
    skill_name = extract_skill_name(skill_path, fm)
    metadata = collect_metadata(skill_path)
    errors = [finding for finding in findings if finding.severity == "ERROR"]
    warnings = [finding for finding in findings if finding.severity == "WARN"]
    overall_score = overall_score_for(findings)
    overall_tier = quality_tier_for(overall_score)
    summary = summary_for(skill_name, findings)

    return {
        "schema_version": "1.0",
        "status": "ok",
        "skill_name": skill_name,
        "skill_content": skill_text,
        "supporting_context": collect_supporting_context(skill_path),
        "overall_score": overall_score,
        "overall_tier": overall_tier,
        "summary": summary,
        "checks_overview": {
            "checks_total": len(ALL_CHECK_IDS),
            "checks_passed": max(0, len(ALL_CHECK_IDS) - len(findings)),
            "checks_failed": len(findings),
        },
        "findings": {
            "total": len(findings),
            "error_findings": [finding_payload(f) for f in errors],
            "warning_findings": [finding_payload(f) for f in warnings],
        },
        "metadata": metadata,
    }


def _find_skill_md(root: Path) -> Path | None:
    """Locate SKILL.md inside *root*, preferring root-level, then one level deep."""
    candidate = root / "SKILL.md"
    if candidate.exists():
        return candidate
    # Walk one level deep (e.g. copilot-review/SKILL.md inside an upload dir).
    for child in sorted(root.iterdir()):
        if child.is_dir():
            nested = child / "SKILL.md"
            if nested.exists():
                return nested
    return None


def evaluate(path: str) -> list[Finding]:
    skill_path = Path(path)
    if skill_path.is_dir():
        found = _find_skill_md(skill_path)
        if found is not None:
            skill_path = found
        else:
            skill_path = skill_path / "SKILL.md"  # will fail below with a clear message
    if not skill_path.exists():
        return [Finding("--", "ERROR", f"file not found: {skill_path}")]

    emit_progress("Locating primary skill file...")
    emit_progress("Reading skill content...")
    text = read_text_file(skill_path)
    lines = text.splitlines()
    skill_dir = str(skill_path.parent)
    fm, body = parse_frontmatter(text)

    emit_progress("Running deterministic checks...")
    findings: list[Finding] = []
    if fm is None:
        diag = diagnose_frontmatter_failure(text)
        findings.append(Finding("1.1", "ERROR", diag))
        findings.extend(check_1_6(lines))
        findings.extend(check_2_1(body))
        findings.extend(check_2_2(body))
        return findings

    findings.extend(run_tier1_checks(fm, lines, skill_dir))
    findings.extend(run_tier2_checks(body, skill_dir))
    findings.extend(run_tier3_checks(body, skill_dir))
    findings.extend(run_tier4_checks(body, skill_dir))
    emit_progress("Collecting supporting context...")
    _ = collect_supporting_context(skill_path)
    return findings


def render_human_output(resolved: Path, findings: list[Finding]) -> None:
    errors = [finding for finding in findings if finding.severity == "ERROR"]
    warnings = [finding for finding in findings if finding.severity == "WARN"]
    skill_name = resolved.parent.name if resolved.name == "SKILL.md" else resolved.stem
    line_count = len(read_text_file(resolved).splitlines()) if resolved.exists() else 0

    print()
    print(f"  {BOLD}Skill: {skill_name}{RESET}")
    print(f"  Baseline: {line_count} lines")
    print()
    print_separator("DETERMINISTIC EVALUATION")
    print()

    for prefix, title in (("1.", "Tier 1 — Spec Compliance (1.1-1.11)"), ("2.", "Tier 2 — Security (2.1-2.2, 2.4)"), ("3.", "Tier 3 — Token Efficiency (3.1-3.7)"), ("4.", "Tier 4 — Effectiveness (4.1-4.7)")):
        tier_findings = [finding for finding in findings if finding.check_id.startswith(prefix)]
        print_box_top(title)
        if tier_findings:
            for finding in tier_findings:
                icon = "🔴" if finding.severity == "ERROR" else "🟡"
                print_box_line(f"{icon} {BOLD}{finding.check_id}{RESET}: {finding.message[:40]}")
        else:
            print_box_line("🟢 All checks passed")
        print_box_bottom()
        print()

    print_separator("DETERMINISTIC RESULTS")
    print()
    result = "FAIL" if errors else ("WARN" if warnings else "PASS")
    if errors:
        print(f"  {RED}{BOLD}Errors ({len(errors)}){RESET}")
        for finding in errors:
            print(f"    {finding.colored()}")
        print()
    if warnings:
        print(f"  {YELLOW}{BOLD}Warnings ({len(warnings)}){RESET}")
        for finding in warnings:
            print(f"    {finding.colored()}")
        print()
    if not errors and not warnings:
        print("  🟢 No issues found.")
        print()

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
    banner_text = f"Deterministic Result: {label}"
    pad = inner - len(banner_text)
    left = pad // 2
    right = pad - left
    print(f"  {color}{BOLD}{'━' * BOX_WIDTH}{RESET}")
    print(f"  {color}{BOLD}{' ' * left}{banner_text}{' ' * right}{RESET}")
    print(f"  {color}{BOLD}{'━' * BOX_WIDTH}{RESET}")
    print()


def main() -> None:
    if "--help" in sys.argv or "-h" in sys.argv:
        print("Usage: eval.py <path-to-SKILL.md|skill-dir> [--ci]")
        print()
        print("Deterministic skill evaluator.")
        print("Validates SKILL.md against agentskills.io spec, security, token-efficiency, and heuristic effectiveness checks.")
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
    resolved = Path(path) / "SKILL.md" if Path(path).is_dir() else Path(path)
    findings = evaluate(path)
    errors = [finding for finding in findings if finding.severity == "ERROR"]
    warnings = [finding for finding in findings if finding.severity == "WARN"]

    if ci_mode:
        print(json.dumps(build_json_result(resolved, findings), indent=None, separators=(",", ":")))
    else:
        render_human_output(resolved, findings)

    sys.exit(1 if errors else (2 if warnings else 0))


if __name__ == "__main__":
    main()
