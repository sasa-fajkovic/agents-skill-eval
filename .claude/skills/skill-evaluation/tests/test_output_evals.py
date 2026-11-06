from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import output_evals


class OutputEvalsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "csv-analyzer"
        self.root.mkdir()
        self.manifest = self.root / "evals/evals.json"
        self.base = {
            "skill_name": "csv-analyzer",
            "evals": [{"id": 1, "prompt": "Summarize the CSV.", "expected_output": "A revenue summary."}],
        }

    def write_manifest(self, data: object) -> None:
        self.manifest.parent.mkdir(exist_ok=True)
        self.manifest.write_text(json.dumps(data), encoding="utf-8")

    def assert_invalid(self, name: str | None = "csv-analyzer") -> None:
        metadata, findings = output_evals.inspect_output_evals(self.root, name)
        self.assertEqual(metadata, {
            "measurement": "not_run",
            "files": [{"path": "evals/evals.json", "valid": False, "eval_count": 0,
                       "assertion_count": 0, "input_file_count": 0}],
        })
        self.assertEqual(len(findings), 1)
        self.assertEqual((findings[0].check_id, findings[0].severity, findings[0].message),
                         ("4.10", "WARN", output_evals.INVALID_MESSAGE))

    def test_absence_is_neutral_with_or_without_evals_directory(self) -> None:
        for _ in range(2):
            self.assertEqual(output_evals.inspect_output_evals(self.root),
                             ({"measurement": "not_run", "files": []}, []))
            self.manifest.parent.mkdir(exist_ok=True)

    def test_absence_is_neutral_before_root_or_platform_safe_access_checks(self) -> None:
        alias = self.root.parent / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        for root in (self.root, alias):
            with self.subTest(root=root), mock.patch.dict(output_evals.os.__dict__):
                del output_evals.os.O_NOFOLLOW
                self.assertEqual(output_evals.inspect_output_evals(root),
                                 ({"measurement": "not_run", "files": []}, []))

    def test_unknown_manifest_presence_warns_and_dangling_manifest_is_supplied(self) -> None:
        with mock.patch.object(output_evals.os, "lstat", side_effect=PermissionError("PRIVATE")):
            self.assert_invalid()
        self.manifest.parent.mkdir()
        self.manifest.symlink_to(self.root / "missing.json")
        self.assert_invalid()

    def test_unreadable_manifest_parent_warns_instead_of_claiming_absence(self) -> None:
        self.manifest.parent.mkdir()
        self.manifest.parent.chmod(0)
        self.addCleanup(self.manifest.parent.chmod, 0o700)
        try:
            os.lstat(self.manifest)
        except PermissionError:
            self.assert_invalid()
        else:
            self.skipTest("filesystem permissions do not restrict this process")

    def test_guide_csv_example_and_unique_input_counts_without_reading_inputs(self) -> None:
        data = {
            "skill_name": "csv-analyzer",
            "evals": [
                {"id": 1, "prompt": "Find the top 3 months by revenue and make a bar chart.",
                 "expected_output": "A bar chart with labeled axes and values.",
                 "files": ["evals/files/sales_2025.csv", "evals/files/sales_2025.csv"],
                 "assertions": ["The chart shows exactly 3 months", "Both axes are labeled"]},
                {"id": 2, "prompt": "Clean customers.csv and count missing emails.",
                 "expected_output": "A cleaned CSV and a missing-email count.",
                 "files": ["evals/files/customers.csv"], "expectations": ["The count is shown"]},
            ],
        }
        files = self.root / "evals/files"
        files.mkdir(parents=True)
        for name in ("sales_2025.csv", "customers.csv"):
            (files / name).write_bytes(b"\xff\x00not-decoded-or-executed")
        self.write_manifest(data)
        with mock.patch.object(output_evals.os, "read", wraps=os.read) as read, \
             mock.patch.object(output_evals, "validate_fixture_path", wraps=output_evals.validate_fixture_path) as validate:
            metadata, findings = output_evals.inspect_output_evals(self.root, "csv-analyzer")
        self.assertEqual(findings, [])
        self.assertEqual(metadata, {
            "measurement": "not_run",
            "files": [{"path": "evals/evals.json", "valid": True, "eval_count": 2,
                       "assertion_count": 3, "input_file_count": 2}],
        })
        self.assertEqual(read.call_count, 2)
        self.assertEqual(validate.call_count, 2)

    def test_optional_fields_empty_arrays_and_unknown_fields_are_compatible(self) -> None:
        for extras in ({}, {"files": [], "assertions": [], "expectations": []},
                       {"future": {"nested": [1, True, None]}}):
            with self.subTest(extras=extras):
                data = copy.deepcopy(self.base)
                data["evals"][0].update(extras)
                data["future"] = "ignored"
                self.write_manifest(data)
                metadata, findings = output_evals.inspect_output_evals(self.root)
                self.assertEqual(findings, [])
                self.assertTrue(metadata["files"][0]["valid"])
                self.assertEqual(metadata["files"][0]["assertion_count"], 0)

    def test_name_is_required_and_matches_only_when_supplied(self) -> None:
        for value in (None, "", " \n", 1, [], {}):
            data = copy.deepcopy(self.base)
            if value is None:
                data.pop("skill_name")
            else:
                data["skill_name"] = value
            self.write_manifest(data)
            self.assert_invalid()
        self.write_manifest(self.base)
        self.assert_invalid("different-name")
        self.assertEqual(output_evals.inspect_output_evals(self.root)[1], [])

    def test_required_structure_and_case_fields(self) -> None:
        for data in (None, [], "text", {}, {"skill_name": "csv-analyzer"},
                     {"skill_name": "csv-analyzer", "evals": []},
                     {"skill_name": "csv-analyzer", "evals": {}},
                     {"skill_name": "csv-analyzer", "evals": [None]}):
            with self.subTest(data=data):
                self.write_manifest(data)
                self.assert_invalid()
        for key in ("id", "prompt", "expected_output"):
            data = copy.deepcopy(self.base)
            data["evals"][0].pop(key)
            self.write_manifest(data)
            self.assert_invalid()
        for key in ("prompt", "expected_output"):
            for value in ("", " \n", None, True, 1, [], {}):
                with self.subTest(key=key, value=value):
                    data = copy.deepcopy(self.base)
                    data["evals"][0][key] = value
                    self.write_manifest(data)
                    self.assert_invalid()

    def test_ids_are_unique_integers_excluding_booleans(self) -> None:
        for value in (None, True, False, 1.0, "1", [], {}):
            with self.subTest(value=value):
                data = copy.deepcopy(self.base)
                data["evals"][0]["id"] = value
                self.write_manifest(data)
                self.assert_invalid()
        data = copy.deepcopy(self.base)
        data["evals"].append(dict(data["evals"][0]))
        self.write_manifest(data)
        self.assert_invalid()
        data["evals"][1]["id"] = -1
        self.write_manifest(data)
        self.assertEqual(output_evals.inspect_output_evals(self.root)[1], [])

    def test_optional_fields_reject_invalid_types_and_blank_checks(self) -> None:
        for key in ("files", "assertions", "expectations"):
            for value in (None, True, "text", {}, [None], [True], [1], [""], [" \n"]):
                with self.subTest(key=key, value=value):
                    data = copy.deepcopy(self.base)
                    data["evals"][0][key] = value
                    self.write_manifest(data)
                    self.assert_invalid()

    def test_unsafe_and_missing_input_paths(self) -> None:
        for relative in ("missing.csv", "/etc/passwd", "../outside", "a/../input.csv", "./input.csv",
                         "a/./input.csv", "a//input.csv", "input.csv/", "a\\input.csv", "C:/input.csv",
                         "https://example.com/input.csv", "input.csv\x00", "//host/input.csv"):
            with self.subTest(relative=relative):
                data = copy.deepcopy(self.base)
                data["evals"][0]["files"] = [relative]
                self.write_manifest(data)
                self.assert_invalid()

    def test_input_directory_fifo_symlink_and_symlink_ancestor_are_rejected(self) -> None:
        directory = self.root / "inputs"
        directory.mkdir()
        outside = Path(self.directory.name) / "outside.csv"
        outside.write_text("sentinel", encoding="utf-8")
        (self.root / "linked.csv").symlink_to(outside)
        (self.root / "linked-dir").symlink_to(outside.parent, target_is_directory=True)
        os.mkfifo(self.root / "pipe")
        for relative in ("inputs", "linked.csv", "linked-dir/outside.csv", "pipe"):
            with self.subTest(relative=relative):
                data = copy.deepcopy(self.base)
                data["evals"][0]["files"] = [relative]
                self.write_manifest(data)
                self.assert_invalid()

    def test_root_and_manifest_symlinks_and_nonregular_manifests_are_rejected(self) -> None:
        self.write_manifest(self.base)
        alias = Path(self.directory.name) / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(output_evals.inspect_output_evals(alias)[0]["files"][0]["valid"], False)
        self.manifest.unlink()
        target = self.root / "target.json"
        target.write_text(json.dumps(self.base), encoding="utf-8")
        self.manifest.symlink_to(target)
        self.assert_invalid()
        self.manifest.unlink()
        self.manifest.mkdir()
        self.assert_invalid()
        self.manifest.rmdir()
        os.mkfifo(self.manifest)
        self.assert_invalid()
        self.manifest.unlink()
        self.manifest.parent.rmdir()
        (self.root / "evals.json").write_text(json.dumps(self.base), encoding="utf-8")
        self.manifest.parent.symlink_to(self.root, target_is_directory=True)
        self.assert_invalid()

    def test_input_size_limit_is_stat_only(self) -> None:
        data = copy.deepcopy(self.base)
        data["evals"][0]["files"] = ["input.csv"]
        self.write_manifest(data)
        path = self.root / "input.csv"
        with path.open("wb") as handle:
            handle.truncate(output_evals.MAX_INPUT_BYTES)
        with mock.patch.object(output_evals.os, "read", side_effect=AssertionError("input read")):
            self.assertEqual(output_evals.validate_fixture_path(self.root, "input.csv"), path)
        self.assertEqual(output_evals.inspect_output_evals(self.root)[1], [])
        with path.open("ab") as handle:
            handle.write(b"x")
        self.assert_invalid()

    def test_declared_input_identities_do_not_read_input_content(self) -> None:
        path = self.root / "private.txt"
        path.write_text("PRIVATE_INPUT", encoding="utf-8")
        self.write_manifest({"evals": [{"files": ["private.txt", "missing.txt"]}]})
        original_read = os.read
        identity = path.stat()

        def read_manifest_only(descriptor, count):
            info = os.fstat(descriptor)
            self.assertNotEqual((info.st_dev, info.st_ino), (identity.st_dev, identity.st_ino))
            return original_read(descriptor, count)

        with mock.patch.object(output_evals.os, "read", side_effect=read_manifest_only):
            paths, identities = output_evals.declared_output_inputs(self.root)
        self.assertEqual(paths, {path.absolute(), (self.root / "missing.txt").absolute()})
        self.assertEqual(identities, {(identity.st_dev, identity.st_ino)})

    def test_manifest_size_utf8_and_json_syntax_are_bounded(self) -> None:
        self.manifest.parent.mkdir()
        for raw in (b" " * (output_evals.MAX_MANIFEST_BYTES + 1), b"\xff", b"{", b"{} {}",
                    b'{"skill_name":"csv-analyzer","evals":[],"skill_name":"csv-analyzer"}',
                    b'{"unknown":NaN}', b'{"unknown":Infinity}', b'{"unknown":-Infinity}',
                    b'{"unknown":1e999}'):
            with self.subTest(raw=raw[:100]):
                self.manifest.write_bytes(raw)
                self.assert_invalid()

    def test_json_depth_is_checked_before_parsing_and_ignores_quoted_brackets(self) -> None:
        data = copy.deepcopy(self.base)
        data["evals"][0]["prompt"] = '[{"' * 100
        self.write_manifest(data)
        self.assertEqual(output_evals.inspect_output_evals(self.root)[1], [])
        raw = '{"nested":' + '[' * 64 + '0' + ']' * 64 + '}'
        self.manifest.write_text(raw, encoding="utf-8")
        with mock.patch.object(output_evals.json, "loads", side_effect=AssertionError("parser reached")):
            self.assert_invalid()

    def test_duplicate_nested_keys_reject_even_unknown_fields(self) -> None:
        self.write_manifest(self.base)
        raw = self.manifest.read_text(encoding="utf-8")
        self.manifest.write_text(raw[:-1] + ',"unknown":{"x":1,"x":2}}', encoding="utf-8")
        self.assert_invalid()

    def test_bounded_reader_rejects_growth_and_never_reads_more_than_limit_plus_one(self) -> None:
        path = self.root / "growing.txt"
        path.write_bytes(b"123456789")
        info = SimpleNamespace(st_mode=stat.S_IFREG, st_size=1)
        original_read = os.read
        with mock.patch.object(output_evals.os, "stat", return_value=info), \
             mock.patch.object(output_evals.os, "fstat", return_value=info), \
             mock.patch.object(output_evals.os, "read", wraps=original_read) as read:
            with self.assertRaisesRegex(ValueError, "^Invalid or unsafe local fixture.$"):
                output_evals.read_bounded_regular_file(self.root, path, 8)
        self.assertEqual(read.call_args_list[0].args[1], 9)
        self.assertEqual(read.call_count, 1)

    def test_bounded_reader_safe_paths_limits_and_symlinks(self) -> None:
        path = self.root / "safe.txt"
        path.write_bytes(b"safe")
        self.assertEqual(output_evals.read_bounded_regular_file(self.root, path, 4), b"safe")
        self.assertEqual(output_evals.read_bounded_regular_file(self.root, Path("safe.txt"), 4), b"safe")
        linked = self.root / "linked.txt"
        linked.symlink_to(path)
        for candidate, limit in ((path, 3), (linked, 4), (Path("../escape"), 4),
                                 (self.root.parent / "outside", 4), (path, -1), (path, True)):
            with self.subTest(candidate=candidate, limit=limit):
                with self.assertRaisesRegex(ValueError, "^Invalid or unsafe local fixture.$"):
                    output_evals.read_bounded_regular_file(self.root, candidate, limit)

    def test_unavailable_descriptor_support_is_generic_advisory(self) -> None:
        self.write_manifest(self.base)
        with mock.patch.object(output_evals.os, "open", side_effect=NotImplementedError("PRIVATE")):
            self.assert_invalid()
        with mock.patch.dict(output_evals.os.__dict__):
            del output_evals.os.O_NOFOLLOW
            self.assert_invalid()

    def test_bounded_reader_accepts_filesystem_relative_and_root_relative_paths(self) -> None:
        path = self.root / "safe.txt"
        path.write_bytes(b"safe")
        relative_root = Path(os.path.relpath(self.root))
        self.assertEqual(output_evals.read_bounded_regular_file(relative_root, relative_root / "safe.txt"), b"safe")
        self.assertEqual(output_evals.read_bounded_regular_file(relative_root, Path("safe.txt")), b"safe")

    def test_valid_and_invalid_metadata_findings_and_logs_do_not_disclose_fixture_values(self) -> None:
        sentinel = "PRIVATE_SENTINEL_89X"
        data = copy.deepcopy(self.base)
        data["skill_name"] = sentinel
        case = data["evals"][0]
        case.update(id=987654321, prompt=sentinel, expected_output=sentinel,
                    assertions=[sentinel], expectations=[sentinel], files=[sentinel + ".csv"])
        (self.root / (sentinel + ".csv")).write_bytes(sentinel.encode())
        for invalid in (False, True):
            with self.subTest(invalid=invalid):
                self.write_manifest(data)
                if invalid:
                    self.manifest.write_text('{"' + sentinel + '":', encoding="utf-8")
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    metadata, findings = output_evals.inspect_output_evals(self.root)
                public = json.dumps(metadata) + "".join(str(item) + item.reason for item in findings)
                self.assertNotIn(sentinel, public)
                self.assertNotIn("987654321", public)
                self.assertEqual(stdout.getvalue() + stderr.getvalue(), "")

    def test_internal_module_help_and_direct_invocation(self) -> None:
        script = SCRIPTS_DIR / "output_evals.py"
        help_result = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("Usage:", help_result.stdout)
        direct = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
        self.assertEqual(direct.returncode, 2)
        self.assertIn("error", json.loads(direct.stdout))


if __name__ == "__main__":
    unittest.main()
