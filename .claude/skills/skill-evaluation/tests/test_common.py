from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import common  # noqa: E402


class CommonTests(unittest.TestCase):
    def test_parse_frontmatter_extracts_mapping_and_body(self) -> None:
        fm, body = common.parse_frontmatter("---\nname: demo\n---\n\nHello")
        self.assertEqual(fm["name"], "demo")
        self.assertEqual(body, "Hello")


if __name__ == "__main__":
    unittest.main()
