from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import tier4  # noqa: E402


class Tier4Tests(unittest.TestCase):
    def test_check_4_7_only_checks_entrypoint_scripts(self) -> None:
        root = Path(tempfile.mkdtemp())
        skill_dir = root / "demo"
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "runner.py").write_text('if __name__ == "__main__":\n    import sys\n    sys.exit(1)\n', encoding="utf-8")
        (scripts_dir / "helper.py").write_text('def helper():\n    return 1\n', encoding="utf-8")

        findings = tier4.check_4_7(str(skill_dir))
        self.assertEqual(len(findings), 1)
        self.assertIn("runner.py", findings[0].message)

    # --- 4.1: unbounded scope + context qualification ---

    def test_check_4_1_flags_handle_errors(self) -> None:
        body = "Handle errors that occur during the process."
        findings = tier4.check_4_1(body)
        self.assertTrue(any(f.check_id == "4.1" for f in findings))

    def test_check_4_1_flags_clean_up(self) -> None:
        body = "Clean up the environment after running."
        findings = tier4.check_4_1(body)
        self.assertTrue(any(f.check_id == "4.1" for f in findings))

    def test_check_4_1_suppresses_qualified_ambiguous_word(self) -> None:
        body = "Write concise summaries (1-2 sentences per point)."
        findings = tier4.check_4_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.1"]), 0)

    def test_check_4_1_suppresses_with_example_qualifier(self) -> None:
        body = 'Use the proper format, for example: "YYYY-MM-DD".'
        findings = tier4.check_4_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.1"]), 0)

    def test_check_4_1_skips_table_rows(self) -> None:
        body = "| Field | Type | Description: relevant data |"
        findings = tier4.check_4_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.1"]), 0)

    def test_check_4_1_skips_list_items(self) -> None:
        body = "- Use appropriate tools as needed"
        findings = tier4.check_4_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.1"]), 0)

    # --- 4.2: tool delegation exclusion ---

    def test_check_4_2_suppresses_when_tool_delegates_output(self) -> None:
        body = "## Output\n\nUse `gh pr create` to create the pull request."
        findings = tier4.check_4_2(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.2"]), 0)

    # --- 4.4: skip flags in fenced code blocks ---

    def test_check_4_4_skips_flags_in_fenced_code_blocks(self) -> None:
        body = "## Input\n\n## Process\n\n```bash\ncurl --header 'Accept: json' https://api.example.com\n```"
        findings = tier4.check_4_4(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.4"]), 0)

    def test_check_4_4_flags_flags_in_prose(self) -> None:
        body = "## Process\n\nRun the script with --target and summarize the result."
        findings = tier4.check_4_4(body)
        self.assertTrue(any(f.check_id == "4.4" for f in findings))

    # --- 4.5: broader non-idempotent patterns ---

    def test_check_4_5_flags_insert_into(self) -> None:
        body = "INSERT INTO users (name) VALUES ('test')"
        findings = tier4.check_4_5(body)
        self.assertTrue(any(f.check_id == "4.5" for f in findings))

    def test_check_4_5_flags_post_api(self) -> None:
        body = "POST /api/v1/resources to create a new resource."
        findings = tier4.check_4_5(body)
        self.assertTrue(any(f.check_id == "4.5" for f in findings))

    # --- 4.7: exit code documentation check ---

    def test_check_4_7_passes_when_exit_codes_documented(self) -> None:
        root = Path(tempfile.mkdtemp())
        skill_dir = root / "demo"
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        content = (
            'if __name__ == "__main__":\n'
            "    import sys\n"
            '    # Exit codes: 0=success, 1=failure\n'
            "    sys.exit(1)\n"
        )
        (scripts_dir / "runner.py").write_text(content, encoding="utf-8")
        (scripts_dir / "helper.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

        findings = tier4.check_4_7(str(skill_dir))
        self.assertEqual(len([f for f in findings if f.check_id == "4.7"]), 0)


if __name__ == "__main__":
    unittest.main()
