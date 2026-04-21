#!/usr/bin/env python3
"""Single source of truth for all evaluation scoring logic.

Every score computation — deterministic penalties, LLM tier base scores,
weighted blending, and tier resolution — lives here so every consumer
(eval.py, the Go backend, direct agent use) shares one implementation.

All tunables are read from ``scoring_config.json`` (next to this file).
No environment variables are needed.

CLI usage (called by the Go backend after LLM review):

    python3 scoring.py --det-score 96 --llm-tier good

Outputs a single JSON object to stdout:

    {"overall_score": 92, "overall_tier": "excellent"}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent / "scoring_config.json"
_config_cache: dict[str, Any] | None = None


def _load_config() -> dict[str, Any]:
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    with open(_CONFIG_PATH) as f:
        _config_cache = json.load(f)
    return _config_cache


def reset_config_cache() -> None:
    """Clear cached config — useful for tests that patch the config file."""
    global _config_cache
    _config_cache = None


# ---------------------------------------------------------------------------
# Tier thresholds
# ---------------------------------------------------------------------------

def load_tier_thresholds() -> list[tuple[str, int]]:
    """Load tier thresholds from scoring_config.json.

    Returns list of (name, min_score) sorted descending by min_score.
    """
    cfg = _load_config()
    tiers = [(t["name"], t["min_score"]) for t in cfg["tiers"]]
    tiers.sort(key=lambda t: t[1], reverse=True)
    return tiers


def tier_for_score(score: int) -> str:
    """Return the quality tier name for a given numeric score."""
    for name, min_score in load_tier_thresholds():
        if score >= min_score:
            return name
    return "poor"


# ---------------------------------------------------------------------------
# Deterministic score
# ---------------------------------------------------------------------------

def deterministic_score(errors: int, warnings: int) -> int:
    """Compute the deterministic score from error/warning counts.

    Reads penalty values from scoring_config.json.
    """
    cfg = _load_config()
    pen = cfg["penalties"]
    error_penalty = pen["error_penalty"]
    error_cap = pen["error_cap"]
    warning_penalty = pen["warning_penalty"]
    warning_cap = pen["warning_cap"]
    score = 100 - min(errors * error_penalty, error_cap) - min(warnings * warning_penalty, warning_cap)
    return max(0, min(100, score))


def apply_score_caps(score: int, error_check_ids: list[str]) -> int:
    """Hard-cap *score* when specific critical findings are present.

    For example, a skill with no frontmatter (1.1 ERROR) should never
    score above 75, and a redirect/stub skill (4.8 ERROR) should never
    score above 35 — regardless of the penalty arithmetic.

    Caps are read from the ``score_caps`` key in scoring_config.json.
    Only ERROR-level findings trigger caps (the caller passes only
    error check IDs).
    """
    cfg = _load_config()
    caps = cfg.get("score_caps", {})
    for check_id in error_check_ids:
        if check_id in caps:
            score = min(score, caps[check_id])
    return score


# ---------------------------------------------------------------------------
# LLM quality tier handling
# ---------------------------------------------------------------------------

def _llm_tier_base_scores() -> dict[str, int]:
    """Return the LLM tier → base score mapping from config."""
    return _load_config()["llm_tier_base_scores"]


_TIER_ALIASES: dict[str, str] = {
    "very_good": "good",
}


def normalize_quality_tier(tier: str) -> str:
    """Normalize an LLM-returned quality tier to a canonical name."""
    tier = tier.strip().lower().replace(" ", "_")
    tier = _TIER_ALIASES.get(tier, tier)
    if tier in _llm_tier_base_scores():
        return tier
    return "needs_work"


def quality_tier_base_score(tier: str) -> int:
    """Return the numeric base score for a quality tier label.

    Used as the LLM's contribution to the blended score.
    """
    scores = _llm_tier_base_scores()
    return scores.get(normalize_quality_tier(tier), 70)


# ---------------------------------------------------------------------------
# Blending (deterministic + LLM)
# ---------------------------------------------------------------------------

def blend_score(
    det_score: int,
    llm_tier: str | None,
) -> int:
    """Compute a weighted blend of deterministic score and LLM assessment.

    Weights are read from scoring_config.json.
    When *llm_tier* is None the deterministic score is returned unchanged.
    """
    if llm_tier is None:
        return det_score
    cfg = _load_config()
    det_weight = cfg["blending"]["deterministic_weight"]
    llm_weight = cfg["blending"]["llm_weight"]
    llm_score = quality_tier_base_score(llm_tier)
    combined = det_weight * det_score + llm_weight * llm_score
    return max(0, min(100, round(combined)))


# ---------------------------------------------------------------------------
# CLI entry point (used by the Go backend)
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Compute blended evaluation score from deterministic + LLM results.",
    )
    parser.add_argument("--det-score", type=int, required=True, help="Deterministic score (0-100)")
    parser.add_argument("--llm-tier", type=str, default=None, help="LLM quality tier (excellent|good|needs_work|poor)")
    args = parser.parse_args()

    score = blend_score(args.det_score, args.llm_tier)
    tier = tier_for_score(score)
    json.dump({"overall_score": score, "overall_tier": tier}, sys.stdout)


if __name__ == "__main__":
    main()
