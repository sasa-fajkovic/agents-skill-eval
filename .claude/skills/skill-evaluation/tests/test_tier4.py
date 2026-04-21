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

    def test_check_4_1_flags_appropriately(self) -> None:
        body = "Format the output appropriately for the user."
        findings = tier4.check_4_1(body)
        self.assertTrue(any(f.check_id == "4.1" for f in findings))

    def test_check_4_1_flags_as_needed(self) -> None:
        body = "Add logging as needed throughout the code."
        findings = tier4.check_4_1(body)
        self.assertTrue(any(f.check_id == "4.1" for f in findings))

    def test_check_4_1_passes_handle_errors(self) -> None:
        body = "Handle errors that occur during the process."
        findings = tier4.check_4_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.1"]), 0)

    def test_check_4_1_passes_clean_up(self) -> None:
        body = "Clean up the environment after running."
        findings = tier4.check_4_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.1"]), 0)

    def test_check_4_1_passes_concise(self) -> None:
        body = "Write concise commit messages."
        findings = tier4.check_4_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.1"]), 0)

    def test_check_4_1_passes_clear(self) -> None:
        body = "Provide clear error messages to the user."
        findings = tier4.check_4_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.1"]), 0)

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

    def test_check_4_4_skips_table_separators(self) -> None:
        body = "| Input | Becomes |\n|-------|---------|"
        findings = tier4.check_4_4(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.4"]), 0)

    def test_check_4_4_skips_inline_command_references(self) -> None:
        body = "Do NOT use `gh pr edit --add-reviewer @copilot` directly."
        findings = tier4.check_4_4(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.4"]), 0)

    def test_check_4_4_keeps_single_token_inline_flags(self) -> None:
        body = "## Input\n\n- `--target`\n\n## Process\n\nUse `--target` to select."
        findings = tier4.check_4_4(body)
        self.assertTrue(any(f.check_id == "4.4" for f in findings))

    # --- 4.3: negative-only with positive alternative detection ---

    def test_check_4_3_passes_when_only_precedes_never(self) -> None:
        body = "Only move forward. Never regress."
        findings = tier4.check_4_3(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.3"]), 0)

    def test_check_4_3_passes_when_always_precedes_never(self) -> None:
        body = "Always query transitions dynamically; never assume status names."
        findings = tier4.check_4_3(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.3"]), 0)

    def test_check_4_3_passes_when_leave_is_alternative(self) -> None:
        body = "Do NOT resolve threads that are questions.\nIf unsure, leave it open."
        findings = tier4.check_4_3(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.3"]), 0)

    def test_check_4_3_passes_when_ok_to_is_alternative(self) -> None:
        body = "Never resolve human reviewer threads. OK to resolve bot threads."
        findings = tier4.check_4_3(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.3"]), 0)

    def test_check_4_3_passes_when_positive_precedes_negative(self) -> None:
        body = "Stage specific files only.\nNever use git add -A."
        findings = tier4.check_4_3(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.3"]), 0)

    def test_check_4_3_flags_pure_negative(self) -> None:
        body = "Never push to main branch."
        findings = tier4.check_4_3(body)
        self.assertTrue(any(f.check_id == "4.3" for f in findings))

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

    # --- 4.6: success criteria ---

    def test_check_4_6_passes_with_must_return(self) -> None:
        body = "The script must return a JSON object with the results."
        findings = tier4.check_4_6(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.6"]), 0)

    def test_check_4_6_passes_with_done_when(self) -> None:
        body = "Done when all tests pass and the PR is created."
        findings = tier4.check_4_6(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.6"]), 0)

    def test_check_4_6_passes_with_output_heading(self) -> None:
        body = "## Output\n\nA JSON file with the evaluation results."
        findings = tier4.check_4_6(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.6"]), 0)

    def test_check_4_6_flags_when_only_descriptive_return(self) -> None:
        body = "The API returns a 200 status code when the request succeeds."
        findings = tier4.check_4_6(body)
        self.assertTrue(any(f.check_id == "4.6" for f in findings))

    def test_check_4_6_flags_when_no_criteria(self) -> None:
        body = "Run the linter on all files in the repository."
        findings = tier4.check_4_6(body)
        self.assertTrue(any(f.check_id == "4.6" for f in findings))

    def test_check_4_6_passes_with_report_heading(self) -> None:
        body = "### Report\n\nCopilot review requested on PR #123."
        findings = tier4.check_4_6(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.6"]), 0)

    def test_check_4_6_passes_with_verify_heading(self) -> None:
        body = "### Verify\n\nShould show: {\"status\":\"completed\"}."
        findings = tier4.check_4_6(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.6"]), 0)

    def test_check_4_6_passes_with_confirm_heading(self) -> None:
        body = "## Confirm\n\nLog which threads were resolved."
        findings = tier4.check_4_6(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.6"]), 0)

    def test_check_4_6_passes_with_should_show(self) -> None:
        body = "# Should show: {\"status\":\"cancelled\"}."
        findings = tier4.check_4_6(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.6"]), 0)

    def test_check_4_6_passes_with_prints_on_success(self) -> None:
        body = "Prints the ticket key on success."
        findings = tier4.check_4_6(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.6"]), 0)

    # --- 4.1: raised ambiguity threshold ---

    def test_check_4_1_skips_numbered_list_items(self) -> None:
        body = "1. Format the output appropriately based on the schema."
        findings = tier4.check_4_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.1"]), 0)

    def test_check_4_1_skips_lines_with_inline_code(self) -> None:
        body = "Set the appropriate value using `config.json`."
        findings = tier4.check_4_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.1"]), 0)

    def test_check_4_1_skips_lines_with_numbers(self) -> None:
        body = "Use the appropriate timeout of 30 seconds."
        findings = tier4.check_4_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.1"]), 0)

    # --- 4.4: expanded context window ---

    def test_check_4_4_finds_default_5_lines_away(self) -> None:
        """Flag should be suppressed when default is documented 5 lines away."""
        body = "line1\nline2\nline3\nIf omitted, defaults to main branch.\nline5\nUse --target to specify the branch."
        findings = tier4.check_4_4(body)
        self.assertEqual(len([f for f in findings if f.check_id == "4.4"]), 0)


if __name__ == "__main__":
    unittest.main()
