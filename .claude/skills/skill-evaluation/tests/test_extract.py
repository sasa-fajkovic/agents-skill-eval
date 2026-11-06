from __future__ import annotations

import sys
import json
import os
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import extract  # noqa: E402
from output_evals import MAX_INPUT_BYTES


class ExtractTests(unittest.TestCase):
    def test_declared_inputs_outside_evals_are_omitted_even_from_invalid_cases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "SKILL.md"
            skill.write_text("# Example", encoding="utf-8")
            (root / "evals").mkdir()
            (root / "references").mkdir()
            (root / "references/input.md").write_text("PRIVATE_INPUT_CONTENT", encoding="utf-8")
            (root / "references/guide.md").write_text("Public guidance", encoding="utf-8")
            (root / "evals/evals.json").write_text(json.dumps({"evals": [
                {"files": ["references/input.md", "../outside.md", None]},
            ]}), encoding="utf-8")
            context = extract.collect_supporting_context(skill)
            self.assertNotIn("PRIVATE_INPUT_CONTENT", context)
            self.assertNotIn("references/input.md", context)
            self.assertIn("Public guidance", context)

    def test_supporting_reads_and_scripts_enforce_limits_and_symlink_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skill"
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            skill = root / "SKILL.md"
            skill.write_text("# Example", encoding="utf-8")
            outside = root.parent / "outside.py"
            outside.write_text('if __name__ == "__main__":\n    print("PRIVATE_EXTERNAL_CONTENT")', encoding="utf-8")
            (scripts / "linked.py").symlink_to(outside)
            oversized = scripts / "large.py"
            with oversized.open("wb") as stream:
                stream.write(b'if __name__ == "__main__":\n    print("PRIVATE_LARGE_CONTENT")')
                stream.truncate(MAX_INPUT_BYTES + 1)
            context = extract.collect_supporting_context(skill)
            self.assertNotIn("PRIVATE_EXTERNAL_CONTENT", context)
            self.assertNotIn("PRIVATE_LARGE_CONTENT", context)
            self.assertEqual(extract.entrypoint_scripts(str(root)), [])
            for candidate in (scripts / "linked.py", oversized):
                with self.assertRaises(OSError):
                    extract.read_text_file(candidate)

    def test_declared_input_hardlinks_and_query_fixtures_are_omitted(self) -> None:
        from trigger_evals import QUERY_FILENAMES

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "SKILL.md"
            skill.write_text("# Example", encoding="utf-8")
            (root / "evals").mkdir()
            source = root / "private.md"
            source.write_text("PRIVATE_INPUT_CANARY", encoding="utf-8")
            os.link(source, root / "alias.md")
            files = ["private.md"]
            for parent in (root, root / "evals"):
                for name in QUERY_FILENAMES:
                    path = parent / name
                    path.write_text("PRIVATE_QUERY_CANARY", encoding="utf-8")
                    files.append(path.relative_to(root).as_posix())
            (root / "evals/evals.json").write_text(json.dumps({"evals": [{"files": files}]}), encoding="utf-8")
            context = extract.collect_supporting_context(skill)
            self.assertNotIn("PRIVATE_", context)
            self.assertNotIn("alias.md", context)
            self.assertNotIn("private.md", context)
            self.assertFalse(any(name in context for name in QUERY_FILENAMES))

    def test_case_variant_input_omission_follows_filesystem_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "SKILL.md"
            skill.write_text("# Example", encoding="utf-8")
            (root / "evals").mkdir()
            (root / "references").mkdir()
            source = root / "references/input.md"
            source.write_text("PRIVATE_INPUT_CANARY", encoding="utf-8")
            alias = root / "References/Input.md"
            if not alias.exists():
                alias.parent.mkdir()
                alias.write_text("PUBLIC_DISTINCT_CASE_FILE", encoding="utf-8")
                declared = "references/input.md"
            else:
                declared = "References/Input.md"
            (root / "evals/evals.json").write_text(json.dumps({"evals": [{"files": [declared]}]}), encoding="utf-8")
            context = extract.collect_supporting_context(skill)
            self.assertNotIn("PRIVATE_INPUT_CANARY", context)
            if not source.samefile(alias):
                self.assertIn("PUBLIC_DISTINCT_CASE_FILE", context)

    def test_declared_script_still_receives_security_analysis(self) -> None:
        import tier1
        import tier3

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "SKILL.md"
            skill.write_text("# Example", encoding="utf-8")
            (root / "evals").mkdir()
            (root / "scripts").mkdir()
            (root / "scripts/run.py").write_text('if __name__ == "__main__":\n    print("mcp__github_pull_request_read")\n', encoding="utf-8")
            (root / "evals/evals.json").write_text(json.dumps({"evals": [{"files": ["scripts/run.py"]}]}), encoding="utf-8")
            self.assertNotIn("mcp__github", extract.collect_supporting_context(skill))
            self.assertTrue(tier1.check_1_8(str(root)))
            self.assertTrue(tier3.check_3_7("", str(root)))

    def test_pdf_text_is_included_in_supporting_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_path = root / "SKILL.md"
            skill_path.write_text("# Example", encoding="utf-8")
            pdf_path = root / "reference.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(width=612, height=792)
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
            })
            content = DecodedStreamObject()
            content.set_data(b"BT /F1 12 Tf 72 720 Td (Dependency migration PDF text) Tj ET")
            page[NameObject("/Contents")] = content
            writer.write(pdf_path)

            self.assertIn("Dependency migration PDF text", extract.read_pdf_file(pdf_path))
            context = extract.collect_supporting_context(skill_path)
            self.assertIn("--- FILE: reference.pdf ---", context)
            self.assertIn("Dependency migration PDF text", context)

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
