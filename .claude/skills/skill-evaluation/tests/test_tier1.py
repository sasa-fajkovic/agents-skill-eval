from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import tier1  # noqa: E402


class Tier1Tests(unittest.TestCase):
    def test_check_1_8_only_requires_help_for_entrypoints(self) -> None:
        root = Path(tempfile.mkdtemp())
        skill_dir = root / "demo"
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "runner.py").write_text('if __name__ == "__main__":\n    pass\n', encoding="utf-8")
        (scripts_dir / "helper.py").write_text('def helper():\n    return 1\n', encoding="utf-8")

        findings = tier1.check_1_8(str(skill_dir))
        self.assertEqual(len(findings), 1)
        self.assertIn("runner.py", findings[0].message)

    # --- 1.1: consecutive hyphens ---

    def test_check_1_1_flags_consecutive_hyphens(self) -> None:
        fm = {"name": "my--skill"}
        findings = tier1.check_1_1(fm, "/tmp/my--skill")
        self.assertTrue(any(f.check_id == "1.1" and "consecutive hyphens" in f.message for f in findings))

    def test_check_1_1_passes_single_hyphens(self) -> None:
        fm = {"name": "my-cool-skill"}
        findings = tier1.check_1_1(fm, "/tmp/my-cool-skill")
        name_findings = [f for f in findings if "hyphens" in f.message or "must be" in f.message]
        self.assertEqual(len(name_findings), 0)

    def test_check_1_1_has_no_word_count_limit(self) -> None:
        name = "one-two-three-four-five-six-seven-eight-nine-ten-eleven"
        self.assertEqual(tier1.check_1_1({"name": name}, f"/tmp/{name}"), [])

    def test_check_1_1_length_boundaries(self) -> None:
        for count in (1, 50, 51, 64, 65):
            with self.subTest(count=count):
                name = "a" * count
                findings = tier1.check_1_1({"name": name}, f"/tmp/{name}")
                self.assertEqual(bool(findings), count > 64)
        self.assertTrue(tier1.check_1_1({"name": ""}, "/tmp/demo"))

    def test_check_1_1_unicode_normalization(self) -> None:
        for name, dirname in (("café", "cafe\u0301"), ("cafe\u0301", "café"), ("技能", "技能"), ("ｄｅｍｏ", "demo"), ("demo", "ｄｅｍｏ"), (" demo ", "demo")):
            with self.subTest(name=name, dirname=dirname):
                self.assertEqual(tier1.check_1_1({"name": name}, f"/tmp/{dirname}"), [])

    def test_check_1_1_rejects_invalid_normalized_names(self) -> None:
        for name in ("Demo", "Ｄｅｍｏ", "a_b", "a b", "-demo", "demo-", "demo--name", "demo😀", "é\u0301"):
            with self.subTest(name=name):
                self.assertTrue(tier1.check_1_1({"name": name}, f"/tmp/{name}"))

    def test_check_1_1_matches_real_directory_but_skips_synthetic_upload(self) -> None:
        self.assertTrue(tier1.check_1_1({"name": "demo"}, "/tmp/different"))
        self.assertEqual(tier1.check_1_1({"name": "demo"}, "/tmp/eval-01234567-0123-0123-0123-0123456789ab"), [])

    # --- 1.2: description length thresholds ---

    def test_check_1_2_errors_over_1024(self) -> None:
        desc = "Use when " + "x" * 1016
        fm = {"name": "demo", "description": desc}
        findings = tier1.check_1_2(fm)
        err_findings = [f for f in findings if f.check_id == "1.2" and f.severity == "ERROR" and "max 1024" in f.message]
        self.assertEqual(len(err_findings), 1)

    def test_check_1_2_passes_short_description(self) -> None:
        fm = {"name": "demo", "description": "Use when reviewing skills."}
        findings = tier1.check_1_2(fm)
        length_findings = [f for f in findings if "max 1024" in f.message or "context bloat" in f.message]
        self.assertEqual(len(length_findings), 0)

    def test_check_1_2_accepts_up_to_1024_as_spec_valid(self) -> None:
        for count in (500, 501, 1024):
            with self.subTest(count=count):
                findings = tier1.check_1_2({"description": "Use when " + "x" * (count - 9)})
                self.assertFalse(any(f.severity == "ERROR" for f in findings))
                self.assertEqual(findings, [])

    def test_check_1_2_accepts_varied_activation_context(self) -> None:
        for description in (
            "Use this skill when the user wants a CSV chart.",
            "Use the skill for charting CSV files.",
            "When the user needs a chart from tabular data, analyze the file.",
            "Activate when a tabular data analysis workflow needs custom grouping.",
            "Analyze tabular data. Activate this skill when the user shares a CSV.",
            "Invokes when the user needs CSV analysis.",
            "Triggers for CSV analysis requests.",
            "Analyze CSVs. Use this when a spreadsheet needs charts.",
            "Use it when a spreadsheet needs charts.",
            "Analyze CSVs whenever the user asks for regional totals.",
            "Analyze CSVs when a user needs a chart.",
            "Analyze CSVs when users need a chart.",
            "Triggers on CSV uploads.",
            "Activate on CSV analysis requests.",
        ):
            with self.subTest(description=description):
                self.assertEqual(tier1.check_1_2({"description": description}), [])

    def test_check_1_2_does_not_mistake_trigger_nouns_for_activation(self) -> None:
        for description in ("Create database triggers from SQL.", "Deactivate inactive users."):
            findings = tier1.check_1_2({"description": description})
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].severity, "WARN")
            self.assertIn("heuristic", findings[0].message)

    def test_check_1_3_allowed_tools_strings_have_no_finding(self) -> None:
        for value in ("Bash(git:*) Bash(jq:*) Read", ""):
            fm = {"allowed-tools": value}
            self.assertEqual(tier1.check_1_3(fm) + tier1.check_1_4(fm), [])

    def test_check_1_4_allowed_tools_requires_string(self) -> None:
        for value in (["Bash"], {"tool": "Bash"}, True, 12, None):
            with self.subTest(value=value):
                findings = tier1.check_1_3({"allowed-tools": value}) + tier1.check_1_4({"allowed-tools": value})
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0].severity, "ERROR")
                self.assertEqual(findings[0].check_id, "1.4")

    def test_check_1_3_claude_code_extension_is_warn(self) -> None:
        fm = {"name": "demo", "description": "test", "model": "claude-sonnet"}
        findings = tier1.check_1_3(fm)
        model_findings = [f for f in findings if "model" in f.message]
        self.assertEqual(len(model_findings), 1)
        self.assertEqual(model_findings[0].severity, "WARN")

    # --- 1.4: empty value checks ---

    def test_check_1_4_license_has_no_minimum_length(self) -> None:
        for value in ("", "   "):
            self.assertEqual(tier1.check_1_4({"license": value}), [])

    def test_check_1_4_metadata_is_arbitrary_string_mapping(self) -> None:
        fm = {"metadata": {"author": "example-org", "version": "1.0", "model": "custom data", "tags": "ci"}}
        self.assertEqual(tier1.check_1_3(fm) + tier1.check_1_4(fm) + tier1.check_1_5(fm), [])
        for value in (["ci"], 1, True, None, {"nested": "value"}):
            with self.subTest(value=value):
                self.assertTrue(tier1.check_1_4({"metadata": {"custom": value}}))

    def test_check_1_4_passes_valid_license(self) -> None:
        fm = {"name": "demo", "description": "test", "license": "MIT"}
        findings = tier1.check_1_4(fm)
        self.assertEqual(len(findings), 0)

    def test_check_1_4_flags_empty_compatibility(self) -> None:
        fm = {"name": "demo", "description": "test", "compatibility": ""}
        findings = tier1.check_1_4(fm)
        self.assertTrue(any(f.check_id == "1.4" and "empty" in f.message for f in findings))

    def test_check_1_4_passes_valid_compatibility(self) -> None:
        fm = {"name": "demo", "description": "test", "compatibility": "Requires git CLI."}
        findings = tier1.check_1_4(fm)
        self.assertEqual(len(findings), 0)

    # --- 1.11: script language handling ---

    def test_check_1_11_js_is_policy_warning(self) -> None:
        root = Path(tempfile.mkdtemp())
        skill_dir = root / "demo"
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "runner.js").write_text("console.log('hi');\n", encoding="utf-8")

        findings = tier1.check_1_11(str(skill_dir))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "WARN")
        self.assertIn("evaluator portability policy", findings[0].message)

    def test_check_1_11_ts_is_warn(self) -> None:
        root = Path(tempfile.mkdtemp())
        skill_dir = root / "demo"
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "runner.ts").write_text("console.log('hi');\n", encoding="utf-8")

        findings = tier1.check_1_11(str(skill_dir))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "WARN")

    def test_check_1_11_py_is_allowed(self) -> None:
        root = Path(tempfile.mkdtemp())
        skill_dir = root / "demo"
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "runner.py").write_text("print('hi')\n", encoding="utf-8")

        findings = tier1.check_1_11(str(skill_dir))
        self.assertEqual(len(findings), 0)

    def test_check_1_11_other_language_is_warning_not_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp) / "scripts"
            scripts.mkdir()
            for ext in (".lua", ".rs", ".go"):
                (scripts / f"runner{ext}").write_text("", encoding="utf-8")
            findings = tier1.check_1_11(tmp)
            self.assertEqual(len(findings), 3)
            self.assertTrue(all(f.severity == "WARN" for f in findings))

    def test_check_1_6_recommendation_applies_to_all_skills(self) -> None:
        for description in ("Use when creating PRs", "reference documentation", "knowledge base"):
            for count in (499, 500, 501, 800, 850):
                with self.subTest(description=description, count=count):
                    findings = tier1.check_1_6(["line"] * count, description)
                    self.assertEqual(len(findings), int(count >= 500))
                    self.assertTrue(all(f.severity == "WARN" for f in findings))


if __name__ == "__main__":
    unittest.main()
