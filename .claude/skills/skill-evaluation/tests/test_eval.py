from __future__ import annotations

import importlib.util
import json
import subprocess
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
if str(MODULE_PATH.parent) not in sys.path:
    sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("skill_eval", MODULE_PATH)
skill_eval = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(skill_eval)


class EvalMCPTests(unittest.TestCase):
    def make_skill_dir(self, body: str, script_content: str | None = None, script_name: str = "helper.py") -> Path:
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
            (scripts_dir / script_name).write_text(script_content, encoding="utf-8")
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
            "#!/usr/bin/env python3\nif __name__ == \"__main__\":\n    print('call mcp__github__pull_request_read')\n",
        )
        findings = skill_eval.evaluate(str(skill_dir))
        messages = [str(f) for f in findings if f.check_id == "2.3"]
        self.assertTrue(any("scripts/helper.py" in message for message in messages), messages)

    def test_ignores_mcp_reference_in_non_entrypoint_helper_module(self) -> None:
        skill_dir = self.make_skill_dir(
            "Use scripts when needed.",
            "PATTERN = 'mcp__github__pull_request_read'\n",
        )
        findings = skill_eval.evaluate(str(skill_dir))
        messages = [str(f) for f in findings if f.check_id == "2.3"]
        self.assertEqual(messages, [])

    def test_ci_mode_emits_single_json_object(self) -> None:
        skill_dir = self.make_skill_dir("Use when validating a skill package.")
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH), str(skill_dir), "--ci"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.stderr, "")
        self.assertIn(completed.returncode, (0, 1, 2))
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["skill_name"], "skill-eval")
        self.assertIn("deterministic", payload)
        self.assertIn("llm_analysis", payload)
        self.assertIn("metadata", payload)
        self.assertIn("skill_content", payload)
        self.assertIn("supporting_context", payload)
        self.assertIn("overall_tier", payload)
        self.assertIsInstance(payload["deterministic"]["issues"], list)
        self.assertIsInstance(payload["llm_analysis"]["strengths"], list)
        self.assertIsInstance(payload["metadata"]["script_types"], list)

    def test_ci_mode_includes_app_compatibility_fields(self) -> None:
        skill_dir = self.make_skill_dir("Use when validating a skill package.")
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH), str(skill_dir), "--ci"],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(completed.stdout)
        self.assertIn("skill_description", payload)
        self.assertIn("skill_compatibility", payload)
        self.assertIn("overall_score", payload)
        self.assertIn("summary", payload)
        self.assertIn("file_count", payload["deterministic"])
        self.assertIn("line_count", payload["deterministic"])

    def test_discouraged_runtime_is_soft_warning(self) -> None:
        skill_dir = self.make_skill_dir(
            "Use when validating a skill package.",
            "console.log('hello')\n",
            script_name="helper.js",
        )
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH), str(skill_dir), "--ci"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertIn(completed.returncode, (1, 2))
        payload = json.loads(completed.stdout)
        issues = payload["deterministic"]["issues"]
        runtime_issues = [issue for issue in issues if issue["rule_id"] == "runtime_dependency_required"]
        self.assertTrue(runtime_issues, issues)
        self.assertEqual(runtime_issues[0]["severity"], "warning")
        self.assertIn(".js", payload["metadata"]["unsupported_script_types"])

    def test_flags_long_inline_code_as_token_warning(self) -> None:
        body = """Use when validating a skill package.

```bash
echo one
echo two
echo three
echo four
echo five
echo six
```
"""
        skill_dir = self.make_skill_dir(body)
        findings = skill_eval.evaluate(str(skill_dir))
        self.assertTrue(any(f.check_id == "3.1" for f in findings), findings)

    def test_flags_missing_success_criteria(self) -> None:
        body = """Use when validating a skill package.

## Process

1. Read the skill.
2. Review it carefully.
3. Continue as needed.
"""
        skill_dir = self.make_skill_dir(body)
        findings = skill_eval.evaluate(str(skill_dir))
        self.assertTrue(any(f.check_id == "4.6" for f in findings), findings)

    def test_flags_argument_without_default_behavior(self) -> None:
        body = """Use when validating a skill package.

## Input

- `--target`

## Process

Run the script with `--target` and summarize the result.

## Output

Return a short summary.
"""
        skill_dir = self.make_skill_dir(body)
        findings = skill_eval.evaluate(str(skill_dir))
        self.assertTrue(any(f.check_id == "4.4" for f in findings), findings)


if __name__ == "__main__":
    unittest.main()
