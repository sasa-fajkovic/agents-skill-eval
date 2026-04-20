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
    def test_check_2_3_scans_only_entrypoint_scripts(self) -> None:
        root = Path(tempfile.mkdtemp())
        skill_dir = root / "demo"
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "runner.py").write_text('if __name__ == "__main__":\n    print("mcp__github__thing")\n', encoding="utf-8")
        (scripts_dir / "helper.py").write_text('PATTERN = "mcp__github__thing"\n', encoding="utf-8")

        findings = tier2.check_2_3("Use gh instead.", str(skill_dir))
        self.assertEqual(len([finding for finding in findings if finding.check_id == "2.3"]), 1)
        self.assertIn("runner.py", findings[0].message)

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


if __name__ == "__main__":
    unittest.main()
