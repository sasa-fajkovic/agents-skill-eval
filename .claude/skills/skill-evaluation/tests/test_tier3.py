from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import tier3  # noqa: E402


class Tier3Tests(unittest.TestCase):
    def test_check_3_1_flags_long_inline_code(self) -> None:
        body = """```bash\na\nb\nc\nd\ne\nf\n```"""
        findings = tier3.check_3_1(body)
        self.assertTrue(any(finding.check_id == "3.1" for finding in findings))

    def test_check_3_1_skips_json_blocks(self) -> None:
        body = "```json\n" + "\n".join(f'"key{i}": "val{i}"' for i in range(10)) + "\n```"
        findings = tier3.check_3_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "3.1"]), 0)

    def test_check_3_1_skips_non_executable_without_script_patterns(self) -> None:
        body = "```xml\n" + "\n".join(f"<item{i}/>" for i in range(10)) + "\n```"
        findings = tier3.check_3_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "3.1"]), 0)

    def test_check_3_1_flags_unlabeled_block_with_pipes(self) -> None:
        body = "```\ncat file.txt | grep foo | sort | uniq\nline2\nline3\nline4\nline5\nline6\n```"
        findings = tier3.check_3_1(body)
        self.assertTrue(any(f.check_id == "3.1" for f in findings))

    def test_check_3_1_skips_template_block_with_checkboxes(self) -> None:
        body = (
            "```\n"
            "## Goals to achieve\n"
            "[2-3 sentences: purpose and motivation]\n"
            "\n"
            "## Acceptance criteria\n"
            "- [ ] Criterion 1\n"
            "- [ ] Criterion 2\n"
            "- [ ] Criterion 3\n"
            "```"
        )
        findings = tier3.check_3_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "3.1"]), 0)

    def test_check_3_1_skips_template_block_with_placeholders(self) -> None:
        body = (
            "```\n"
            "## Description\n"
            "[summarize the change...]\n"
            "## Steps\n"
            "- [x] Step already done\n"
            "- [ ] Remaining step\n"
            "- [ ] Another remaining step\n"
            "```"
        )
        findings = tier3.check_3_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "3.1"]), 0)

    # --- 3.3: broader standard tool tutorials ---

    def test_check_3_3_flags_git_clone_tutorial(self) -> None:
        body = "To clone the repo, run `git clone https://example.com/repo.git`"
        findings = tier3.check_3_3(body)
        self.assertTrue(any(f.check_id == "3.3" for f in findings))

    def test_check_3_3_flags_curl_tutorial(self) -> None:
        body = "Use curl to fetch the data from the API endpoint."
        findings = tier3.check_3_3(body)
        self.assertTrue(any(f.check_id == "3.3" for f in findings))

    # --- 3.4: prose deduplication ---

    def test_check_3_4_flags_near_duplicate_prose(self) -> None:
        body = (
            "This skill reads the configuration and validates it against the schema before deployment.\n"
            "\n"
            "Some other content here.\n"
            "\n"
            "This skill reads the configuration and validates it against the schema before deploying.\n"
        )
        findings = tier3.check_3_4(body)
        self.assertTrue(any(f.check_id == "3.4" for f in findings))

    def test_check_3_4_passes_unique_prose(self) -> None:
        body = (
            "This skill validates configuration files against the schema.\n"
            "\n"
            "The output includes a summary of all issues found during evaluation.\n"
        )
        findings = tier3.check_3_4(body)
        self.assertEqual(len([f for f in findings if f.check_id == "3.4"]), 0)

    def test_check_3_4_skips_short_similar_code_blocks(self) -> None:
        body = (
            "```bash\nbash ~/.claude/skills/demo/scripts/setup.sh owner repo 123\n```\n"
            "\n"
            "Then later:\n"
            "\n"
            "```bash\nbash ~/.claude/skills/demo/scripts/review.sh owner repo 123\n```"
        )
        findings = tier3.check_3_4(body)
        self.assertEqual(len([f for f in findings if f.check_id == "3.4"]), 0)

    # --- 3.1: content sniffing for unlabeled blocks ---

    def test_check_3_1_skips_unlabeled_json_config_block(self) -> None:
        """Unlabeled block that looks like JSON config should not fire."""
        body = '```\n{\n  "key1": "val1",\n  "key2": "val2",\n  "key3": "val3",\n  "key4": "val4",\n  "key5": "val5",\n  "key6": "val6"\n}\n```'
        findings = tier3.check_3_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "3.1"]), 0)

    def test_check_3_1_skips_unlabeled_yaml_config_block(self) -> None:
        """Unlabeled block that looks like YAML config should not fire."""
        body = "```\nname: demo\nversion: 1.0\nhost: localhost\nport: 8080\ndb_name: mydb\ntimeout: 30\n```"
        findings = tier3.check_3_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "3.1"]), 0)

    # --- 3.2: core content heading awareness ---

    def test_check_3_2_skips_table_under_test_matrix_heading(self) -> None:
        """Tables under 'Test Matrix' heading are core content — don't flag."""
        rows = ["| Col A | Col B | Col C |"] + ["| --- | --- | --- |"]
        rows += [f"| val{i} | val{i} | val{i} |" for i in range(15)]
        body = "## Test Matrix\n\n" + "\n".join(rows)
        findings = tier3.check_3_2(body)
        self.assertEqual(len([f for f in findings if f.check_id == "3.2"]), 0)

    def test_check_3_2_flags_table_under_generic_heading(self) -> None:
        """Tables under generic headings should still be flagged."""
        rows = ["| Col A | Col B | Col C |"] + ["| --- | --- | --- |"]
        rows += [f"| val{i} | val{i} | val{i} |" for i in range(15)]
        body = "## Notes\n\n" + "\n".join(rows)
        findings = tier3.check_3_2(body)
        self.assertTrue(any(f.check_id == "3.2" for f in findings))

    # --- 3.4: contrast pair detection ---

    def test_check_3_4_skips_wrong_correct_pair(self) -> None:
        """WRONG/CORRECT comparison blocks should not be flagged as duplicates."""
        block = "\n".join(f"  echo step{i}" for i in range(6))
        body = (
            "### Wrong\n"
            f"```bash\n{block}\n```\n\n"
            "### Correct\n"
            f"```bash\n{block}\n```"
        )
        findings = tier3.check_3_4(body)
        self.assertEqual(len([f for f in findings if f.check_id == "3.4"]), 0)

    def test_check_3_4_skips_similar_rest_endpoint_blocks(self) -> None:
        """REST endpoint blocks with similar curl structures should not fire."""
        body = (
            "```\ncurl -X POST https://api.example.com/users \\\n"
            "  -H 'Content-Type: application/json' \\\n"
            "  -d '{\"name\": \"alice\"}'\n"
            "more lines\nmore lines\nmore lines\n```\n\n"
            "```\ncurl -X POST https://api.example.com/orders \\\n"
            "  -H 'Content-Type: application/json' \\\n"
            "  -d '{\"item\": \"widget\"}'\n"
            "more lines\nmore lines\nmore lines\n```"
        )
        findings = tier3.check_3_4(body)
        self.assertEqual(len([f for f in findings if f.check_id == "3.4"]), 0)


class Tier3MCPNamespaceTests(unittest.TestCase):
    """Tests for the namespace-aware MCP scanner (check 3.7)."""

    def _make_skill_dir(self, script_content: str | None = None) -> str:
        root = Path(tempfile.mkdtemp())
        skill_dir = root / "demo"
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        if script_content is not None:
            (scripts_dir / "runner.py").write_text(
                f'if __name__ == "__main__":\n    print("{script_content}")\n',
                encoding="utf-8",
            )
        return str(skill_dir)

    def test_check_3_7_allows_figma_namespace(self) -> None:
        skill_dir = self._make_skill_dir()
        findings = tier3.check_3_7("Use mcp__figma_get_design_context to read the frame.", skill_dir)
        self.assertEqual([f for f in findings if f.check_id == "3.7"], [])

    def test_check_3_7_allows_slack_namespace(self) -> None:
        skill_dir = self._make_skill_dir()
        findings = tier3.check_3_7("Use mcp__slack_post_message to notify the channel.", skill_dir)
        self.assertEqual([f for f in findings if f.check_id == "3.7"], [])

    def test_check_3_7_errors_on_github_namespace(self) -> None:
        skill_dir = self._make_skill_dir()
        findings = tier3.check_3_7("Use mcp__github_get_pull_request to fetch the PR.", skill_dir)
        mcp_findings = [f for f in findings if f.check_id == "3.7"]
        self.assertTrue(len(mcp_findings) > 0)
        self.assertEqual(mcp_findings[0].severity, "ERROR")
        self.assertIn("gh", mcp_findings[0].message)

    def test_check_3_7_errors_on_atlassian_namespace(self) -> None:
        skill_dir = self._make_skill_dir()
        findings = tier3.check_3_7("Run mcp__atlassian_get_jira_issue with the ticket key.", skill_dir)
        mcp_findings = [f for f in findings if f.check_id == "3.7"]
        self.assertTrue(len(mcp_findings) > 0)
        self.assertEqual(mcp_findings[0].severity, "ERROR")
        self.assertIn("acli", mcp_findings[0].message)

    def test_check_3_7_warns_on_unknown_namespace(self) -> None:
        skill_dir = self._make_skill_dir()
        findings = tier3.check_3_7("Use mcp__datadog_get_metrics to check monitoring.", skill_dir)
        mcp_findings = [f for f in findings if f.check_id == "3.7"]
        self.assertTrue(len(mcp_findings) > 0)
        self.assertEqual(mcp_findings[0].severity, "WARN")

    def test_check_3_7_warns_on_generic_mcp_prose(self) -> None:
        skill_dir = self._make_skill_dir()
        findings = tier3.check_3_7("Use the MCP server to interact with the service.", skill_dir)
        mcp_findings = [f for f in findings if f.check_id == "3.7"]
        self.assertTrue(len(mcp_findings) > 0)
        self.assertEqual(mcp_findings[0].severity, "WARN")

    def test_check_3_7_suppresses_when_negated(self) -> None:
        skill_dir = self._make_skill_dir()
        findings = tier3.check_3_7("Do not use mcp__github_get_pull_request. Use gh instead.", skill_dir)
        self.assertEqual([f for f in findings if f.check_id == "3.7"], [])

    def test_check_3_7_scans_entrypoint_scripts(self) -> None:
        skill_dir = self._make_skill_dir("mcp__github_get_pull_request")
        findings = tier3.check_3_7("Use scripts when needed.", skill_dir)
        mcp_findings = [f for f in findings if f.check_id == "3.7"]
        self.assertTrue(len(mcp_findings) > 0)
        self.assertEqual(mcp_findings[0].severity, "ERROR")
        self.assertIn("runner.py", mcp_findings[0].message)


if __name__ == "__main__":
    unittest.main()
