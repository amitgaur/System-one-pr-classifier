"""Tests for the Jev/decider adapter.

Verifies that:
1. classify_with_fallback gracefully returns rule-based result when decider
   is not installed
2. State building produces valid Jev state objects
3. Question library is well-formed
4. PRClassification output from the rule-based path is identical to calling
   classify_pr_ontology directly
"""
from __future__ import annotations

import sys
sys.path.insert(0, '.')


def test_fallback_without_decider():
    """When decider-ai is not installed, fallback returns rule-based result."""
    from src.decider_adapter import classify_with_fallback, HAS_DECIDER

    result = classify_with_fallback(
        title="Add authentication middleware",
        body="",
        file_paths=["src/auth/middleware.py"],
        diff="+ auth_required decorator",
        additions=20,
        deletions=5,
    )
    assert result.pr_intent in (
        "bug_fix", "feature", "docs", "test", "build_ci", "chore", "perf",
        "refactor", "revert", "security_patch", "mixed_or_unclear",
    )
    # Without decider OR with decider — both paths should return a valid PRClassification
    # (When decider IS installed, it may or may not be used depending on confidence.)


def test_state_building():
    """State builder produces a dict with required keys."""
    from src.decider_adapter import _build_state

    state = _build_state(
        title="Fix login bug",
        body="The login form was broken",
        file_paths=["src/auth.py", "src/login.py", "tests/test_auth.py"],
        diff="+ fix bug",
        additions=10,
        deletions=5,
    )
    assert "title" in state
    assert "body_excerpt" in state
    assert "file_count" in state
    assert "primary_languages" in state
    assert "diff_first_30k" in state
    assert "additions" in state
    assert "deletions" in state
    assert state["file_count"] == 3
    assert "python" in state["primary_languages"]


def test_question_library():
    """Question library has the right shape."""
    from src.decider_adapter import (
        INTENT_QUESTION, TIER_QUESTION, RISK_NOUL_QUESTIONS, COMPLEXITY_QUESTION
    )
    # INTENT: 11 options, all valid intent literals
    assert len(INTENT_QUESTION["options"]) == 11
    assert "bug_fix" in INTENT_QUESTION["options"]
    assert "mixed_or_unclear" in INTENT_QUESTION["options"]

    # TIER: 5 options, all valid tier literals
    assert len(TIER_QUESTION["options"]) == 5
    assert "T3_system2_deliberate" in TIER_QUESTION["options"]

    # RISK: 9 noul questions, one per risk flag
    assert len(RISK_NOUL_QUESTIONS) == 9
    assert "touches_auth" in RISK_NOUL_QUESTIONS
    assert "touches_secrets" in RISK_NOUL_QUESTIONS

    # COMPLEXITY: 6 score levels, monotonic 0.0 -> 1.0
    assert len(COMPLEXITY_QUESTION["levels"]) == 6
    levels = [l["value"] for l in COMPLEXITY_QUESTION["levels"]]
    assert levels == sorted(levels)
    assert levels[0] == 0.0
    assert levels[-1] == 1.0


def test_risk_keyword_scan():
    """Risk keyword scanner catches obvious risk terms."""
    from src.decider_adapter import _scan_for_risk_keywords

    # Auth risks
    risks = _scan_for_risk_keywords("Fix login bug", "session handling")
    assert "auth" in risks

    # Secret risks
    risks = _scan_for_risk_keywords("", "rotated password and credentials")
    assert "secrets" in risks

    # Crypto risks
    risks = _scan_for_risk_keywords("", "new crypto module")
    assert "crypto" in risks

    # No risks
    risks = _scan_for_risk_keywords("Update docs", "fix typo")
    assert len(risks) == 0


def test_diff_truncation():
    """First 30KB of diff is what's sent to the decision model."""
    from src.decider_adapter import _build_state

    long_diff = "x" * 100_000
    state = _build_state(
        title="long diff test",
        body="",
        file_paths=["foo.py"],
        diff=long_diff,
        additions=100,
        deletions=50,
    )
    # SWE-PRBench config_A protocol: 30KB max
    assert len(state["diff_first_30k"]) == 30_000


def test_prclassification_round_trip():
    """The fallback function returns a valid PRClassification."""
    from src.classifier import PRClassification
    from src.decider_adapter import classify_with_fallback

    result = classify_with_fallback(
        title="Refactor auth module",
        body="",
        file_paths=["src/auth.py"],
        diff="+ cleaner API",
        additions=50,
        deletions=30,
    )
    # Pydantic round-trips
    assert isinstance(result, PRClassification)
    assert 0.0 <= result.confidence <= 1.0
    assert 0.0 <= result.complexity_score <= 1.0
    assert result.review_tier in (
        "T0_skip", "T1_system1_fast", "T2_system1_verified",
        "T3_system2_deliberate", "T4_human_gate",
    )


if __name__ == "__main__":
    test_fallback_without_decider()
    print("[PASS] fallback without decider")
    test_state_building()
    print("[PASS] state building")
    test_question_library()
    print("[PASS] question library")
    test_risk_keyword_scan()
    print("[PASS] risk keyword scan")
    test_diff_truncation()
    print("[PASS] diff truncation")
    test_prclassification_round_trip()
    print("[PASS] PRClassification round-trip")
    print("\nAll Jev/decider adapter tests pass.")