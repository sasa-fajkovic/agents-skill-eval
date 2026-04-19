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


if __name__ == "__main__":
    unittest.main()
