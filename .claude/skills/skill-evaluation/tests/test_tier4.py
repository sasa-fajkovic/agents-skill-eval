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


if __name__ == "__main__":
    unittest.main()
