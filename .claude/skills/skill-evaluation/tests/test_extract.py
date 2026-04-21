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

    def test_body_paragraphs_skips_numbered_list_items(self) -> None:
        body = (
            "Some intro text.\n"
            "\n"
            "1. First step in the workflow.\n"
            "2. Second step does something else.\n"
            "3. Third step finalizes.\n"
            "\n"
            "Closing paragraph here.\n"
        )
        paragraphs = extract.body_paragraphs(body)
        texts = [text for _line, text in paragraphs]
        # Numbered items should NOT be merged into a paragraph
        self.assertNotIn(
            "First step in the workflow. Second step does something else. Third step finalizes.",
            texts,
        )
        # But regular prose should still be collected
        self.assertIn("Some intro text.", texts)
        self.assertIn("Closing paragraph here.", texts)

    def test_body_paragraphs_still_collects_regular_prose(self) -> None:
        body = (
            "This is a paragraph that spans\n"
            "multiple lines of regular prose.\n"
        )
        paragraphs = extract.body_paragraphs(body)
        texts = [text for _line, text in paragraphs]
        self.assertEqual(len(texts), 1)
        self.assertIn("multiple lines", texts[0])


if __name__ == "__main__":
    unittest.main()
