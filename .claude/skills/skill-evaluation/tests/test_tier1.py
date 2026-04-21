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

    # --- 1.3: allowed-tools is WARN not ERROR ---

    def test_check_1_3_allowed_tools_is_warn(self) -> None:
        fm = {"name": "demo", "description": "test", "allowed-tools": ["Bash"]}
        findings = tier1.check_1_3(fm)
        at_findings = [f for f in findings if "allowed-tools" in f.message]
        self.assertEqual(len(at_findings), 1)
        self.assertEqual(at_findings[0].severity, "WARN")

    def test_check_1_3_claude_code_extension_is_warn(self) -> None:
        fm = {"name": "demo", "description": "test", "model": "claude-sonnet"}
        findings = tier1.check_1_3(fm)
        model_findings = [f for f in findings if "model" in f.message]
        self.assertEqual(len(model_findings), 1)
        self.assertEqual(model_findings[0].severity, "WARN")

    # --- 1.4: empty value checks ---

    def test_check_1_4_flags_empty_license(self) -> None:
        fm = {"name": "demo", "description": "test", "license": ""}
        findings = tier1.check_1_4(fm)
        self.assertTrue(any(f.check_id == "1.4" and "empty" in f.message for f in findings))

    def test_check_1_4_flags_whitespace_only_license(self) -> None:
        fm = {"name": "demo", "description": "test", "license": "   "}
        findings = tier1.check_1_4(fm)
        self.assertTrue(any(f.check_id == "1.4" and "empty" in f.message for f in findings))

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

    # --- 1.7: escalation based on script complexity ---

    def test_check_1_7_warns_for_simple_untested_script(self) -> None:
        root = Path(tempfile.mkdtemp())
        skill_dir = root / "demo"
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "simple.sh").write_text("#!/bin/bash\necho hello\n", encoding="utf-8")

        findings = tier1.check_1_7(str(skill_dir))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "WARN")

    def test_check_1_7_warns_for_complex_untested_script(self) -> None:
        root = Path(tempfile.mkdtemp())
        skill_dir = root / "demo"
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        # 35-line script with conditionals
        lines = ["#!/bin/bash\n"]
        lines.append('if [ "$1" == "--help" ]; then\n')
        lines.append('  echo "Usage: complex.sh"\n')
        lines.append("fi\n")
        for i in range(31):
            lines.append(f"echo line{i}\n")
        (scripts_dir / "complex.sh").write_text("".join(lines), encoding="utf-8")

        findings = tier1.check_1_7(str(skill_dir))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "WARN")
        self.assertIn("strongly recommended", findings[0].message)

    def test_check_1_7_passes_when_test_exists(self) -> None:
        root = Path(tempfile.mkdtemp())
        skill_dir = root / "demo"
        scripts_dir = skill_dir / "scripts"
        tests_dir = skill_dir / "tests"
        scripts_dir.mkdir(parents=True)
        tests_dir.mkdir(parents=True)
        (scripts_dir / "runner.sh").write_text("#!/bin/bash\necho hello\n", encoding="utf-8")
        (tests_dir / "runner.bats").write_text("@test 'runs' { run ./runner.sh; }", encoding="utf-8")

        findings = tier1.check_1_7(str(skill_dir))
        self.assertEqual(len(findings), 0)

    # --- 1.11: script language handling ---

    def test_check_1_11_js_is_warn_with_spec_mention(self) -> None:
        root = Path(tempfile.mkdtemp())
        skill_dir = root / "demo"
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "runner.js").write_text("console.log('hi');\n", encoding="utf-8")

        findings = tier1.check_1_11(str(skill_dir))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "WARN")
        self.assertIn("spec lists JavaScript as common", findings[0].message)

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

    # --- 1.6: reference skill line limit ---

    def test_check_1_6_reference_skill_gets_higher_limit(self) -> None:
        """Reference skills get 800-line limit instead of 500."""
        lines = ["line"] * 600  # Over 500 but under 800
        findings = tier1.check_1_6(lines, description="reference documentation for API endpoints")
        self.assertEqual(len(findings), 0)

    def test_check_1_6_reference_skill_over_limit(self) -> None:
        lines = ["line"] * 850  # Over 800
        findings = tier1.check_1_6(lines, description="knowledge base reference")
        self.assertEqual(len(findings), 1)
        self.assertIn("800", findings[0].message)

    def test_check_1_6_normal_skill_500_limit(self) -> None:
        lines = ["line"] * 600
        findings = tier1.check_1_6(lines, description="Use when creating PRs")
        self.assertEqual(len(findings), 1)
        self.assertIn("500", findings[0].message)


if __name__ == "__main__":
    unittest.main()
