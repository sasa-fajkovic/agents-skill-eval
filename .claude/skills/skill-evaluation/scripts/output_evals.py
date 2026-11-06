from __future__ import annotations

import json
import math
import os
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from common import Finding


MANIFEST_PATH = "evals/evals.json"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_JSON_DEPTH = 64
INVALID_MESSAGE = "Optional output-quality evals are invalid or exceed safe local fixture limits."
INVALID_FIXTURE_MESSAGE = "Invalid or unsafe local fixture."


def _relative_parts(relative: str) -> tuple[str, ...]:
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError(INVALID_FIXTURE_MESSAGE)
    if "\\" in relative or "\x00" in relative or ":" in relative:
        raise ValueError(INVALID_FIXTURE_MESSAGE)
    parts = tuple(relative.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(INVALID_FIXTURE_MESSAGE)
    return parts


def _check_regular(info: os.stat_result, max_bytes: int) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
        raise ValueError(INVALID_FIXTURE_MESSAGE)


@contextmanager
def _directory(root: Path, parts: tuple[str, ...] = ()) -> Iterator[int]:
    if not all(hasattr(os, flag) for flag in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")):
        raise ValueError(INVALID_FIXTURE_MESSAGE)
    descriptors = []
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory = os.open(root.absolute(), directory_flags)
        descriptors.append(directory)
        for part in parts:
            directory = os.open(part, directory_flags, dir_fd=directory)
            descriptors.append(directory)
        yield directory
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


@contextmanager
def _regular_file(root: Path, relative: str, max_bytes: int) -> Iterator[int]:
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError(INVALID_FIXTURE_MESSAGE)
    parts = _relative_parts(relative)
    with _directory(root, parts[:-1]) as directory:
        _check_regular(os.stat(parts[-1], dir_fd=directory, follow_symlinks=False), max_bytes)
        descriptor = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory,
        )
        try:
            _check_regular(os.fstat(descriptor), max_bytes)
            yield descriptor
        finally:
            os.close(descriptor)


def validate_fixture_path(
    root: Path, relative: str, max_bytes: int = MAX_INPUT_BYTES,
) -> Path:
    """Check a root-relative POSIX path without reading its input content."""
    try:
        with _regular_file(root, relative, max_bytes):
            return root / relative
    except (OSError, ValueError, UnicodeError, NotImplementedError):
        raise ValueError(INVALID_FIXTURE_MESSAGE) from None


def _read_relative(root: Path, relative: str, max_bytes: int) -> bytes:
    with _regular_file(root, relative, max_bytes) as descriptor:
        chunks = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        _check_regular(os.fstat(descriptor), max_bytes)
        if len(data) > max_bytes:
            raise ValueError(INVALID_FIXTURE_MESSAGE)
        return data


def read_bounded_regular_file(
    root: Path, path: Path, max_bytes: int = MAX_INPUT_BYTES,
) -> bytes:
    """Read at most max_bytes from a regular file beneath root without symlinks."""
    try:
        candidate = Path(path)
        if candidate.is_absolute():
            relative = candidate.relative_to(root.absolute()).as_posix()
        elif not root.is_absolute() and candidate.is_relative_to(root):
            relative = candidate.relative_to(root).as_posix()
        else:
            relative = candidate.as_posix()
        return _read_relative(root, relative, max_bytes)
    except (OSError, ValueError, UnicodeError, NotImplementedError):
        raise ValueError(INVALID_FIXTURE_MESSAGE) from None


def _check_json_depth(text: str) -> None:
    depth = 0
    quoted = False
    escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError(INVALID_FIXTURE_MESSAGE)
        elif char in "]}":
            depth -= 1


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(INVALID_FIXTURE_MESSAGE)
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError(INVALID_FIXTURE_MESSAGE)


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(INVALID_FIXTURE_MESSAGE)
    return result


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _load_manifest(root: Path) -> object:
    text = _read_relative(root, MANIFEST_PATH, MAX_MANIFEST_BYTES).decode("utf-8", errors="strict")
    _check_json_depth(text)
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant, parse_float=_finite_float)


def fixture_file_identity(root: Path, relative: str) -> tuple[int, int]:
    with _regular_file(root, relative, MAX_INPUT_BYTES) as descriptor:
        info = os.fstat(descriptor)
        return info.st_dev, info.st_ino


def declared_output_inputs(skill_dir: Path) -> tuple[set[Path], set[tuple[int, int]]]:
    """Identify input paths for context omission without trusting fixture validity."""
    paths = set()
    identities = set()
    try:
        data = _load_manifest(skill_dir)
        cases = data.get("evals", []) if isinstance(data, dict) else []
        if not isinstance(cases, list):
            return paths, identities
        for case in cases:
            files = case.get("files", []) if isinstance(case, dict) else []
            if not isinstance(files, list):
                continue
            for relative in files:
                try:
                    _relative_parts(relative)
                except ValueError:
                    continue
                paths.add((skill_dir / relative).absolute())
                try:
                    identities.add(fixture_file_identity(skill_dir, relative))
                except (OSError, ValueError, UnicodeError, NotImplementedError):
                    pass
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError, NotImplementedError):
        pass
    return paths, identities


def _manifest_counts(root: Path, data: object, skill_name: str | None) -> tuple[int, int, int]:
    if not isinstance(data, dict) or not _nonempty_string(data.get("skill_name")):
        raise ValueError(INVALID_FIXTURE_MESSAGE)
    if skill_name is not None and data["skill_name"] != skill_name:
        raise ValueError(INVALID_FIXTURE_MESSAGE)
    cases = data.get("evals")
    if not isinstance(cases, list) or not cases:
        raise ValueError(INVALID_FIXTURE_MESSAGE)
    identifiers = set()
    inputs = set()
    assertion_count = 0
    for case in cases:
        if not isinstance(case, dict) or type(case.get("id")) is not int:
            raise ValueError(INVALID_FIXTURE_MESSAGE)
        if case["id"] in identifiers:
            raise ValueError(INVALID_FIXTURE_MESSAGE)
        identifiers.add(case["id"])
        if not all(_nonempty_string(case.get(key)) for key in ("prompt", "expected_output")):
            raise ValueError(INVALID_FIXTURE_MESSAGE)
        for key in ("assertions", "expectations"):
            assertions = case.get(key, [])
            if not isinstance(assertions, list) or not all(_nonempty_string(item) for item in assertions):
                raise ValueError(INVALID_FIXTURE_MESSAGE)
            assertion_count += len(assertions)
        files = case.get("files", [])
        if not isinstance(files, list):
            raise ValueError(INVALID_FIXTURE_MESSAGE)
        for relative in files:
            if isinstance(relative, str) and relative in inputs:
                continue
            validate_fixture_path(root, relative)
            inputs.add(relative)
    return len(cases), assertion_count, len(inputs)


def inspect_output_evals(skill_dir: Path, skill_name: str | None = None) -> tuple[dict, list[Finding]]:
    """Inspect optional declarations; counts do not establish measured output quality.

    Policy limits: 1 MiB strict UTF-8 manifest, JSON depth 64, and 5 MiB per
    regular input file. Input count is unique paths; both check arrays count.
    """
    metadata = {"measurement": "not_run", "files": []}
    entry = {"path": MANIFEST_PATH, "valid": False, "eval_count": 0, "assertion_count": 0, "input_file_count": 0}
    try:
        os.lstat(skill_dir / MANIFEST_PATH)
    except FileNotFoundError:
        return metadata, []
    except (OSError, ValueError, UnicodeError, NotImplementedError):
        metadata["files"].append(entry)
        return metadata, [Finding("4.10", "WARN", INVALID_MESSAGE)]
    try:
        raw = _read_relative(skill_dir, MANIFEST_PATH, MAX_MANIFEST_BYTES)
    except FileNotFoundError:
        return metadata, []
    except (OSError, ValueError, UnicodeError, NotImplementedError):
        metadata["files"].append(entry)
        return metadata, [Finding("4.10", "WARN", INVALID_MESSAGE)]
    metadata["files"].append(entry)
    try:
        text = raw.decode("utf-8", errors="strict")
        _check_json_depth(text)
        data = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant, parse_float=_finite_float)
        eval_count, assertion_count, input_count = _manifest_counts(skill_dir, data, skill_name)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError, NotImplementedError):
        return metadata, [Finding("4.10", "WARN", INVALID_MESSAGE)]
    entry.update(valid=True, eval_count=eval_count, assertion_count=assertion_count, input_file_count=input_count)
    return metadata, []


def main() -> None:
    if "--help" in sys.argv or "-h" in sys.argv:
        print("Usage: output_evals.py --help")
        print("Internal optional output-quality fixture inspector. Use eval.py to evaluate a skill.")
        print("Exit codes: 0 help shown; 2 invoke eval.py instead.")
        return
    print(json.dumps({"error": "output_evals.py is an internal helper module. Use eval.py instead."}))
    sys.exit(2)


if __name__ == "__main__":
    main()
