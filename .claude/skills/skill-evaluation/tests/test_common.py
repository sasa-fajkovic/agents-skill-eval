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

    def test_parse_frontmatter_preserves_real_yaml(self) -> None:
        text = """---
name: demo
description: >-
  Use when parsing quoted values:
  preserve a # character.
license: "MIT: custom # note"
metadata:
  author: example-org
  version: "1.0"
  note: "a: b # c"
allowed-tools: Bash(git:*) Read
---
Body
"""
        fm, body = common.parse_frontmatter(text)
        self.assertEqual(fm["description"], "Use when parsing quoted values: preserve a # character.")
        self.assertEqual(fm["license"], "MIT: custom # note")
        self.assertEqual(fm["metadata"], {"author": "example-org", "version": "1.0", "note": "a: b # c"})
        self.assertEqual(body, "Body")

    def test_parse_frontmatter_rejects_invalid_yaml_and_non_mappings(self) -> None:
        for content in ("name: [unclosed", "? [a, b]\n: value", "- name: demo", "just a string", "!!python/object/apply:os.system ['echo unsafe']"):
            with self.subTest(content=content):
                text = f"---\n{content}\n---\nBody"
                fm, body = common.parse_frontmatter(text)
                self.assertIsNone(fm)
                self.assertEqual(body, "Body")
                self.assertTrue(common.diagnose_frontmatter_failure(text))


if __name__ == "__main__":
    unittest.main()
