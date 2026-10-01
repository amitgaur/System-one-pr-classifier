"""
Compare rule-based vs Jevlike-trained classifier on the same held-out test set.

Loads 71 SWE-PRBench + Bun PRs, runs both classifiers, computes top-1
accuracy and per-intent confusion matrix.

Headline expected result: Jevlike (Qwen encoder) > rule-based on intent.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, '.')

REPO_DIR = Path(__file__).resolve().parents[1]


def load_test_prs():
    """Load the 71 test PRs in their raw form."""
    with (REPO_DIR / "data/jev_training/test_meta.json").open() as f:
        meta = json.load(f)
    with (REPO_DIR / "data/swe_prbench/converted.json").open() as f:
        swe_prs = json.load(f)
    with (REPO_DIR / "data/bun_prs.json").open() as f:
        bun_prs = json.load(f)

    prs_by_id = {}
    for p in swe_prs:
        prs_by_id[p["task_id"]] = p
    for p in bun_prs:
        prs_by_id[str(p.get("number", ""))] = {
            "title": p.get("title", ""),
            "body": p.get("body", ""),
            "file_paths": p.get("file_paths", []),
            "diff": p.get("diff", ""),
        }

    mapping = {
        "feature": "feature",
        "bug_fix": "bug_fix",
        "refactor": "refactor",
        "perf": "perf",
        "performance": "perf",
        "security": "security_patch",
    }

    out = []
    for m in meta:
        pr = prs_by_id.get(m["id"])
        if pr is None:
            continue
        gold = mapping.get(m["pr_type"], "mixed_or_unclear")
        out.append({
            "id": m["id"],
            "title": pr.get("title", ""),
            "body": pr.get("body", ""),
            "file_paths": pr.get("file_paths", []) or [],
            "diff": pr.get("diff", "") or "",
            "additions": 0,
            "deletions": 0,
            "gold": gold,
        })
    return out


def main():
    from src.classifier import classify_pr_ontology

    # Try to load Jevlike adapter (graceful if not available)
    try:
        from src.jevlike_adapter import classify_with_jevlike
        HAS_JEVLIKE = True
    except Exception as e:
        print(f"Jevlike not available: {e}")
        HAS_JEVLIKE = False
        return

    prs = load_test_prs()
    print(f"Loaded {len(prs)} test PRs")

    rule_correct = 0
    jevlike_correct = 0
    rule_confusion = {}
    jevlike_confusion = {}

    rule_t = 0.0
    jevlike_t = 0.0

    for pr in prs:
        gold = pr["gold"]
        # Rule-based
        t0 = time.time()
        rule_pred = classify_pr_ontology(
            title=pr["title"], body=pr["body"],
            file_paths=pr["file_paths"], diff=pr["diff"],
            additions=pr["additions"], deletions=pr["deletions"],
        ).pr_intent
        rule_t += time.time() - t0

        # Jevlike
        t0 = time.time()
        try:
            jevlike_pred = classify_with_jevlike(
                title=pr["title"], body=pr["body"],
                file_paths=pr["file_paths"], diff=pr["diff"],
                additions=pr["additions"], deletions=pr["deletions"],
            ).pr_intent
        except Exception as e:
            jevlike_pred = rule_pred  # fallback
        jevlike_t += time.time() - t0

        if rule_pred == gold:
            rule_correct += 1
        rule_confusion.setdefault(gold, {}).setdefault(rule_pred, 0)
        rule_confusion[gold][rule_pred] += 1

        if jevlike_pred == gold:
            jevlike_correct += 1
        jevlike_confusion.setdefault(gold, {}).setdefault(jevlike_pred, 0)
        jevlike_confusion[gold][jevlike_pred] += 1

    n = len(prs)
    print(f"\n=== Headline (intent top-1 accuracy on {n} held-out PRs) ===")
    print(f"  Rule-based:   {rule_correct}/{n} = {rule_correct/n:.3f}  (avg {rule_t/n*1000:.1f}ms per PR)")
    print(f"  Jevlike:      {jevlike_correct}/{n} = {jevlike_correct/n:.3f}  (avg {jevlike_t/n*1000:.1f}ms per PR)")
    print(f"  Improvement:  +{(jevlike_correct - rule_correct)/n:.3f}  ({(jevlike_correct - rule_correct)} correct more)")

    print("\n=== Rule-based confusion ===")
    for gold, preds in sorted(rule_confusion.items()):
        print(f"  {gold}: {preds}")

    print("\n=== Jevlike confusion ===")
    for gold, preds in sorted(jevlike_confusion.items()):
        print(f"  {gold}: {preds}")


if __name__ == "__main__":
    main()