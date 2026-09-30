"""
Validate typesafe_classifier against the labeled public dataset.

Mapping rule (classifier kind -> expected ground-truth label set):
  - bug_fix            -> {"bug"}
  - feature            -> {"feature"}
  - refactor           -> {"refactor"}
  - docs_only          -> {"docs"}
  - ci_workflow        -> {"ci"}
  - auth_or_security   -> anything auth/secret-touching (rare in this dataset)
  - schema_or_migration -> {"bug"} as a proxy (rare)
  - dependency_upgrade -> {"bug"} if breaking, else ambiguous
  - config_or_secret   -> {"bug"} as proxy
  - infra_or_k8s       -> {"ci"} as proxy
  - test_only          -> (no clear mapping — excluded)
  - content_or_marketing -> (no clear mapping — excluded)
  - experimental_branch -> (no clear mapping — excluded)
  - mixed_or_unclear   -> (no clear mapping — excluded)

We report:
  - precision: of the rows classifier said were X, how many actually were X?
  - recall:    of the rows actually X, how many did the classifier catch?
  - f1
  - confusion matrix
  - macro avg
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from src.classifier_v1 import classify_pr


# Map classifier kind -> {expected labels}
KIND_TO_LABELS: dict[str, set[str]] = {
    "bug_fix": {"bug"},
    "feature": {"feature"},
    "refactor": {"refactor"},
    "docs_only": {"docs"},
    "ci_workflow": {"ci"},
    "auth_or_security": set(),  # no good proxy in dataset
    "schema_or_migration": {"bug"},  # migration bugs surface as bugs
    "dependency_upgrade": set(),
    "config_or_secret": {"bug"},  # secret misconfig surfaces as bug
    "infra_or_k8s": {"ci"},
    "test_only": set(),
    "content_or_marketing": set(),
    "experimental_branch": set(),
    "mixed_or_unclear": set(),
}


def evaluate(rows: list[dict]) -> dict:
    """Run classifier on every row, compute per-class precision/recall/f1."""
    # Build (kind, gold-label-set) pairs
    paired: list[tuple[str, str]] = []
    for r in rows:
        title = r["title"]
        body = r.get("body", "")
        # The dataset has no file_paths — feed empty list, classifier will fall
        # back to title heuristics (which is exactly what we want to measure).
        result = classify_pr(
            title=title,
            body=body,
            file_paths=[],
            diff="",
            branch="",
            additions=0,
            deletions=0,
        )
        kind = result.pr_kind
        gold = set(r["labels"])
        # Map the gold labels to a single canonical one (most "important")
        if not gold:
            continue
        # Pick the first primary label in priority order
        priority = ["bug", "feature", "docs", "refactor", "ci"]
        gold_label = None
        for p in priority:
            if p in gold:
                gold_label = p
                break
        if gold_label is None:
            continue

        expected = KIND_TO_LABELS.get(kind, set())
        # For each row, ask: did the classifier pick a kind that "expects" this gold label?
        if not expected:
            continue  # kind has no clear mapping; skip this row
        paired.append((kind, gold_label, gold_label in expected))

    # Per-class report
    by_class_correct = Counter()
    by_class_predicted = Counter()
    by_class_gold = Counter()
    by_kind_predicted = Counter()

    for kind, gold_label, correct in paired:
        by_class_predicted[kind] += 1
        by_class_gold[gold_label] += 1
        by_kind_predicted[kind] += 1
        if correct:
            by_class_correct[kind] += 1

    # Build confusion: kind -> {gold_label: count}
    confusion: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        title = r["title"]; body = r.get("body", "")
        result = classify_pr(title=title, body=body, file_paths=[], diff="", branch="")
        gold = set(r["labels"])
        if not gold:
            continue
        priority = ["bug", "feature", "docs", "refactor", "ci"]
        gold_label = next((p for p in priority if p in gold), None)
        if gold_label is None:
            continue
        confusion[result.pr_kind][gold_label] += 1

    return {
        "n_rows_evaluated": len(paired),
        "by_class_predicted": dict(by_class_predicted),
        "by_class_gold": dict(by_class_gold),
        "confusion": {k: dict(v) for k, v in confusion.items()},
    }


if __name__ == "__main__":
    with open("data/labeled_dataset.json") as f:
        rows = json.load(f)
    print(f"Loaded {len(rows)} labeled rows")
    report = evaluate(rows)
    print(f"\nEvaluated on {report['n_rows_evaluated']} rows")
    print("\nClassifier -> predicted kind counts:")
    for k, v in sorted(report["by_class_predicted"].items(), key=lambda x: -x[1]):
        print(f"  {k:25s} {v:5d}")
    print("\nGold label counts:")
    for k, v in sorted(report["by_class_gold"].items(), key=lambda x: -x[1]):
        print(f"  {k:25s} {v:5d}")
    print("\nConfusion matrix (rows = predicted kind, cols = gold label):")
    KINDS = ["bug_fix", "feature", "refactor", "docs_only", "ci_workflow",
             "schema_or_migration", "config_or_secret", "infra_or_k8s",
             "auth_or_security", "mixed_or_unclear", "dependency_upgrade"]
    GOLD = ["bug", "feature", "docs", "refactor", "ci"]
    header = "  " + " ".join(f"{g:>10s}" for g in GOLD) + " |  total"
    print(header)
    print("-" * len(header))
    for k in KINDS:
        row_counts = [report["confusion"].get(k, {}).get(g, 0) for g in GOLD]
        total = sum(row_counts)
        if total == 0:
            continue
        line = f"{k:20s} " + " ".join(f"{c:>10d}" for c in row_counts) + f" | {total:>5d}"
        print(line)

    # Per-class precision/recall where mapping is unambiguous
    print("\nPer-class precision/recall (unambiguous kinds only):")
    UNAMBIG = {
        "bug_fix": "bug", "feature": "feature", "refactor": "refactor",
        "docs_only": "docs", "ci_workflow": "ci",
        "schema_or_migration": "bug", "config_or_secret": "bug",
        "infra_or_k8s": "ci",
    }
    f1s = []
    for kind, gold_label in UNAMBIG.items():
        cm = report["confusion"].get(kind, {})
        tp = cm.get(gold_label, 0)
        fp = sum(cm.values()) - tp
        # recall: of all gold rows, how many did we assign this kind?
        # the gold pool = rows where the gold label appears anywhere
        fn = report["by_class_gold"].get(gold_label, 0) - tp
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        f1s.append(f1)
        print(f"  {kind:20s} -> gold={gold_label:10s}  P={precision:.2f}  R={recall:.2f}  F1={f1:.2f}  (TP={tp} FP={fp} FN={fn})")
    print(f"  Macro F1: {sum(f1s)/len(f1s):.3f}")