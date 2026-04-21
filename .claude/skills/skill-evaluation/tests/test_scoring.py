from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import scoring


class DeterministicScoreTests(unittest.TestCase):
    def test_no_findings(self) -> None:
        self.assertEqual(scoring.deterministic_score(0, 0), 100)

    def test_errors_only(self) -> None:
        # 2 errors * 5 = 10
        self.assertEqual(scoring.deterministic_score(2, 0), 90)

    def test_warnings_only(self) -> None:
        # 3 warnings * 2 = 6
        self.assertEqual(scoring.deterministic_score(0, 3), 94)

    def test_mixed(self) -> None:
        # 1 error * 5 + 2 warnings * 2 = 9
        self.assertEqual(scoring.deterministic_score(1, 2), 91)

    def test_error_cap(self) -> None:
        # 20 errors * 5 = 100 capped at 70
        self.assertEqual(scoring.deterministic_score(20, 0), 30)

    def test_warning_cap(self) -> None:
        # 20 warnings * 2 = 40 capped at 30
        self.assertEqual(scoring.deterministic_score(0, 20), 70)

    def test_floor_at_zero(self) -> None:
        self.assertEqual(scoring.deterministic_score(20, 20), 0)


class TierForScoreTests(unittest.TestCase):
    def test_excellent(self) -> None:
        self.assertEqual(scoring.tier_for_score(95), "excellent")
        self.assertEqual(scoring.tier_for_score(90), "excellent")

    def test_good(self) -> None:
        self.assertEqual(scoring.tier_for_score(89), "good")
        self.assertEqual(scoring.tier_for_score(80), "good")
        self.assertEqual(scoring.tier_for_score(79), "good")
        self.assertEqual(scoring.tier_for_score(70), "good")

    def test_needs_work(self) -> None:
        self.assertEqual(scoring.tier_for_score(69), "needs_work")
        self.assertEqual(scoring.tier_for_score(50), "needs_work")

    def test_poor(self) -> None:
        self.assertEqual(scoring.tier_for_score(49), "poor")
        self.assertEqual(scoring.tier_for_score(0), "poor")


class NormalizeQualityTierTests(unittest.TestCase):
    def test_known_tiers(self) -> None:
        self.assertEqual(scoring.normalize_quality_tier("excellent"), "excellent")
        self.assertEqual(scoring.normalize_quality_tier("good"), "good")
        self.assertEqual(scoring.normalize_quality_tier("needs_work"), "needs_work")
        self.assertEqual(scoring.normalize_quality_tier("poor"), "poor")

    def test_space_variant(self) -> None:
        self.assertEqual(scoring.normalize_quality_tier("needs work"), "needs_work")

    def test_very_good_alias(self) -> None:
        """very_good is not a valid tier — it maps to good."""
        self.assertEqual(scoring.normalize_quality_tier("very_good"), "good")
        self.assertEqual(scoring.normalize_quality_tier("very good"), "good")

    def test_unknown_defaults_to_needs_work(self) -> None:
        self.assertEqual(scoring.normalize_quality_tier("mediocre"), "needs_work")
        self.assertEqual(scoring.normalize_quality_tier(""), "needs_work")


class QualityTierBaseScoreTests(unittest.TestCase):
    def test_all_tiers(self) -> None:
        self.assertEqual(scoring.quality_tier_base_score("excellent"), 95)
        self.assertEqual(scoring.quality_tier_base_score("good"), 85)
        self.assertEqual(scoring.quality_tier_base_score("needs_work"), 60)
        self.assertEqual(scoring.quality_tier_base_score("poor"), 45)

    def test_very_good_alias_maps_to_good(self) -> None:
        """very_good is aliased to good, so its base score is good's."""
        self.assertEqual(scoring.quality_tier_base_score("very_good"), 85)


class BlendScoreTests(unittest.TestCase):
    def test_no_llm(self) -> None:
        self.assertEqual(scoring.blend_score(96, None), 96)

    def test_excellent_llm(self) -> None:
        # 0.6*96 + 0.4*95 = 57.6 + 38 = 95.6 -> 96
        self.assertEqual(scoring.blend_score(96, "excellent"), 96)

    def test_good_llm(self) -> None:
        # 0.6*96 + 0.4*85 = 57.6 + 34 = 91.6 -> 92
        self.assertEqual(scoring.blend_score(96, "good"), 92)

    def test_needs_work_llm(self) -> None:
        # 0.6*94 + 0.4*60 = 56.4 + 24 = 80.4 -> 80
        self.assertEqual(scoring.blend_score(94, "needs_work"), 80)

    def test_poor_llm(self) -> None:
        # 0.6*80 + 0.4*45 = 48 + 18 = 66
        self.assertEqual(scoring.blend_score(80, "poor"), 66)

    def test_clamp_at_100(self) -> None:
        self.assertLessEqual(scoring.blend_score(100, "excellent"), 100)

    def test_clamp_at_0(self) -> None:
        self.assertGreaterEqual(scoring.blend_score(0, "poor"), 0)


class CLITests(unittest.TestCase):
    def _run(self, *args: str) -> dict:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "scoring.py"), *args],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_det_only(self) -> None:
        out = self._run("--det-score", "96")
        self.assertEqual(out["overall_score"], 96)
        self.assertEqual(out["overall_tier"], "excellent")

    def test_with_llm_tier(self) -> None:
        out = self._run("--det-score", "96", "--llm-tier", "good")
        self.assertEqual(out["overall_score"], 92)
        self.assertEqual(out["overall_tier"], "excellent")


class ApplyScoreCapsTests(unittest.TestCase):
    def test_no_caps_applied(self) -> None:
        # Check IDs that have no cap configured
        self.assertEqual(scoring.apply_score_caps(95, ["2.1", "3.1"]), 95)

    def test_cap_1_1_missing_frontmatter(self) -> None:
        # Missing name caps at 75
        self.assertEqual(scoring.apply_score_caps(95, ["1.1"]), 75)

    def test_cap_1_2_missing_description(self) -> None:
        # Missing description caps at 75
        self.assertEqual(scoring.apply_score_caps(95, ["1.2"]), 75)

    def test_cap_4_8_redirect_stub(self) -> None:
        # Redirect/stub skill caps at 35
        self.assertEqual(scoring.apply_score_caps(91, ["4.8"]), 35)

    def test_already_below_cap(self) -> None:
        # Score already below cap is unchanged
        self.assertEqual(scoring.apply_score_caps(30, ["1.1"]), 30)

    def test_multiple_caps_uses_lowest(self) -> None:
        # Multiple caps: 1.1 -> 75, 4.8 -> 35; lowest wins
        self.assertEqual(scoring.apply_score_caps(95, ["1.1", "4.8"]), 35)

    def test_empty_error_ids(self) -> None:
        self.assertEqual(scoring.apply_score_caps(95, []), 95)


if __name__ == "__main__":
    unittest.main()
