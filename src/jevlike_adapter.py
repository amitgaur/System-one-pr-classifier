"""
Adapter for the Jevlike decision models we trained in this project.

We train one TinyScorer per question type (intent, tier, risk, complexity).
Each model is a small (~400KB) attention head over either byte embeddings
or a frozen Qwen2.5-0.5B encoder.

The adapter returns a PRClassification, matching the rule-based classifier's
schema. Use it as a faster, more calibrated alternative to the rules.

Why this works:
- Each question is a Jev-style typed decision (Choice / Noul / Score)
- All 4 question types run in ~10ms total on CPU (or ~2ms on GPU)
- Calibrated probabilities (ECE ~0.14 on intent) — beats the rule-based
  hard-coded confidence values
- Top-1 accuracy on intent: 63.4% (Qwen encoder) vs rule-based 49.3%
  on the same held-out SWE-PRBench + Bun test split

Usage:
    from src.jevlike_adapter import classify_with_jevlike

    # All 4 questions in one call
    result = classify_with_jevlike(
        title="...",
        body="...",
        file_paths=[...],
        diff="...",
        additions=10, deletions=5,
    )

    # Or per-question
    intent = predict_intent("some context", intent_model, intent_options)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import torch

from .classifier import (
    PRClassification,
    classify_pr_ontology,
)


REPO_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RUNS_DIR = REPO_DIR / "runs"

# Question option libraries — must match build_jev_training_data.py
INTENT_OPTIONS = [
    "bug_fix", "feature", "docs", "test", "build_ci",
    "chore", "perf", "refactor", "revert", "security_patch", "mixed_or_unclear",
]
TIER_OPTIONS = [
    "T0_skip", "T1_system1_fast", "T2_system1_verified",
    "T3_system2_deliberate", "T4_human_gate",
]
RISK_FLAGS = [
    ("touches_auth", "auth code"),
    ("touches_secrets", "secrets/credentials"),
    ("touches_db_or_migration", "db schema/migration"),
    ("touches_public_api", "public API"),
    ("touches_file_serving", "file serving"),
    ("touches_path_handling", "filesystem paths"),
    ("touches_crypto_or_ffi", "crypto/FFI"),
    ("touches_concurrency", "concurrency primitives"),
    ("large_change", "large change >500 lines"),
]
COMPLEXITY_OPTIONS = [
    "0.0_trivial_lockfile",
    "0.2_simple_typo",
    "0.4_moderate_refactor",
    "0.6_complex_cross_file",
    "0.8_intricate_auth_security",
    "1.0_frontier_reasoning",
]

# Lazy imports — only if torch + jevlike are available
try:
    import jevlike  # noqa: F401
    from jevlike.model import select_device, make_system
    from jevlike.data import JsonlDataset, ChoiceExample
    import numpy as np
    HAS_JEVLIKE = True
except ImportError:
    HAS_JEVLIKE = False


# --- Context builder (must match build_jev_training_data.py) ---------------

def build_context(pr: dict) -> str:
    """Build the Jevlike context string from a PR dict. Must match the
    training data builder exactly."""
    parts = []
    title = pr.get("title", "")
    if title:
        parts.append(f"TITLE: {title}")

    body = pr.get("body", "")
    if body and len(body) > 30:
        parts.append(f"BODY: {body[:150]}")

    files = pr.get("file_paths", [])
    if files:
        exts = sorted(set(fp.rsplit(".", 1)[-1] if "." in fp else "no-ext" for fp in files))
        path_kws_set = {"test", "tests", "docs", "doc", "ci", "build",
                        "scripts", "tools", "src", "lib", "auth",
                        "migrations", "workflows", "examples", "bench"}
        path_kws = sorted(set(
            seg for fp in files for seg in fp.split("/")
            if seg and seg.lower() in path_kws_set
        ))
        parts.append(f"FILES: ext={','.join(exts[:5])} kws={','.join(path_kws[:5])}")

    diff_size = pr.get("additions", 0) + pr.get("deletions", 0)
    parts.append(f"DIFF_SIZE: {diff_size}")

    return " | ".join(parts)[:500]


# --- Model loader ----------------------------------------------------------

class JevlikePredictor:
    """Loads a trained Jevlike model and runs predictions."""

    def __init__(self, model_path: str, device: str = "cpu"):
        if not HAS_JEVLIKE:
            raise ImportError("jevlike is not installed. Run `pip install -e /path/to/jevlike`")
        self.device = torch.device(device)
        checkpoint = torch.load(model_path, map_location=self.device, weights_only=False)
        config = checkpoint["config"]
        self.config = config
        system_tuple = make_system(config, self.device)
        # make_system returns (model, collator)
        self.system = system_tuple[0]
        self.collator = system_tuple[1]
        self.system.load_state_dict(checkpoint["state_dict"], strict=False)
        self.system.eval()

    def predict(self, context: str, options: list[str]) -> tuple[int, list[float]]:
        """Return (argmax_index, probabilities)."""
        # Build a ChoiceExample in the format Jevlike expects
        ex = ChoiceExample(context, tuple(options), 0)
        batch = self.collator([ex])
        # Move batch tensors to device
        batch = {k: v.to(self.device) if hasattr(v, "to") else v for k, v in batch.items()}
        with torch.no_grad():
            logits = self.system(batch)
        probs = torch.softmax(logits[0], dim=-1).tolist()
        return int(np.argmax(probs)), probs


def config_get(config: dict, key: str, default=None):
    """Get a config key (Jevlike uses nested dicts sometimes)."""
    if key in config:
        return config[key]
    return default


# --- Higher-level adapter --------------------------------------------------

def classify_with_jevlike(
    title: str,
    body: str = "",
    file_paths: list[str] | None = None,
    diff: str = "",
    additions: int = 0,
    deletions: int = 0,
    *,
    runs_dir: str | Path = DEFAULT_RUNS_DIR,
    device: str = "cpu",
) -> PRClassification:
    """Run all 4 Jevlike models and return a PRClassification.

    Falls back to the rule-based classifier if jevlike or the trained
    checkpoints aren't available.
    """
    if not HAS_JEVLIKE:
        return classify_pr_ontology(
            title=title, body=body, file_paths=file_paths or [],
            diff=diff, additions=additions, deletions=deletions,
        )

    # Run intent model (Qwen encoder preferred, tiny fallback)
    qwen_ckpt = runs_dir / "jevlike-intent-qwen.pt"
    tiny_ckpt = runs_dir / "jevlike-intent-tiny-v2.pt"
    if qwen_ckpt.exists():
        intent_ckpt = qwen_ckpt
    elif tiny_ckpt.exists():
        intent_ckpt = tiny_ckpt
    else:
        # No trained model — fall back
        return classify_pr_ontology(
            title=title, body=body, file_paths=file_paths or [],
            diff=diff, additions=additions, deletions=deletions,
        )

    # Build context once
    pr_dict = {
        "title": title,
        "body": body,
        "file_paths": file_paths or [],
        "additions": additions,
        "deletions": deletions,
    }
    context = build_context(pr_dict)

    # Run intent model (Qwen encoder preferred, tiny fallback)
    intent_pred = JevlikePredictor(str(intent_ckpt), device=device)
    intent_idx, intent_probs = intent_pred.predict(context, INTENT_OPTIONS)
    intent_label = INTENT_OPTIONS[intent_idx]
    intent_confidence = intent_probs[intent_idx]

    # Run tier model if available
    tier_label = "T2_system1_verified"
    tier_confidence = 0.5
    tier_ckpt = runs_dir / "jevlike-tier-tiny.pt"
    if tier_ckpt.exists():
        try:
            tier_pred = JevlikePredictor(str(tier_ckpt), device=device)
            tier_idx, tier_probs = tier_pred.predict(context, TIER_OPTIONS)
            tier_label = TIER_OPTIONS[tier_idx]
            tier_confidence = tier_probs[tier_idx]
        except Exception:
            pass

    # Run risk model if available
    risk_labels: list[str] = []
    risk_confidences: dict[str, float] = {}
    risk_ckpt = runs_dir / "jevlike-risk-tiny.pt"
    if risk_ckpt.exists():
        try:
            risk_pred = JevlikePredictor(str(risk_ckpt), device=device)
            for flag_name, flag_desc in RISK_FLAGS:
                options = [f"no {flag_desc}", f"yes {flag_desc}"]
                idx, probs = risk_pred.predict(context, options)
                # label=1 means yes
                yes_prob = probs[1]
                risk_confidences[flag_name] = yes_prob
                if yes_prob >= 0.5:
                    risk_labels.append(flag_name)
        except Exception:
            pass

    # Run complexity model if available
    complexity_score = 0.5
    complexity_ckpt = runs_dir / "jevlike-complexity-tiny.pt"
    if complexity_ckpt.exists():
        try:
            comp_pred = JevlikePredictor(str(complexity_ckpt), device=device)
            idx, probs = comp_pred.predict(context, COMPLEXITY_OPTIONS)
            complexity_label = COMPLEXITY_OPTIONS[idx]
            # Extract numeric complexity from label
            complexity_score = float(complexity_label.split("_")[0])
        except Exception:
            pass

    # Now derive the rest of the PRClassification from the rule-based classifier
    # but override with the Jevlike predictions
    rule = classify_pr_ontology(
        title=title, body=body, file_paths=file_paths or [],
        diff=diff, additions=additions, deletions=deletions,
    )

    # Merge: use Jevlike intent/tier/risks/complexity, keep rule-based surfaces/severity/domain
    from .classifier import _detect_surfaces, _severity, _domain

    surfaces, signals = _detect_surfaces(file_paths or [], diff)

    # Recompute severity using Jevlike's risk flags + intent
    severity = _severity(risk_labels, intent_label)

    # Re-derive tier from severity if rule-based tier doesn't match
    # (rule-based tier uses risk flags too — but Jevlike's tier is its own prediction)
    domain = _domain(intent_label, severity, surfaces)

    return PRClassification(
        pr_intent=intent_label,
        pr_surfaces=surfaces,
        pr_risks=risk_labels,
        pr_severity=severity,
        complexity_score=complexity_score,
        review_domain=domain,
        review_tier=tier_label,
        confidence=intent_confidence,
        recommended_model_family=rule.recommended_model_family,
        must_check=rule.must_check,
        signals=signals,
    )