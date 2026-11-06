from __future__ import annotations

import json
from pathlib import Path

from common import Finding


QUERY_FILENAMES = ("eval_queries.json", "train_queries.json", "validation_queries.json")
MAX_QUERY_FILE_BYTES = 1_000_000


def inspect_trigger_evals(skill_dir: Path) -> tuple[dict, list[Finding]]:
    files = []
    findings = []
    for directory in (skill_dir, skill_dir / "evals"):
        for filename in QUERY_FILENAMES:
            path = directory / filename
            if not path.exists() and not path.is_symlink():
                continue
            relative = path.relative_to(skill_dir).as_posix()
            entry = {
                "path": relative,
                "valid": False,
                "query_count": 0,
                "should_trigger_count": 0,
                "should_not_trigger_count": 0,
            }
            files.append(entry)
            try:
                if path.is_symlink() or directory.is_symlink():
                    raise ValueError("symlinked query fixtures are not read by evaluator policy")
                if not path.is_file():
                    raise ValueError("expected a regular query file")
                if path.stat().st_size > MAX_QUERY_FILE_BYTES:
                    raise ValueError("exceeds evaluator's 1 MB query-file limit")
                queries = json.loads(path.read_text(encoding="utf-8"))
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
                findings.append(Finding("4.9", "WARN", f"optional evaluator policy: {relative}: {detail}; trigger accuracy was not measured"))
                continue
            entry.update(
                valid=True,
                query_count=len(queries),
                should_trigger_count=sum(query["should_trigger"] for query in queries),
                should_not_trigger_count=sum(not query["should_trigger"] for query in queries),
            )
    return {"measurement": "not_run", "files": files}, findings
