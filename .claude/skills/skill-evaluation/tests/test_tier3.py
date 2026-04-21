from __future__ import annotations

import sys
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


if __name__ == "__main__":
    unittest.main()
