"""
Validate the ontology classifier against SWE-PRBench (n=350, 65 repos, 6 languages).

SWE-PRBench labels are: feature, bug_fix, refactor, performance, security.
We map these to our 8-intent ontology:
  - feature    -> feature
  - bug_fix    -> bug_fix
  - refactor   -> refactor
  - performance-> perf
  - security   -> security_patch

Reports per-intent P/R/F1, per-language F1, per-difficulty F1, plus
bootstrap confidence intervals on macro F1.

This is the cross-repo + multi-language test the Bun corpus alone can't provide.
"""
from __future__ import annotations

import json
import sys
import statistics
from collections import Counter, defaultdict

sys.path.insert(0, ".")
from src.classifier import classify_pr_ontology


# Mapping from SWE-PRBench pr_type to our intent enum
GOLD_TO_INTENT = {
    "feature": "feature",
    "bug_fix": "bug_fix",
    "refactor": "refactor",
    "performance": "perf",
    "security": "security_patch",
    # Common aliases in case the dataset uses different spellings
    "bug": "bug_fix",
    "perf": "perf",
    "doc": "docs",
    "docs": "docs",
    "test": "test",
    "build": "build_ci",
    "chore": "chore",
}


def pr_metrics(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp > 0 else 0.0
    r = tp / (tp + fn) if tp + fn > 0 else 0.0
    f = 2 * p * r / (p + r) if p + r > 0 else 0.0
    return p, r, f


def bootstrap_macro_f1(predictions, gold_labels, support_labels, n_boot=1000, seed=42):
    """Bootstrap resample macro F1 to get a 95% CI.

    support_labels: only include labels with support >= 3 in macro F1,
    matching the headline metric.
    """
    import random
    rng = random.Random(seed)
    n = len(predictions)
    f1s = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        preds = [predictions[i] for i in idx]
        golds = [gold_labels[i] for i in idx]
        f1s.append(macro_f1(preds, golds, support_labels))
    return f1s


def macro_f1(preds, golds, labels):
    """Compute macro F1 over the given label set."""
    if not labels:
        return 0.0
    f1s = []
    for label in labels:
        tp = sum(1 for p, g in zip(preds, golds) if p == label == label)
        fp = sum(1 for p, g in zip(preds, golds) if p == label and g != label)
        fn = sum(1 for p, g in zip(preds, golds) if p != label and g == label)
        _, _, f = pr_metrics(tp, fp, fn)
        f1s.append(f)
    return sum(f1s) / len(f1s)


def main():
    with open("data/swe_prbench/converted.json") as f:
        prs = json.load(f)

    print(f"SWE-PRBench eval: {len(prs)} PRs across {len(set(p['language'] for p in prs))} languages")

    # Run classifier on each PR
    predictions = []
    gold_labels = []
    per_pr = []
    skipped = []
    for p in prs:
        gold_raw = p["gold_intent"]
        gold = GOLD_TO_INTENT.get(gold_raw, gold_raw)
        # Skip labels we don't have in our taxonomy yet
        if gold not in {"feature", "bug_fix", "refactor", "perf", "security_patch"}:
            skipped.append((p["task_id"], gold_raw))
            continue

        result = classify_pr_ontology(
            title=p.get("title", ""),
            body=p.get("body", ""),
            file_paths=p.get("file_paths", []),
            diff=p.get("diff", ""),
            additions=p.get("lines_added", 0),
            deletions=p.get("lines_removed", 0),
        )
        pred = result.pr_intent
        predictions.append(pred)
        gold_labels.append(gold)
        per_pr.append((p, gold, pred, result))

    print(f"Classified {len(predictions)} PRs (skipped {len(skipped)} unsupported labels)")
    if skipped:
        print(f"  Skipped labels: {Counter(s for _, s in skipped).most_common()}")

    # Per-intent metrics
    print("\n" + "=" * 70)
    print("PER-INTENT (all PRs)")
    print("=" * 70)
    print(f"{'Intent':18s} {'P':>5s} {'R':>5s} {'F1':>5s} {'TP':>4s} {'FP':>4s} {'FN':>4s} {'sup':>4s}")
    print("-" * 70)
    labels_present = sorted(set(predictions) | set(gold_labels))
    intent_results = {}
    for label in labels_present:
        tp = sum(1 for p, g in zip(predictions, gold_labels) if p == label and g == label)
        fp = sum(1 for p, g in zip(predictions, gold_labels) if p == label and g != label)
        fn = sum(1 for p, g in zip(predictions, gold_labels) if p != label and g == label)
        sup = sum(1 for g in gold_labels if g == label)
        p, r, f = pr_metrics(tp, fp, fn)
        intent_results[label] = (p, r, f, tp, fp, fn, sup)
        print(f"{label:18s} {p:5.2f} {r:5.2f} {f:5.2f} {tp:4d} {fp:4d} {fn:4d} {sup:4d}")

    # Macro F1 (over labels with support >= 3)
    sup_labels = [l for l in labels_present if intent_results[l][6] >= 3]
    macro = sum(intent_results[l][2] for l in sup_labels) / len(sup_labels) if sup_labels else 0
    print(f"\nMacro F1 (support>=3): {macro:.3f}")

    # Bootstrap CI
    print("\nBootstrap 95% CI on macro F1 (1000 resamples)...")
    f1s = bootstrap_macro_f1(predictions, gold_labels, sup_labels, n_boot=1000)
    f1s.sort()
    lo = f1s[int(0.025 * len(f1s))]
    hi = f1s[int(0.975 * len(f1s))]
    med = statistics.median(f1s)
    print(f"  Median macro F1: {med:.3f}")
    print(f"  95% CI: [{lo:.3f}, {hi:.3f}]")

    # Per-language
    print("\n" + "=" * 70)
    print("PER-LANGUAGE")
    print("=" * 70)
    lang_buckets = defaultdict(lambda: ([], []))
    for p, gold, pred, _ in per_pr:
        lang_buckets[p["language"]][0].append(pred)
        lang_buckets[p["language"]][1].append(gold)
    for lang in sorted(lang_buckets.keys()):
        preds, golds = lang_buckets[lang]
        all_labels = set(preds) | set(golds)
        m = macro_f1(preds, golds, all_labels)
        print(f"  {lang:14s} n={len(preds):3d}  macro F1={m:.3f}")

    # Per-difficulty
    print("\n" + "=" * 70)
    print("PER-DIFFICULTY (Type1/2/3)")
    print("=" * 70)
    diff_buckets = defaultdict(lambda: ([], []))
    for p, gold, pred, _ in per_pr:
        d = p.get("difficulty", "Unknown")
        if not d:
            d = "Unknown"
        diff_buckets[d][0].append(pred)
        diff_buckets[d][1].append(gold)
    for d in sorted(diff_buckets.keys()):
        preds, golds = diff_buckets[d]
        all_labels = set(preds) | set(golds)
        m = macro_f1(preds, golds, all_labels)
        print(f"  {d:22s} n={len(preds):3d}  macro F1={m:.3f}")

    # Confusion matrix (top off-diagonal)
    print("\n" + "=" * 70)
    print("CONFUSION MATRIX (top misclassifications)")
    print("=" * 70)
    confusion = Counter()
    for pred, gold in zip(predictions, gold_labels):
        confusion[(gold, pred)] += 1
    print(f"{'gold':>14s} {'pred':>14s} {'count':>5s}")
    print("-" * 36)
    for (g, p), c in confusion.most_common(15):
        marker = "  " if g == p else "**"
        print(f"{marker}{g:>14s} {p:>14s} {c:>5d}")

    # Final headline
    print("\n" + "=" * 70)
    print("HEADLINE")
    print("=" * 70)
    print(f"Macro F1:           {macro:.3f}")
    print(f"Macro F1 95% CI:    [{lo:.3f}, {hi:.3f}]  (bootstrap, n=1000)")
    print(f"Total PRs:          {len(predictions)}")
    print(f"Repos:              {len(set(p['repo'] for p, _, _, _ in per_pr))}")
    print(f"Languages:          {len(lang_buckets)}")
    print(f"Correct:            {sum(1 for p, g in zip(predictions, gold_labels) if p == g)}/{len(predictions)} = {100*sum(1 for p, g in zip(predictions, gold_labels) if p == g)/len(predictions):.1f}%")


if __name__ == "__main__":
    main()