from __future__ import annotations

import json
from pathlib import Path

from common import Finding


QUERY_FILENAMES = ("eval_queries.json", "train_queries.json", "validation_queries.json")
MAX_QUERY_FILE_BYTES = 1_000_000


def read_trigger_fixture(path: Path) -> str:
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("symlinked query fixtures are not read by evaluator policy")
    if not path.is_file():
        raise ValueError("expected a regular query file")
    if path.stat().st_size > MAX_QUERY_FILE_BYTES:
        raise ValueError("exceeds evaluator's 1 MB query-file limit")
    with path.open("rb") as stream:
        content = stream.read(MAX_QUERY_FILE_BYTES + 1)
    if len(content) > MAX_QUERY_FILE_BYTES:
        raise ValueError("exceeds evaluator's 1 MB query-file limit")
    return content.decode("utf-8")


def inspect_trigger_evals(skill_dir: Path) -> tuple[dict, list[Finding]]:
    files = []
    findings = []
    for directory in (skill_dir, skill_dir / "evals"):
        for filename in QUERY_FILENAMES:
            path = directory / filename
            relative = path.relative_to(skill_dir).as_posix()
            try:
                if not path.exists() and not path.is_symlink():
                    continue
            except OSError:
                location = directory.relative_to(skill_dir).as_posix()
                findings.append(Finding("4.9", "WARN", f"optional evaluator policy: cannot inspect trigger-query fixtures in {location}; fixture presence and trigger accuracy were not measured"))
                break
            entry = {
                "path": relative,
                "valid": False,
                "query_count": 0,
                "should_trigger_count": 0,
                "should_not_trigger_count": 0,
            }
            try:
                queries = json.loads(read_trigger_fixture(path))
                if not isinstance(queries, list) or not queries:
                    raise ValueError("expected a non-empty JSON array")
                invalid = []
                for index, query in enumerate(queries, start=1):
                    if (
                        not isinstance(query, dict)
                        or not isinstance(query.get("query"), str)
                        or not query["query"].strip()
                        or type(query.get("should_trigger")) is not bool
                    ):
                        invalid.append(index)
                if invalid:
                    shown = ", ".join(str(index) for index in invalid[:10])
                    raise ValueError(f"invalid entries at positions {shown}; each needs a non-empty query string and boolean should_trigger")
            except (OSError, UnicodeError, ValueError, RecursionError) as exc:
                detail = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else "unreadable or invalid JSON"
                files.append(entry)
                findings.append(Finding("4.9", "WARN", f"optional evaluator policy: {relative}: {detail}; trigger accuracy was not measured"))
                continue
            files.append(entry)
            entry.update(
                valid=True,
                query_count=len(queries),
                should_trigger_count=sum(query["should_trigger"] for query in queries),
                should_not_trigger_count=sum(not query["should_trigger"] for query in queries),
            )
    return {"measurement": "not_run", "files": files}, findings
