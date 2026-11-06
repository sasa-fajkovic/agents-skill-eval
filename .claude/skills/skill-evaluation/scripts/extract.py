from __future__ import annotations

import re
import os
import stat
from io import BytesIO
from pathlib import Path

from common import (
    PREFERRED_SCRIPT_EXTENSIONS,
    ALLOWED_TEXT_EXTENSIONS,
    PORTABILITY_SCRIPT_EXTENSIONS,
    IMAGE_EXTENSIONS,
    MAX_FILE_EXCERPT_CHARS,
    MAX_SUPPORTING_CONTEXT_CHARS,
    PDF_EXTENSIONS,
    PdfReader,
    parse_frontmatter,
    read_text_file as read_primary_text_file,
)
from output_evals import _directory, declared_output_inputs, fixture_file_identity, inspect_output_evals, read_bounded_regular_file, validate_fixture_path


def read_text_file(path: Path) -> str:
    try:
        return read_bounded_regular_file(path.parent, path.absolute()).decode("utf-8", errors="replace")
    except ValueError as exc:
        raise OSError("supporting file omitted by evaluator read policy") from exc


def read_pdf_file(path: Path, root: Path | None = None) -> str:
    if PdfReader is None:
        return ""
    reader = PdfReader(BytesIO(read_bounded_regular_file(root or path.parent, path.absolute())))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n".join(pages)


def collect_supporting_context(skill_path: Path) -> str:
    from trigger_evals import MAX_QUERY_FILE_BYTES, QUERY_FILENAMES

    root = skill_path.parent
    output_inputs, output_identities = declared_output_inputs(root)
    paths = [path for path in root.rglob("*") if path.is_file() and path != skill_path]
    inventory = []
    parts = []
    used_chars = 0

    def sort_key(path: Path) -> tuple[int, str]:
        relative = str(path.relative_to(root)).lower()
        priority = 3
        if "/references/" in f"/{relative}" or relative.startswith("references/"):
            priority = 0
        elif relative.endswith("license.txt") or relative.endswith("readme.md") or relative.endswith("readme.txt"):
            priority = 1
        elif "/scripts/" in f"/{relative}" or relative.startswith("scripts/"):
            priority = 2
        return (priority, relative)

    for path in sorted(paths, key=sort_key):
        suffix = path.suffix.lower()
        relative = path.relative_to(root)
        trigger_fixture = path.name in QUERY_FILENAMES and path.parent in (root, root / "evals")
        if path.absolute() in output_inputs or (not trigger_fixture and relative.parts[0] == "evals"):
            continue
        if output_identities:
            try:
                if fixture_file_identity(root, relative.as_posix()) in output_identities:
                    continue
            except (OSError, ValueError, UnicodeError, NotImplementedError):
                pass
        inventory.append(str(relative))
        try:
            if trigger_fixture:
                content = read_bounded_regular_file(root, path.absolute(), MAX_QUERY_FILE_BYTES).decode("utf-8")
            elif suffix in ALLOWED_TEXT_EXTENSIONS:
                content = read_bounded_regular_file(root, path.absolute()).decode("utf-8", errors="replace")
            elif suffix in PDF_EXTENSIONS:
                content = read_pdf_file(path, root)
            elif suffix in IMAGE_EXTENSIONS:
                validate_fixture_path(root, relative.as_posix())
                content = "[image file omitted from text analysis]"
            else:
                continue
        except (OSError, ValueError, UnicodeError):
            content = "[Supporting file content omitted by evaluator read policy]"

        stripped = content.strip()
        if not stripped:
            continue

        remaining = MAX_SUPPORTING_CONTEXT_CHARS - used_chars
        if remaining <= 0:
            break

        excerpt = stripped[: min(MAX_FILE_EXCERPT_CHARS, remaining)]
        if len(stripped) > len(excerpt):
            excerpt += "\n[truncated]"
        block = f"--- FILE: {relative} ---\n{excerpt}"
        parts.append(block)
        used_chars += len(block) + 2

    summary = ["Supporting file inventory:"]
    summary.extend(f"- {item}" for item in inventory)
    if parts:
        summary.append("")
        summary.append("Supporting file excerpts:")
        summary.append("")
        summary.append("\n\n".join(parts))
    return "\n".join(summary)


def extract_frontmatter_string_field(fm: dict | None, field_name: str) -> str:
    if not isinstance(fm, dict):
        return ""
    value = fm.get(field_name)
    if isinstance(value, str):
        return value.strip()
    return ""


def extract_skill_name(skill_path: Path, fm: dict | None) -> str:
    name = extract_frontmatter_string_field(fm, "name")
    if name:
        return name
    return skill_path.parent.name if skill_path.name == "SKILL.md" else skill_path.stem


def extract_skill_description(skill_text: str, fm: dict | None) -> str:
    description = extract_frontmatter_string_field(fm, "description")
    if description:
        return description

    lines = skill_text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().lower() in {"## description", "# description"}:
            collected = []
            for candidate in lines[index + 1:]:
                stripped = candidate.strip()
                if not stripped:
                    collected.append("")
                    continue
                if stripped.startswith("#"):
                    break
                collected.append(stripped)
            while collected and not collected[0]:
                collected.pop(0)
            while collected and not collected[-1]:
                collected.pop()
            if collected:
                paragraphs = []
                current = []
                for item in collected:
                    if item:
                        current.append(item)
                        continue
                    if current:
                        paragraphs.append(" ".join(current))
                        current = []
                if current:
                    paragraphs.append(" ".join(current))
                if paragraphs:
                    return "\n\n".join(paragraphs)

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def extract_skill_compatibility(fm: dict | None) -> str:
    return extract_frontmatter_string_field(fm, "compatibility")


def fenced_code_blocks(body: str) -> list[tuple[int, int, str, str]]:
    lines = body.splitlines()
    blocks = []
    in_fence = False
    start = 0
    lang = ""
    collected: list[str] = []
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                in_fence = True
                start = index
                lang = stripped[3:].strip().lower()
                collected = []
            else:
                blocks.append((start, index, lang, "\n".join(collected)))
                in_fence = False
                lang = ""
                collected = []
            continue
        if in_fence:
            collected.append(line)
    return blocks


def heading_ranges(body: str) -> list[tuple[int, int, str, str]]:
    import re

    lines = body.splitlines()
    headings: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines, start=1):
        match = re.match(r"^(#{1,3})\s+(.+?)\s*$", line.strip())
        if match:
            headings.append((index, match.group(1), match.group(2).strip()))
    ranges = []
    for idx, (line_num, hashes, title) in enumerate(headings):
        end_line = len(lines)
        for next_line, next_hashes, _next_title in headings[idx + 1:]:
            if len(next_hashes) <= len(hashes):
                end_line = next_line - 1
                break
        ranges.append((line_num, end_line, hashes, title))
    return ranges


def extract_output_section(body: str) -> tuple[int, str]:
    lines = body.splitlines()
    for start, end, _hashes, title in heading_ranges(body):
        if title.lower() == "output":
            return start, "\n".join(lines[start:end])
    return 0, ""


def has_nearby_example(lines: list[str], index: int, window: int = 20) -> bool:
    import re

    start = max(0, index - window)
    end = min(len(lines), index + window)
    context = "\n".join(lines[start:end])
    return bool(re.search(r"(?i)\bexample\b|```", context))


def normalize_text_block(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


_NUMBERED_LIST_RE = re.compile(r"^\d+\.\s")


def body_paragraphs(body: str) -> list[tuple[int, str]]:
    paragraphs = []
    current: list[str] = []
    start_line = 1
    lines = body.splitlines()
    in_fence = False
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            if current:
                paragraphs.append((start_line, " ".join(current)))
                current = []
            continue
        # Skip content inside fenced code blocks
        if in_fence:
            continue
        if not stripped:
            if current:
                paragraphs.append((start_line, " ".join(current)))
                current = []
            continue
        if not current:
            start_line = index
        if stripped.startswith("#") or stripped.startswith("-") or stripped.startswith("*") or _NUMBERED_LIST_RE.match(stripped):
            if current:
                paragraphs.append((start_line, " ".join(current)))
                current = []
            continue
        current.append(stripped)
    if current:
        paragraphs.append((start_line, " ".join(current)))
    return paragraphs


def script_inventory(skill_dir: str) -> tuple[list[tuple[Path, str]], list[str]]:
    root = Path(skill_dir)
    results = []
    issues = []
    try:
        with _directory(root) as descriptor:
            with os.scandir(descriptor) as entries:
                names = sorted(entry.name for entry in entries)
            for name in names:
                path = root / name
                if path.suffix in PREFERRED_SCRIPT_EXTENSIONS:
                    if not stat.S_ISDIR(os.stat(name, dir_fd=descriptor, follow_symlinks=False).st_mode):
                        results.append((path, name))
            if "scripts" in names:
                try:
                    with _directory(root, ("scripts",)) as scripts_descriptor:
                        with os.scandir(scripts_descriptor) as entries:
                            for entry in sorted(entries, key=lambda item: item.name):
                                if not entry.is_dir(follow_symlinks=False):
                                    results.append((root / "scripts" / entry.name, f"scripts/{entry.name}"))
                except (OSError, ValueError, UnicodeError, NotImplementedError):
                    issues.append("scripts/")
    except (OSError, ValueError, UnicodeError, NotImplementedError):
        issues.append("skill root")
    return results, issues


def scripts_under(skill_dir: str) -> list[tuple[Path, str]]:
    results = []
    candidates, _issues = script_inventory(skill_dir)
    for file_path, display in candidates:
        if file_path.suffix not in PREFERRED_SCRIPT_EXTENSIONS:
            continue
        try:
            validate_fixture_path(Path(skill_dir), display)
        except ValueError:
            continue
        results.append((file_path, display))
    return results


def script_analysis_issues(skill_dir: str) -> list[str]:
    candidates, issues = script_inventory(skill_dir)
    for file_path, display in candidates:
        if file_path.suffix not in PREFERRED_SCRIPT_EXTENSIONS or _is_library_or_test_file(file_path):
            continue
        try:
            read_bounded_regular_file(Path(skill_dir), file_path.absolute())
        except (OSError, ValueError, UnicodeError, NotImplementedError):
            issues.append(display)
    return issues


_TEST_FILE_RE = re.compile(r"^(test_|_).*|.*_test\.(py|sh)$|.*\.bats$", re.IGNORECASE)


def _is_library_or_test_file(file_path: Path) -> bool:
    """Return True for files that are not standalone entrypoints.

    Library files (``_common.sh``, ``_format.py``) and test files
    (``test_foo.py``, ``foo_test.py``, ``foo.bats``) should not be
    required to implement ``--help`` or other entrypoint conventions.
    """
    return bool(_TEST_FILE_RE.match(file_path.name))


def entrypoint_scripts(skill_dir: str) -> list[tuple[Path, str]]:
    entrypoints = []
    for file_path, display in scripts_under(skill_dir):
        if _is_library_or_test_file(file_path):
            continue
        try:
            content = read_text_file(file_path)
        except (OSError, UnicodeDecodeError):
            continue
        if file_path.suffix == ".sh":
            entrypoints.append((file_path, display))
            continue
        if file_path.suffix == ".py" and ("if __name__ == \"__main__\"" in content or "if __name__ == '__main__'" in content):
            entrypoints.append((file_path, display))
    return entrypoints


def collect_metadata(skill_path: Path) -> dict:
    skill_dir = skill_path.parent
    files = [path for path in skill_dir.rglob("*") if path.is_file()]
    script_types = sorted({path.suffix for path in files if path.suffix in PREFERRED_SCRIPT_EXTENSIONS})
    unsupported_script_types = sorted({path.suffix for path in files if path.suffix in PORTABILITY_SCRIPT_EXTENSIONS})
    skill_text = read_primary_text_file(skill_path) if skill_path.exists() else ""
    fm, _ = parse_frontmatter(skill_text)
    skill_name = fm.get("name") if isinstance(fm, dict) and isinstance(fm.get("name"), str) else None
    output_evals, _ = inspect_output_evals(skill_dir, skill_name)
    from trigger_evals import inspect_trigger_evals

    trigger_evals, _findings = inspect_trigger_evals(skill_dir)
    return {
        "trigger_evals": trigger_evals,
        "file_count": len(files),
        "line_count": len(skill_text.splitlines()),
        "has_scripts": any(path.suffix in PREFERRED_SCRIPT_EXTENSIONS or path.suffix in PORTABILITY_SCRIPT_EXTENSIONS for path in files),
        "script_types": script_types,
        "unsupported_script_types": unsupported_script_types,
        "output_evals": output_evals,
    }


def main() -> None:
    import json
    import sys

    if "--help" in sys.argv or "-h" in sys.argv:
        print("Usage: extract.py --help")
        print()
        print("Internal extraction helpers for the skill evaluator.")
        print()
        print("Exit codes:")
        print("  0  Help shown successfully")
        print("  2  Module invoked directly (use eval.py instead)")
        sys.exit(0)

    json.dump({"error": "extract.py is an internal helper module. Use eval.py instead."}, sys.stdout)
    print()
    sys.exit(2)


if __name__ == "__main__":
    main()
