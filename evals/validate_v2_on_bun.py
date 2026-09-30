"""
Validate the ONTOLOGY-based classifier against oven-sh/bun PRs.

Same mapping as validate_on_bun.py:
  bug -> bug_fix (intent)
  enhancement -> feature
  docs -> docs
  build -> build_ci
  breaking -> feature  (breaking changes are usually features)
  chore -> chore  (now a first-class intent)
  typescript -> refactor  (type work = refactor)
  perf -> perf (now a first-class intent)
  ci -> ci
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict

sys.path.insert(0, ".")
from src.classifier import classify_pr_ontology


BUN_LABEL_TO_INTENT = {
    "bug": "bug_fix",
    "enhancement": "feature",
    "docs": "docs",
    "performance": "perf",
    "build": "build_ci",
    "breaking": "feature",  # breaking changes are usually features
    "chore": "chore",
    "typescript": "refactor",
    "transpiler": "feature",
    "bundler": "feature",
    "bun:test": "test",
    "bun:ffi": "feature",
    "node:fs": "feature",
    "cli": "feature",
    "wasm": "feature",
    "infrastructure": "build_ci",
}


def get_gold_intent(pr: dict) -> str | None:
    labels = pr.get("labels") or []
    priority = ["bug", "breaking", "performance", "docs", "build",
                "typescript", "chore", "transpiler", "bundler", "bun:test",
                "bun:ffi", "node:fs", "cli", "wasm", "enhancement",
                "infrastructure"]
    for p in priority:
        for lab in labels:
            if lab.get("name") == p:
                return BUN_LABEL_TO_INTENT.get(p)
    return None


def main():
    with open("data/bun_prs.json") as f:
        prs = json.load(f)

    confusion = defaultdict(Counter)
    by_pred = Counter()
    by_gold = Counter()

    # Also count tier distribution and severity
    tier_dist = Counter()
    severity_dist = Counter()
    domain_dist = Counter()

    for pr in prs:
        gold = get_gold_intent(pr)
        if gold is None:
            continue
        r = classify_pr_ontology(
            title=pr.get("title", ""),
            body=pr.get("body", "") or "",
            file_paths=pr.get("all_file_paths", []),
            diff=pr.get("diff", ""),
            branch=pr.get("headRefName", ""),
            additions=pr.get("additions", 0),
            deletions=pr.get("deletions", 0),
        )
        confusion[r.pr_intent][gold] += 1
        by_pred[r.pr_intent] += 1
        by_gold[gold] += 1
        tier_dist[r.review_tier] += 1
        severity_dist[r.pr_severity] += 1
        domain_dist[r.review_domain] += 1

    # ---- Per-class P/R/F1 ----
    print("=" * 70)
    print(f"ONTOLOGY CLASSIFIER on oven-sh/bun (n={sum(by_gold.values())} PRs)")
    print("=" * 70)
    print(f"\n{'Intent':<18} {'P':>5} {'R':>5} {'F1':>5} {'TP':>4} {'FP':>4} {'FN':>4} {'sup':>5}")
    print("-" * 60)
    f1s = []
    intents = sorted(set(list(confusion.keys()) + list(by_gold.keys())))
    for intent in intents:
        cm = confusion[intent]
        tp = cm.get(intent, 0)
        fp = sum(cm.values()) - tp
        fn = by_gold.get(intent, 0) - tp
        P = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        R = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        F1 = 2*P*R/(P+R) if (P+R) > 0 else 0.0
        if by_gold.get(intent, 0) >= 3:
            f1s.append(F1)
        print(f"{intent:<18} {P:>5.2f} {R:>5.2f} {F1:>5.2f} {tp:>4d} {fp:>4d} {fn:>4d} {by_gold.get(intent,0):>5d}")
    print(f"\nMacro F1 (support>=3): {sum(f1s)/len(f1s):.3f}")

    print("\n--- Confusion matrix ---")
    gold_kinds = ["bug_fix", "feature", "docs", "perf", "build_ci",
                  "chore", "refactor", "test"]
    print("predicted \\ gold  " + " ".join(f"{g[:9]:>10s}" for g in gold_kinds) + " | total")
    print("-" * (22 + 11*len(gold_kinds) + 8))
    for intent in sorted(confusion.keys()):
        cm = confusion[intent]
        row = " ".join(f"{cm.get(g, 0):>10d}" for g in gold_kinds)
        total = sum(cm.values())
        print(f"{intent:<22} {row} | {total:>5d}")

    print("\n--- Routing tier distribution ---")
    for tier, n in tier_dist.most_common():
        print(f"  {tier:25s} {n:3d}")

    print("\n--- Severity distribution ---")
    for sev, n in severity_dist.most_common():
        print(f"  {sev:10s} {n:3d}")

    print("\n--- Domain distribution ---")
    for dom, n in domain_dist.most_common():
        print(f"  {dom:30s} {n:3d}")


if __name__ == "__main__":
    main()