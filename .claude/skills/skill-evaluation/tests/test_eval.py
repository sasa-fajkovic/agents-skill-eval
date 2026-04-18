from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


def _safe_load(text: str):
    data = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"\'')
    return data


sys.modules.setdefault("yaml", SimpleNamespace(safe_load=_safe_load, YAMLError=Exception))


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "eval.py"
SPEC = importlib.util.spec_from_file_location("skill_eval", MODULE_PATH)
skill_eval = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(skill_eval)


class EvalMCPTests(unittest.TestCase):
    def make_skill_dir(self, body: str, script_content: str | None = None) -> Path:
        tmp = Path(tempfile.mkdtemp())
        skill_dir = tmp / "skill-eval"
        skill_dir.mkdir()
        skill_text = """---
name: skill-eval
description: Evaluate skills. Use when validating a skill package.
---

""" + body
        (skill_dir / "SKILL.md").write_text(skill_text, encoding="utf-8")
        if script_content is not None:
            scripts_dir = skill_dir / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / "helper.py").write_text(script_content, encoding="utf-8")
        return skill_dir

    def test_flags_mcp_instruction_in_skill_body(self) -> None:
        skill_dir = self.make_skill_dir("Use GitHub MCP to inspect pull requests.")
        findings = skill_eval.evaluate(str(skill_dir))
        messages = [str(f) for f in findings if f.check_id == "2.3"]
        self.assertTrue(any("GitHub MCP" in message for message in messages), messages)

    def test_allows_explicit_mcp_prohibition(self) -> None:
        skill_dir = self.make_skill_dir("Do not use MCP servers. Use gh instead.")
        findings = skill_eval.evaluate(str(skill_dir))
        messages = [str(f) for f in findings if f.check_id == "2.3"]
        self.assertEqual(messages, [])

    def test_flags_mcp_reference_in_script(self) -> None:
        skill_dir = self.make_skill_dir(
            "Use scripts when needed.",
            "#!/usr/bin/env python3\nprint('call mcp__github__pull_request_read')\n",
        )
        findings = skill_eval.evaluate(str(skill_dir))
        messages = [str(f) for f in findings if f.check_id == "2.3"]
        self.assertTrue(any("scripts/helper.py" in message for message in messages), messages)


if __name__ == "__main__":
    unittest.main()
