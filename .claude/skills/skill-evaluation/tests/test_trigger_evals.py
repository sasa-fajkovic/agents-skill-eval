from __future__ import annotations

import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from trigger_evals import inspect_trigger_evals, MAX_QUERY_FILE_BYTES
from extract import collect_metadata
import eval as evaluator


class TriggerEvalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "demo"
        self.root.mkdir()
        (self.root / "SKILL.md").write_text("---\nname: demo\ndescription: Use this skill when reviewing CSV analysis.\n---\n## Output\nReturn the result.\n")

    def fixture(self, value, name="eval_queries.json") -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def test_absent_fixtures_are_optional_and_unmeasured(self) -> None:
        metadata, findings = inspect_trigger_evals(self.root)
        self.assertEqual(findings, [])
        self.assertEqual(metadata, {"measurement": "not_run", "files": []})
        self.assertFalse(any(f.check_id == "4.9" for f in evaluator.evaluate(str(self.root))))

    def test_counts_labels_without_invoking_or_exposing_query_text(self) -> None:
        query = f"Run $(touch {self.root / 'SHOULD_NEVER_EXIST'}) then read ~/private.csv"
        self.fixture([{"query": query, "should_trigger": True, "notes": "extra field"}, {"query": "near miss", "should_trigger": False}])
        metadata = collect_metadata(self.root / "SKILL.md")["trigger_evals"]
        self.assertEqual(metadata["measurement"], "not_run")
        self.assertEqual(metadata["files"], [{"path": "eval_queries.json", "valid": True, "query_count": 2, "should_trigger_count": 1, "should_not_trigger_count": 1}])
        self.assertNotIn(query, json.dumps(metadata))
        self.assertFalse((self.root / "SHOULD_NEVER_EXIST").exists())

    def test_invalid_structures_warn_without_becoming_format_errors(self) -> None:
        for value in ({}, [], ["prompt"], [{"query": "", "should_trigger": True}], [{"query": "request", "should_trigger": 1}], [{"query": "request", "should_trigger": "false"}], [{"should_trigger": False}]):
            with self.subTest(value=value):
                self.fixture(value)
                metadata, findings = inspect_trigger_evals(self.root)
                self.assertEqual(len(findings), 1)
                self.assertEqual((findings[0].check_id, findings[0].severity), ("4.9", "WARN"))
                self.assertIn("optional evaluator policy", findings[0].message)
                self.assertFalse(metadata["files"][0]["valid"])
                self.assertEqual(metadata["files"][0]["query_count"], 0)

    def test_recognizes_documented_names_and_locations_only(self) -> None:
        data = [{"query": "request", "should_trigger": True}]
        for name in ("train_queries.json", "validation_queries.json", "evals/eval_queries.json", "evals/train_queries.json", "evals/validation_queries.json"):
            self.fixture(data, name)
        self.fixture({"not": "a trigger fixture"}, "evals/evals.json")
        self.fixture([], "data.json")
        metadata, findings = inspect_trigger_evals(self.root)
        self.assertEqual(findings, [])
        self.assertEqual(len(metadata["files"]), 5)

    def test_unreadable_large_and_deep_json_do_not_crash(self) -> None:
        path = self.root / "eval_queries.json"
        for content in (b'not json SECRET_QUERY', b'\xff', b'[' * 2000, b' ' * (MAX_QUERY_FILE_BYTES + 1)):
            with self.subTest(length=len(content)):
                path.write_bytes(content)
                metadata, findings = inspect_trigger_evals(self.root)
                self.assertEqual(len(findings), 1)
                self.assertFalse(metadata["files"][0]["valid"])
                self.assertNotIn("SECRET_QUERY", findings[0].message)

    def test_symlinks_are_not_followed(self) -> None:
        external = self.root.parent / "external.json"
        external.write_text('[{"query":"private", "should_trigger":true}]')
        (self.root / "eval_queries.json").symlink_to(external)
        metadata, findings = inspect_trigger_evals(self.root)
        self.assertFalse(metadata["files"][0]["valid"])
        self.assertIn("symlink", findings[0].message)
        result = evaluator.build_json_result(self.root / "SKILL.md", findings)
        self.assertNotIn("private", result["supporting_context"])
        self.assertIn("content omitted", result["supporting_context"])

    def test_oversized_fixture_is_omitted_from_supporting_context(self) -> None:
        path = self.root / "eval_queries.json"
        path.write_text("PRIVATE_SENTINEL" + " " * MAX_QUERY_FILE_BYTES)
        result = evaluator.build_json_result(self.root / "SKILL.md", evaluator.evaluate(str(self.root)))
        self.assertNotIn("PRIVATE_SENTINEL", result["supporting_context"])
        self.assertIn("content omitted", result["supporting_context"])

    def test_valid_fixture_remains_available_for_optional_content_review(self) -> None:
        self.fixture([{"query": "REALISTIC_QUERY", "should_trigger": True}])
        result = evaluator.build_json_result(self.root / "SKILL.md", evaluator.evaluate(str(self.root)))
        self.assertIn("REALISTIC_QUERY", result["supporting_context"])
        self.assertNotIn("REALISTIC_QUERY", json.dumps(result["metadata"]))

    def test_non_regular_query_file_warns(self) -> None:
        (self.root / "eval_queries.json").mkdir()
        metadata, findings = inspect_trigger_evals(self.root)
        self.assertFalse(metadata["files"][0]["valid"])
        self.assertIn("regular query file", findings[0].message)

    def test_permission_error_during_discovery_warns_without_crashing(self) -> None:
        inaccessible = self.root / "evals" / "eval_queries.json"
        original_exists = Path.exists

        def exists(path):
            if path == inaccessible:
                raise PermissionError("access denied")
            return original_exists(path)

        with patch.object(Path, "exists", exists):
            metadata, findings = inspect_trigger_evals(self.root)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "WARN")
        self.assertEqual(metadata["files"], [])
        self.assertIn("cannot inspect", findings[0].message)

    def test_malformed_fixture_reaches_ci_json_as_warning(self) -> None:
        self.fixture([{"query": "request", "should_trigger": "yes"}])
        findings = evaluator.evaluate(str(self.root))
        result = evaluator.build_json_result(self.root / "SKILL.md", findings)
        warnings = result["findings"]["warning_findings"]
        self.assertTrue(any(item["rule_id"] == "4.9" and "not measured" in item["message"] for item in warnings))
        self.assertFalse(any(item["rule_id"] == "4.9" for item in result["findings"]["error_findings"]))
        self.assertEqual(result["metadata"]["trigger_evals"]["measurement"], "not_run")


if __name__ == "__main__":
    unittest.main()
