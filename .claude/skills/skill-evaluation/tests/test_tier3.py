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


if __name__ == "__main__":
    unittest.main()
