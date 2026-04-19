from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import extract  # noqa: E402


class ExtractTests(unittest.TestCase):
    def test_entrypoint_scripts_ignores_helper_module_without_main(self) -> None:
        root = Path(tempfile.mkdtemp())
        skill_dir = root / "demo"
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "runner.py").write_text('if __name__ == "__main__":\n    print("ok")\n', encoding="utf-8")
        (scripts_dir / "helper.py").write_text('def helper():\n    return 1\n', encoding="utf-8")

        displays = [display for _path, display in extract.entrypoint_scripts(str(skill_dir))]
        self.assertEqual(displays, ["scripts/runner.py"])


if __name__ == "__main__":
    unittest.main()
