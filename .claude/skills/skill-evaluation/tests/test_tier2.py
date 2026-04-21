from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import tier2  # noqa: E402


class Tier2Tests(unittest.TestCase):
    # --- 2.1: context-aware scoping ---

    def test_check_2_1_flags_unscoped_tool_usage(self) -> None:
        body = "Run any necessary commands to check the repo."
        findings = tier2.check_2_1(body)
        self.assertTrue(any(f.check_id == "2.1" for f in findings))

    def test_check_2_1_suppresses_when_scoped_nearby(self) -> None:
        body = "Run any necessary commands.\nOnly use the following: git status, git diff, git log."
        findings = tier2.check_2_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "2.1"]), 0)

    def test_check_2_1_suppresses_when_restriction_nearby(self) -> None:
        body = "Run any necessary commands.\nDo not use git push or git reset."
        findings = tier2.check_2_1(body)
        self.assertEqual(len([f for f in findings if f.check_id == "2.1"]), 0)

    # --- 2.2: replace as destructive ---

    def test_check_2_2_flags_replace_without_safeguard(self) -> None:
        body = "Run `replace config.ini config.ini.new` to swap the config."
        findings = tier2.check_2_2(body)
        self.assertTrue(any(f.check_id == "2.2" for f in findings))

    def test_check_2_2_passes_replace_with_backup(self) -> None:
        body = "Create a backup first.\nRun `replace config.ini config.ini.new` to swap the config."
        findings = tier2.check_2_2(body)
        self.assertEqual(len([f for f in findings if f.check_id == "2.2"]), 0)

    # --- 2.2: safe context detection ---

    def test_check_2_2_skips_rm_of_temp_files(self) -> None:
        body = "Clean up by running `rm /tmp/build-output.tar`."
        findings = tier2.check_2_2(body)
        self.assertEqual(len([f for f in findings if f.check_id == "2.2"]), 0)

    def test_check_2_2_skips_cleanup_context(self) -> None:
        body = "During teardown:\n```\nrm -rf $TMPDIR/scratch\n```"
        findings = tier2.check_2_2(body)
        self.assertEqual(len([f for f in findings if f.check_id == "2.2"]), 0)

    def test_check_2_2_still_flags_dangerous_rm(self) -> None:
        body = "Run `rm -rf /var/data/production` to clear the data."
        findings = tier2.check_2_2(body)
        self.assertTrue(any(f.check_id == "2.2" for f in findings))


if __name__ == "__main__":
    unittest.main()
