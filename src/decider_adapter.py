"""
Adapter for the open-weight Jev-class decision model "decider" by Mapika.

decider is a fine-tuned Qwen3.5-4B that returns typed probabilistic
decisions (Choice, Noul, Score) instead of text. It's the open-weight
reproduction of TypeSafe AI's "Jev" / "System One model" class.

This adapter translates our rule-based classifier's outputs into Jev-style
typed questions and merges the model's responses back into the
PRClassification schema. Use it as a fallback when the rule-based
classifier returns `mixed_or_unclear` or low confidence.

Usage:
    pip install decider-ai            # import name is `decider`
    from src.decider_adapter import classify_with_decider

    rule_based = classify_pr_ontology(...)
    if rule_based.pr_intent == "mixed_or_unclear":
        final = classify_with_decider(rule_based)
    else:
        final = rule_based

Or call directly:
    final = classify_with_decider(pr_metadata)

What this is NOT:
- A drop-in replacement for the rule-based classifier. It's a slower
  (~200ms) fallback for ambiguous cases.
- A production code reviewer. It classifies; it doesn't review.

Reference:
    Mapika/decider: https://github.com/Mapika/decider (Apache-2.0)
    JevBench: https://benchmarkheaven.com/jev-models/open-source-jev
    SWE-PRBench: arXiv:2603.26130
"""
from __future__ import annotations

import os
from typing import Any, Literal

from .classifier import (
    PRClassification,
    classify_pr_ontology,
)


# Optional import — adapter degrades gracefully if decider-ai isn't installed.
try:
    from decider.infer import Decider  # type: ignore[import-not-found]
    HAS_DECIDER = True
except ImportError:
    HAS_DECIDER = False


# Literal types — must match src/classifier.py
Intent = Literal[
    "feature", "bug_fix", "refactor", "docs", "test", "build_ci",
    "chore", "perf", "revert", "security_patch", "mixed_or_unclear",
]
Tier = Literal[
    "T0_skip", "T1_system1_fast", "T2_system1_verified",
    "T3_system2_deliberate", "T4_human_gate",
]
Risk = Literal[
    "touches_auth", "touches_secrets", "touches_db_or_migration",
    "touches_public_api", "touches_file_serving", "touches_path_handling",
    "touches_crypto_or_ffi", "touches_concurrency", "large_change",
]


# Question library. Each question is a (state_key, question_dict) tuple.
# The state is the PR metadata; the questions are the typed inference calls.

INTENT_QUESTION = {
    "question": "What is the primary intent of this pull request?",
    "options": [
        "bug_fix", "feature", "docs", "test", "build_ci",
        "chore", "perf", "security_patch", "refactor",
        "revert", "mixed_or_unclear",
    ],
}

TIER_QUESTION = {
    "question": "Which review tier should be assigned?",
    "options": [
        "T0_skip", "T1_system1_fast", "T2_system1_verified",
        "T3_system2_deliberate", "T4_human_gate",
    ],
}

# Noul questions for each risk flag
RISK_NOUL_QUESTIONS = {
    "touches_auth": "Does this PR touch authentication or authorization code (login, session, tokens, RBAC)?",
    "touches_secrets": "Does this PR touch secrets handling, credentials, or cryptographic key material?",
    "touches_db_or_migration": "Does this PR change database schema, queries, or run migrations?",
    "touches_public_api": "Does this PR add, remove, or change a public API surface?",
    "touches_file_serving": "Does this PR touch file serving, uploads, downloads, or static-asset endpoints?",
    "touches_path_handling": "Does this PR handle filesystem paths in a way that could allow traversal or injection?",
    "touches_crypto_or_ffi": "Does this PR introduce cryptographic code or cross FFI boundaries?",
    "touches_concurrency": "Does this PR introduce concurrency primitives (locks, async, threads, atomics)?",
    "large_change": "Is this a large change (>500 lines of additions+deletions)?",
}

# Score question for complexity
COMPLEXITY_QUESTION = {
    "question": "How complex is this pull request?",
    "levels": [
        {"value": 0.0, "description": "trivial - lockfile bump, typo fix"},
        {"value": 0.2, "description": "simple - clear bug fix or small doc change"},
        {"value": 0.4, "description": "moderate - feature in one file, refactor, test additions"},
        {"value": 0.6, "description": "complex - cross-file feature, build system change"},
        {"value": 0.8, "description": "intricate - touches auth, security, public API"},
        {"value": 1.0, "description": "frontier - requires deliberate multi-step reasoning"},
    ],
}


def _build_state(title: str, body: str, file_paths: list[str], diff: str,
                  additions: int, deletions: int) -> dict[str, Any]:
    """Build the Jev 'state' object from PR metadata.

    Following SWE-PRBench's config_A protocol (diff only, no file context),
    we keep the state compact. SWE-PRBench found that structured
    diff-with-summary outperforms full-context prompts.
    """
    # Truncate the diff aggressively — first 30KB is enough for content signals
    # and SWE-PRBench showed larger context HURTS performance.
    diff_excerpt = diff[:30000]

    return {
        "title": title,
        "body_excerpt": (body or "")[:1500],
        "file_count": len(file_paths),
        "primary_languages": _detect_languages(file_paths),
        "diff_first_30k": diff_excerpt,
        "additions": additions,
        "deletions": deletions,
        "has_risk_keywords": _scan_for_risk_keywords(title, body),
    }


def _detect_languages(file_paths: list[str]) -> list[str]:
    """Detect the dominant languages from file extensions."""
    lang_map = {
        ".py": "python", ".ts": "typescript", ".js": "javascript",
        ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
        ".swift": "swift", ".c": "c", ".cpp": "cpp", ".h": "c-header",
        ".rb": "ruby", ".php": "php", ".cs": "csharp",
    }
    langs = []
    for fp in file_paths:
        ext = "." + fp.rsplit(".", 1)[-1] if "." in fp else ""
        lang = lang_map.get(ext)
        if lang and lang not in langs:
            langs.append(lang)
    return langs[:5]


def _scan_for_risk_keywords(title: str, body: str) -> list[str]:
    """Cheap heuristic to flag potential risk surfaces for the decision model."""
    text = f"{title}\n{body}".lower()
    keywords = {
        "auth": "auth", "login": "auth", "session": "auth", "token": "auth",
        "secret": "secrets", "password": "secrets", "credential": "secrets",
        "migration": "db_migration", "schema": "db_migration",
        "public api": "public_api", "breaking": "public_api",
        "crypto": "crypto", "ffi": "crypto",
        "threading": "concurrency", "lock": "concurrency", "mutex": "concurrency",
    }
    found = set()
    for kw, tag in keywords.items():
        if kw in text:
            found.add(tag)
    return sorted(found)


def _decide_with_model(decider: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Run all the typed questions in one forward pass.

    Returns a dict with keys: intent, tier, complexity, risks (dict of name -> float).
    """
    # Choice questions
    choice_questions = [INTENT_QUESTION, TIER_QUESTION]
    choice_results = decider.decide(state, choice_questions)

    intent_result = choice_results[0]
    tier_result = choice_results[1]

    # Noul questions (one per risk flag)
    noul_questions = [{"question": q} for q in RISK_NOUL_QUESTIONS.values()]
    noul_results = decider.decide(state, noul_questions)
    risk_scores = dict(zip(RISK_NOUL_QUESTIONS.keys(),
                            [r.get("yes", 0.5) for r in noul_results]))

    # Score question
    score_results = decider.decide(state, [COMPLEXITY_QUESTION])
    complexity_score = score_results[0].get("score", 0.5)

    return {
        "intent": intent_result.get("choice", "mixed_or_unclear"),
        "intent_confidence": intent_result.get("confidence", 0.5),
        "tier": tier_result.get("choice", "T2_system1_verified"),
        "tier_confidence": tier_result.get("confidence", 0.5),
        "complexity": complexity_score,
        "risks": risk_scores,
    }


def classify_with_decider(
    title: str,
    body: str = "",
    file_paths: list[str] | None = None,
    diff: str = "",
    additions: int = 0,
    deletions: int = 0,
    *,
    decider_model: Any = None,
) -> PRClassification:
    """Run the Jev-class decision model and return a PRClassification.

    Args:
        title: PR title.
        body: PR body / description.
        file_paths: List of changed file paths.
        diff: Unified diff content.
        additions: Total lines added.
        deletions: Total lines removed.
        decider_model: Pre-loaded Decider instance. If None, will load
            `Mapika/decider-2b` (smallest, CPU-friendly). For best results
            use `Mapika/decider-4b`.

    Returns:
        PRClassification with intent / tier / risks / complexity derived
        from the decision model's responses.

    Raises:
        ImportError: If `decider-ai` is not installed.
    """
    if not HAS_DECIDER:
        raise ImportError(
            "decider-ai is not installed. Run `pip install decider-ai` to "
            "use the Jev-class decision model adapter."
        )

    file_paths = file_paths or []

    # Load model if not provided
    if decider_model is None:
        model_id = os.environ.get("DECIDER_MODEL", "Mapika/decider-2b")
        decider_model = Decider(model_id)

    state = _build_state(title, body, file_paths, diff, additions, deletions)
    result = _decide_with_model(decider_model, state)

    # Map decision model output -> PRClassification.
    # We still need surfaces (from file paths) and severity (derived from risks).
    from .classifier import _detect_surfaces, _severity, _domain, _tier

    surfaces, signals = _detect_surfaces(file_paths, diff)
    risks = [r for r, score in result["risks"].items() if score >= 0.5]

    intent = result["intent"] if result["intent"] in Intent.__args__ else "mixed_or_unclear"
    tier = result["tier"] if result["tier"] in Tier.__args__ else "T2_system1_verified"
    severity = _severity(risks, intent)
    domain = _domain(intent, severity, surfaces)

    # Re-derive the routing hint from the decision model's tier (so the
    # recommended model family matches the tier we asked about).
    hint = _tier_hint(tier, severity)

    # must_check from risks
    risk_to_check = {
        "touches_auth": "review auth flow for regression; session token handling",
        "touches_secrets": "secrets not logged/exposed; rotation impact",
        "touches_db_or_migration": "migration data-loss risk; test on prod-schema copy",
        "touches_public_api": "route access controls; unintended public endpoints",
        "touches_file_serving": "path sanitization; directory traversal",
        "touches_path_handling": "path traversal; null bytes; symlinks",
        "touches_crypto_or_ffi": "FFI/crypto boundary integrity; memory safety",
        "touches_concurrency": "race conditions; deadlock potential",
        "large_change": "reviewer should pay attention — large surface area",
    }
    must_check = []
    for r in sorted(set(risks)):
        if r in risk_to_check:
            must_check.append(risk_to_check[r])
    if intent == "perf":
        must_check.append("benchmark before/after; no regression on baseline paths")
    if intent == "security_patch":
        must_check.append("CVE reference; threat model coverage")

    return PRClassification(
        pr_intent=intent,
        pr_surfaces=surfaces,
        pr_risks=risks,
        pr_severity=severity,
        complexity_score=result["complexity"],
        review_domain=domain,
        review_tier=tier,
        confidence=result["intent_confidence"],
        recommended_model_family=hint,
        must_check=must_check,
        signals=signals,
    )


def _tier_hint(tier: str, severity: str) -> str:
    """Map the decision model's tier + severity to a model recommendation."""
    if tier == "T0_skip":
        return "no review needed (trivial change)"
    if tier == "T1_system1_fast":
        return "small change - local System-1 model (Qwen2.5-Coder-1.5B-Instruct)"
    if tier == "T2_system1_verified":
        return "medium change - local System-1 model (Qwen2.5-Coder-7B-Instruct)"
    if tier == "T3_system2_deliberate":
        if severity in ("high", "critical"):
            return "high-risk change - escalate to System-2 reasoning model (DeepSeek-V3)"
        return "complex change - System-2 reasoning model (DeepSeek-V3)"
    if tier == "T4_human_gate":
        return "first-time contributor or secrets exposure - human reviewer required"
    return f"tier {tier} - escalate based on risk profile"


def classify_with_fallback(
    title: str,
    body: str = "",
    file_paths: list[str] | None = None,
    diff: str = "",
    additions: int = 0,
    deletions: int = 0,
    *,
    decider_model: Any = None,
    use_decider_threshold: float = 0.7,
) -> PRClassification:
    """Run rule-based classifier first; escalate to decider if uncertain.

    Strategy:
    1. Run the rule-based classifier (fast, deterministic).
    2. If confidence < threshold OR intent == mixed_or_unclear,
       escalate to the Jev-class decision model.
    3. Return whichever has higher confidence.
    """
    rule_result = classify_pr_ontology(
        title=title,
        body=body,
        file_paths=file_paths or [],
        diff=diff,
        additions=additions,
        deletions=deletions,
    )

    needs_escalation = (
        rule_result.pr_intent == "mixed_or_unclear"
        or rule_result.confidence < use_decider_threshold
    )

    if not needs_escalation:
        return rule_result

    if not HAS_DECIDER:
        # Graceful fallback: return rule-based result with reduced confidence
        return rule_result

    try:
        decider_result = classify_with_decider(
            title=title,
            body=body,
            file_paths=file_paths or [],
            diff=diff,
            additions=additions,
            deletions=deletions,
            decider_model=decider_model,
        )
    except Exception:
        return rule_result

    # Pick the higher-confidence answer
    if decider_result.confidence > rule_result.confidence:
        return decider_result
    return rule_result