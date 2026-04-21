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
        self.assertIn("findings", payload)
        self.assertIn("error_findings", payload["findings"])
        self.assertIn("warning_findings", payload["findings"])
        self.assertIn("total", payload["findings"])
        self.assertIn("checks_overview", payload)
        self.assertIn("checks_total", payload["checks_overview"])
        self.assertIn("checks_passed", payload["checks_overview"])
        self.assertIn("checks_failed", payload["checks_overview"])
        self.assertIn("metadata", payload)
        self.assertIn("skill_content", payload)
        self.assertIn("supporting_context", payload)
        self.assertIn("overall_tier", payload)
        self.assertIsInstance(payload["findings"]["error_findings"], list)
        self.assertIsInstance(payload["findings"]["warning_findings"], list)
        self.assertIsInstance(payload["metadata"]["script_types"], list)

    def test_ci_mode_includes_core_evaluation_fields(self) -> None:
        skill_dir = self.make_skill_dir("Use when validating a skill package.")
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH), str(skill_dir), "--ci"],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(completed.stdout)
        self.assertIn("overall_score", payload)
        self.assertIn("summary", payload)
        self.assertIn("file_count", payload["metadata"])
        self.assertIn("line_count", payload["metadata"])
        # skill_description and skill_compatibility removed — not evaluation data
        self.assertNotIn("skill_description", payload)
        self.assertNotIn("skill_compatibility", payload)

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
        all_items = payload["findings"]["error_findings"] + payload["findings"]["warning_findings"]
        runtime_issues = [item for item in all_items if item["rule_id"] == "1.11"]
        self.assertTrue(runtime_issues, all_items)
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

    def test_ci_json_has_no_old_format_keys(self) -> None:
        """Ensure the new JSON output doesn't contain old format keys."""
        skill_dir = self.make_skill_dir("Use when validating a skill package.")
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH), str(skill_dir), "--ci"],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(completed.stdout)
        self.assertNotIn("deterministic", payload)
        self.assertNotIn("llm_analysis", payload)
        self.assertNotIn("quality_tier", payload)
        self.assertNotIn("skill_description", payload)
        self.assertNotIn("skill_compatibility", payload)
        self.assertNotIn("total_findings", payload)
        self.assertNotIn("error_findings", payload)
        self.assertNotIn("warning_findings", payload)
        self.assertNotIn("checks_passed", payload)
        self.assertNotIn("checks_total", payload)

    def test_ci_json_findings_counts_are_consistent(self) -> None:
        """Verify that findings.total == len(errors) + len(warnings)."""
        skill_dir = self.make_skill_dir("Use when validating a skill package.")
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH), str(skill_dir), "--ci"],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(completed.stdout)
        findings = payload["findings"]
        error_count = len(findings["error_findings"])
        warning_count = len(findings["warning_findings"])
        self.assertEqual(findings["total"], error_count + warning_count)

    def test_ci_json_checks_passed_plus_findings_equals_total(self) -> None:
        """Verify checks_passed + checks_failed == checks_total."""
        skill_dir = self.make_skill_dir("Use when validating a skill package.")
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH), str(skill_dir), "--ci"],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(completed.stdout)
        overview = payload["checks_overview"]
        self.assertEqual(
            overview["checks_passed"] + overview["checks_failed"],
            overview["checks_total"],
        )

    def test_ci_json_rule_ids_are_numeric(self) -> None:
        """Verify all rule_ids are numeric format (e.g. '1.3', '4.6')."""
        skill_dir = self.make_skill_dir("Use when validating a skill package.")
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH), str(skill_dir), "--ci"],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(completed.stdout)
        findings = payload["findings"]
        all_items = findings["error_findings"] + findings["warning_findings"]
        import re
        for item in all_items:
            self.assertRegex(item["rule_id"], r"^\d+\.\d+$", f"rule_id {item['rule_id']!r} is not numeric")
            self.assertIn("message", item)
            self.assertIn("reason", item)

    def test_ci_json_with_errors_has_correct_structure(self) -> None:
        """Test that a skill with errors produces correctly grouped findings."""
        # Write directly to include a non-standard field in frontmatter
        # Use an unknown field (not a CC extension) to trigger an ERROR
        tmp = Path(tempfile.mkdtemp())
        skill_dir = tmp / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Use when testing.\ncustom_field: value\n---\n\nUse when testing.\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH), str(skill_dir), "--ci"],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(completed.stdout)
        error_findings = payload["findings"]["error_findings"]
        self.assertGreater(len(error_findings), 0)
        error_item = error_findings[0]
        self.assertIn("rule_id", error_item)
        self.assertIn("message", error_item)
        self.assertIn("reason", error_item)
        # severity is not a per-item field (items are grouped by severity)
        self.assertNotIn("severity", error_item)


# Import individual check functions for targeted testing
from tier4 import check_4_3, check_4_5, check_4_6, check_4_8
from tier1 import check_1_7, check_1_8, check_1_10
from tier2 import check_2_4
from tier3 import check_3_1
from common import diagnose_frontmatter_failure, parse_frontmatter
from extract import _is_library_or_test_file


class Test43ContextAwareNegatives(unittest.TestCase):
    """Tests for CRITICAL #1: 4.3 false positive reduction."""

    def test_flags_true_negative_only(self) -> None:
        """A genuine negative-only instruction without positive alternative."""
        body = "Don't do that."
        findings = check_4_3(body)
        self.assertTrue(any(f.check_id == "4.3" for f in findings))

    def test_skips_negative_with_positive_alternative(self) -> None:
        """When a positive alternative is nearby, don't flag."""
        body = "Don't use rm. Use trash-cli instead."
        findings = check_4_3(body)
        self.assertEqual([f for f in findings if f.check_id == "4.3"], [])

    def test_skips_inside_fenced_code_block(self) -> None:
        """Lines inside code blocks should not be flagged."""
        body = "Some text.\n```\n// DO NOT DO THIS - defeats parallelization\n```\nMore text."
        findings = check_4_3(body)
        self.assertEqual([f for f in findings if f.check_id == "4.3"], [])

    def test_skips_explanatory_negative(self) -> None:
        """Third-person explanatory uses like 'things that don't need' should not be flagged."""
        body = "Some tasks that don't need built output from dependencies."
        findings = check_4_3(body)
        self.assertEqual([f for f in findings if f.check_id == "4.3"], [])

    def test_skips_security_prohibition(self) -> None:
        """Security prohibitions like 'never expose tokens' don't need alternatives."""
        body = "Never expose API tokens in logs."
        findings = check_4_3(body)
        self.assertEqual([f for f in findings if f.check_id == "4.3"], [])

    def test_skips_bold_section_header(self) -> None:
        """Bold-only section headers like '**Do NOT:**' should not be flagged."""
        body = "**Do NOT:**"
        findings = check_4_3(body)
        self.assertEqual([f for f in findings if f.check_id == "4.3"], [])

    def test_wider_context_window_catches_alternative(self) -> None:
        """Positive alternative 4 lines away should still be found."""
        body = "Don't hard-code values.\n\n\n\nAlways use configuration files instead."
        findings = check_4_3(body)
        self.assertEqual([f for f in findings if f.check_id == "4.3"], [])

    def test_wrong_correct_pattern(self) -> None:
        """'WRONG: ... CORRECT: ...' patterns should not be flagged."""
        body = "Don't do X.\n\n// CORRECT - use Y\nUse Y to handle this."
        findings = check_4_3(body)
        self.assertEqual([f for f in findings if f.check_id == "4.3"], [])


class Test48MinimumContent(unittest.TestCase):
    """Tests for CRITICAL #2: minimum content/substance gate."""

    def test_flags_redirect_skill(self) -> None:
        """A redirect skill with few lines should get an ERROR."""
        body = "Read the following documentation:\n1. See docs at /path/to/docs"
        findings = check_4_8(body, has_scripts=False)
        self.assertTrue(any(f.check_id == "4.8" and f.severity == "ERROR" for f in findings))

    def test_flags_thin_skill_without_scripts(self) -> None:
        """A very short skill body with no scripts gets a WARN."""
        body = "Do the thing.\nThen do another thing."
        findings = check_4_8(body, has_scripts=False)
        self.assertTrue(any(f.check_id == "4.8" and f.severity == "WARN" for f in findings))

    def test_passes_sufficient_content(self) -> None:
        """A skill with enough non-blank lines should pass."""
        body = "\n".join(f"Step {i}: Do something important." for i in range(15))
        findings = check_4_8(body, has_scripts=False)
        self.assertEqual(findings, [])

    def test_thin_skill_with_scripts_not_flagged(self) -> None:
        """A short body with scripts should not be flagged (scripts handle the logic)."""
        body = "Run the helper script.\nCheck the output."
        findings = check_4_8(body, has_scripts=True)
        self.assertEqual(findings, [])


class Test11FrontmatterDiagnostics(unittest.TestCase):
    """Tests for CRITICAL #3: specific frontmatter parse error messages."""

    def test_no_delimiters(self) -> None:
        msg = diagnose_frontmatter_failure("# Just a heading\nSome content.")
        self.assertIn("no YAML frontmatter delimiters", msg)

    def test_encoding_artifacts(self) -> None:
        msg = diagnose_frontmatter_failure("§---\nname: test\n---\nBody.")
        self.assertIn("encoding artifacts", msg)

    def test_no_closing_delimiter(self) -> None:
        msg = diagnose_frontmatter_failure("---\nname: test\nSome content without closing.")
        self.assertIn("no closing", msg)

    def test_empty_file(self) -> None:
        msg = diagnose_frontmatter_failure("")
        self.assertIn("empty", msg)


class Test45IdempotentOperations(unittest.TestCase):
    """Tests for CRITICAL #4: 4.5 false positive reduction."""

    def test_skips_headings(self) -> None:
        """Headings like '# GH PR Create' should not be flagged."""
        body = "# GH PR Create\n\nCreate a PR with proper linking."
        findings = check_4_5(body)
        self.assertEqual([f for f in findings if f.check_id == "4.5"], [])

    def test_flags_unguarded_create(self) -> None:
        """An actual gh pr create without guard should be flagged."""
        body = "Run:\ngh pr create --title 'fix'"
        findings = check_4_5(body)
        self.assertTrue(any(f.check_id == "4.5" for f in findings))

    def test_skips_guarded_by_check(self) -> None:
        """Operations with idempotency guards should not be flagged."""
        body = "Check if PR already exists.\nIf not, run gh pr create --title 'fix'."
        findings = check_4_5(body)
        self.assertEqual([f for f in findings if f.check_id == "4.5"], [])

    def test_search_post_not_flagged(self) -> None:
        """curl POST for search operations should not be flagged (idempotent)."""
        body = "Use search endpoint:\ncurl -X POST /api/search -d '{\"query\": \"test\"}'"
        findings = check_4_5(body)
        self.assertEqual([f for f in findings if f.check_id == "4.5"], [])


class Test18LibraryTestFiles(unittest.TestCase):
    """Tests for CRITICAL #5: 1.8 should skip library/test files."""

    def _make_skill_with_scripts(self, scripts: dict[str, str]) -> Path:
        tmp = Path(tempfile.mkdtemp())
        skill_dir = tmp / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Use when testing.\n---\n\nUse when testing.\n",
            encoding="utf-8",
        )
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        for name, content in scripts.items():
            (scripts_dir / name).write_text(content, encoding="utf-8")
        return skill_dir

    def test_skips_underscore_prefix_library(self) -> None:
        """_common.sh should not be flagged for missing --help."""
        skill_dir = self._make_skill_with_scripts({
            "_common.sh": "#!/bin/bash\nlog() { echo \"$1\"; }\n",
            "main.sh": "#!/bin/bash\nsource _common.sh\necho --help\n",
        })
        findings = check_1_8(str(skill_dir))
        flagged = [f.message for f in findings if "_common.sh" in f.message]
        self.assertEqual(flagged, [])

    def test_skips_test_file(self) -> None:
        """test_foo.py should not be flagged for missing --help."""
        self.assertTrue(_is_library_or_test_file(Path("test_helper.py")))
        self.assertTrue(_is_library_or_test_file(Path("helper_test.py")))
        self.assertTrue(_is_library_or_test_file(Path("_common.sh")))
        self.assertFalse(_is_library_or_test_file(Path("main.py")))
        self.assertFalse(_is_library_or_test_file(Path("deploy.sh")))


class Test110NonInteractiveFallback(unittest.TestCase):
    """Tests for CRITICAL #6: 1.10 should recognize non-interactive fallbacks."""

    def _make_skill_with_script(self, content: str) -> Path:
        tmp = Path(tempfile.mkdtemp())
        skill_dir = tmp / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Use when testing.\n---\n\nUse when testing.\n",
            encoding="utf-8",
        )
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "run.sh").write_text(content, encoding="utf-8")
        return skill_dir

    def test_flags_plain_read_p(self) -> None:
        """A plain read -p without fallback should be flagged."""
        skill_dir = self._make_skill_with_script("#!/bin/bash\nread -p 'Enter value: ' VAL\n")
        findings = check_1_10(str(skill_dir))
        self.assertTrue(any(f.check_id == "1.10" for f in findings))

    def test_skips_read_p_with_tty_check(self) -> None:
        """read -p guarded by [[ -t 0 ]] should not be flagged."""
        skill_dir = self._make_skill_with_script(
            "#!/bin/bash\nif [[ -t 0 ]]; then\n  read -p 'Enter value: ' VAL\nelse\n  VAL=\"default\"\nfi\n"
        )
        findings = check_1_10(str(skill_dir))
        self.assertEqual([f for f in findings if f.check_id == "1.10"], [])


class Test46SuccessCriteria(unittest.TestCase):
    """Tests for HIGH #7: broader success criteria detection."""

    def test_matches_output_heading(self) -> None:
        body = "## Process\n\nDo things.\n\n## Output\n\nReturn JSON."
        findings = check_4_6(body)
        self.assertEqual(findings, [])

    def test_matches_present_results_heading(self) -> None:
        body = "## Steps\n\n1. Analyze.\n\n## Present Results\n\nShow summary."
        findings = check_4_6(body)
        self.assertEqual(findings, [])

    def test_matches_return_result_heading(self) -> None:
        body = "## Steps\n\n1. Analyze.\n\n### Step 10: Return Result\n\nReturn the PR URL."
        findings = check_4_6(body)
        self.assertEqual(findings, [])

    def test_matches_checklist_heading(self) -> None:
        body = "## Steps\n\n1. Do thing.\n\n## Checklist\n\n- [ ] Verified."
        findings = check_4_6(body)
        self.assertEqual(findings, [])

    def test_matches_summary_heading(self) -> None:
        body = "## Process\n\n1. Review.\n\n## Summary\n\nProvide a summary."
        findings = check_4_6(body)
        self.assertEqual(findings, [])

    def test_flags_no_criteria(self) -> None:
        body = "## Process\n\n1. Read.\n2. Review.\n3. Continue."
        findings = check_4_6(body)
        self.assertTrue(any(f.check_id == "4.6" for f in findings))


class Test17TestFileRecognition(unittest.TestCase):
    """Tests for HIGH #9: 1.7 should recognize test files."""

    def test_skips_test_prefixed_files(self) -> None:
        """_test.py, test_*.py should not need their own test files."""
        tmp = Path(tempfile.mkdtemp())
        skill_dir = tmp / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Use when testing.\n---\n\nTest.\n",
            encoding="utf-8",
        )
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "_format.py").write_text("def fmt(): pass\n", encoding="utf-8")
        (scripts_dir / "_format_test.py").write_text("import unittest\n", encoding="utf-8")
        findings = check_1_7(str(skill_dir))
        # Neither _format.py nor _format_test.py should be flagged
        flagged_names = [f.message for f in findings]
        self.assertFalse(any("_format.py" in m for m in flagged_names), flagged_names)
        self.assertFalse(any("_format_test.py" in m for m in flagged_names), flagged_names)


class Test24HardcodedPaths(unittest.TestCase):
    """Tests for HIGH #10: detect hardcoded user paths."""

    def test_flags_macos_user_path(self) -> None:
        body = "Config is at /Users/takuya.uchida/projects/myapp/config.json"
        findings = check_2_4(body, tempfile.mkdtemp())
        self.assertTrue(any(f.check_id == "2.4" for f in findings))

    def test_flags_linux_home_path(self) -> None:
        body = "Install to /home/developer/.local/bin"
        findings = check_2_4(body, tempfile.mkdtemp())
        self.assertTrue(any(f.check_id == "2.4" for f in findings))

    def test_passes_generic_paths(self) -> None:
        body = "Config is at $HOME/.config/app/settings.json"
        findings = check_2_4(body, tempfile.mkdtemp())
        self.assertEqual(findings, [])

    def test_passes_tilde_path(self) -> None:
        body = "Config is at ~/.config/app/settings.json"
        findings = check_2_4(body, tempfile.mkdtemp())
        self.assertEqual(findings, [])


class Test31DocumentationExamples(unittest.TestCase):
    """Tests for HIGH #8: 3.1 should not flag documentation blocks."""

    def test_skips_json_code_blocks(self) -> None:
        """JSON code blocks are documentation, not executable."""
        body = "Example config:\n```json\n" + "".join(f'"key{i}": "val{i}",\n' for i in range(8)) + "```"
        findings = check_3_1(body)
        self.assertEqual([f for f in findings if f.check_id == "3.1"], [])

    def test_skips_yaml_code_blocks(self) -> None:
        """YAML code blocks are documentation, not executable."""
        body = "Example manifest:\n```yaml\n" + "".join(f"key{i}: val{i}\n" for i in range(8)) + "```"
        findings = check_3_1(body)
        self.assertEqual([f for f in findings if f.check_id == "3.1"], [])

    def test_skips_template_blocks(self) -> None:
        """Blocks with template indicators should be skipped."""
        lines = ["### Problem statement", "(describe the problem)", "### Key sources",
                 "[link to docs]", "### Expected outcome", "(describe what success looks like)",
                 "- [ ] Step 1 complete", "- [ ] Step 2 complete"]
        body = "Template:\n```\n" + "\n".join(lines) + "\n```"
        findings = check_3_1(body)
        self.assertEqual([f for f in findings if f.check_id == "3.1"], [])

    def test_flags_long_bash_block(self) -> None:
        """Genuinely long executable bash blocks should still be flagged."""
        body = "Run this:\n```bash\n" + "".join(f"echo step{i}\n" for i in range(8)) + "```"
        findings = check_3_1(body)
        self.assertTrue(any(f.check_id == "3.1" for f in findings))


if __name__ == "__main__":
    unittest.main()
