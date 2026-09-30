"""
Validate the v1 (15-kind enum) classifier against oven-sh/bun PRs (real diffs,
real labels).

Same mapping as validate_v1_on_issues.py, but with Bun's label taxonomy.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from src.classifier_v1 import classify_pr


BUN_LABEL_TO_KIND = {
    "bug": "bug_fix",
    "enhancement": "feature",
    "docs": "docs_only",
    "performance": "mixed_or_unclear",  # perf work spans many kinds
    "build": "ci_workflow",
    "breaking": "feature",  # breaking changes are usually features
    "chore": "refactor",
    "typescript": "refactor",  # type work = refactor
    "transpiler": "feature",
    "bundler": "feature",
    "bun:test": "test_only",
    "bun:ffi": "feature",
    "node:fs": "feature",
    "cli": "feature",
    "wasm": "feature",
    "infrastructure": "infra_or_k8s",
}


def get_gold_kind(pr: dict) -> str | None:
    """Pick the gold classifier kind from a Bun PR's labels."""
    labels = pr.get("labels") or []
    # Priority order: most specific first
    priority = ["bug", "breaking", "performance", "docs", "build",
                "typescript", "chore", "transpiler", "bundler", "bun:test",
                "bun:ffi", "node:fs", "cli", "wasm", "enhancement",
                "infrastructure"]
    for p in priority:
        for lab in labels:
            if lab.get("name") == p:
                return BUN_LABEL_TO_KIND[p]
    return None


def evaluate(prs: list[dict], mode: str) -> dict:
    """Run classifier over each PR and compute per-bucket scores.

    mode: 'full' uses file_paths + diff + title + branch
          'title-only' uses only title (no files/diff)
    """
    confusion: dict[str, Counter] = defaultdict(Counter)
    by_kind_predicted = Counter()
    by_kind_gold = Counter()
    rows = []

    for pr in prs:
        gold = get_gold_kind(pr)
        if gold is None:
            continue
        if mode == "full":
            cls = classify_pr(
                title=pr.get("title", ""),
                body=pr.get("body", "") or "",
                file_paths=pr.get("all_file_paths", []),
                diff=pr.get("diff", ""),
                branch=pr.get("headRefName", ""),
                additions=pr.get("additions", 0),
                deletions=pr.get("deletions", 0),
            )
        else:
            cls = classify_pr(
                title=pr.get("title", ""),
                body=pr.get("body", "") or "",
                file_paths=[],
                diff="",
                branch="",
                additions=0,
                deletions=0,
            )
        pred = cls.pr_kind
        confusion[pred][gold] += 1
        by_kind_predicted[pred] += 1
        by_kind_gold[gold] += 1
        rows.append((pr["number"], pr.get("title", ""), gold, pred, cls.confidence, cls.risk_score))

    return {
        "mode": mode,
        "n": len(rows),
        "confusion": {k: dict(v) for k, v in confusion.items()},
        "by_predicted": dict(by_kind_predicted),
        "by_gold": dict(by_kind_gold),
        "rows": rows,
    }


def score(report: dict) -> dict:
    """Compute per-bucket precision/recall/F1 + macro F1."""
    confusion = report["confusion"]
    by_gold = report["by_gold"]

    per_class = {}
    f1s = []
    for kind in sorted(set(list(confusion.keys()) + list(by_gold.keys()))):
        cm = confusion.get(kind, {})
        total_predicted = sum(cm.values())
        total_gold = by_gold.get(kind, 0)
        tp = cm.get(kind, 0)
        fp = total_predicted - tp
        fn = total_gold - tp
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        per_class[kind] = {
            "P": round(precision, 3),
            "R": round(recall, 3),
            "F1": round(f1, 3),
            "TP": tp, "FP": fp, "FN": fn,
            "support": total_gold,
        }
        if total_gold >= 3:  # only include classes with reasonable support in macro avg
            f1s.append(f1)
    macro_f1 = sum(f1s) / len(f1s) if f1s else 0.0
    return {"per_class": per_class, "macro_F1": round(macro_f1, 3)}


def print_report(report: dict, scores: dict):
    mode = report["mode"]
    n = report["n"]
    print(f"\n{'='*70}")
    print(f"MODE: {mode}  (n={n} PRs)")
    print(f"{'='*70}")
    print(f"\n{'KIND':<22} {'P':>5} {'R':>5} {'F1':>5} {'TP':>4} {'FP':>4} {'FN':>4} {'support':>8}")
    print("-" * 60)
    for kind, s in sorted(scores["per_class"].items(), key=lambda x: -x[1]["support"]):
        if s["support"] == 0:
            continue
        print(f"{kind:<22} {s['P']:>5.2f} {s['R']:>5.2f} {s['F1']:>5.2f} {s['TP']:>4d} {s['FP']:>4d} {s['FN']:>4d} {s['support']:>8d}")
    print(f"\nMacro F1 (kinds with support>=3): {scores['macro_F1']:.3f}")

    print(f"\nConfusion matrix (rows = predicted, cols = gold):")
    GOLD_KINDS = ["bug_fix", "feature", "docs_only", "ci_workflow",
                  "infra_or_k8s", "refactor", "test_only", "mixed_or_unclear"]
    header = "  " + " ".join(f"{g[:8]:>9s}" for g in GOLD_KINDS) + " | total"
    print(header)
    print("-" * len(header))
    for kind in sorted(report["confusion"].keys()):
        cm = report["confusion"][kind]
        row = " ".join(f"{cm.get(g, 0):>9d}" for g in GOLD_KINDS)
        total = sum(cm.values())
        if total == 0:
            continue
        print(f"{kind[:21]:<22} {row} | {total:>5d}")


def main():
    with open("data/bun_prs.json") as f:
        prs = json.load(f)

    # Filter to PRs that have at least one of our mappable labels
    mappable = [p for p in prs if get_gold_kind(p) is not None]
    print(f"Total Bun PRs fetched: {len(prs)}")
    print(f"Mappable to a gold classifier kind: {len(mappable)}")

    # Evaluate both modes
    full_report = evaluate(mappable, mode="full")
    full_scores = score(full_report)
    print_report(full_report, full_scores)

    title_report = evaluate(mappable, mode="title-only")
    title_scores = score(title_report)
    print_report(title_report, title_scores)

    # Side-by-side comparison: kinds where full >> title
    print(f"\n{'='*70}")
    print("FULL-MODE vs TITLE-ONLY-MODE: which kinds benefit from diff signals?")
    print(f"{'='*70}")
    print(f"{'KIND':<22} {'F1 (title)':>12} {'F1 (full)':>12} {'delta':>8} {'support':>8}")
    print("-" * 65)
    all_kinds = sorted(set(full_scores["per_class"]) | set(title_scores["per_class"]))
    for kind in all_kinds:
        ft = title_scores["per_class"].get(kind, {}).get("F1", 0)
        ff = full_scores["per_class"].get(kind, {}).get("F1", 0)
        sup = full_scores["per_class"].get(kind, {}).get("support", 0)
        delta = ff - ft
        marker = "+" if delta > 0 else ("=" if delta == 0 else "")
        print(f"{kind:<22} {ft:>12.3f} {ff:>12.3f} {marker}{delta:>+7.3f} {sup:>8d}")

    # Save the report
    with open("bun_evaluation.json", "w") as f:
        json.dump({
            "full": {"scores": full_scores, "report": {k: v for k, v in full_report.items() if k != "rows"}},
            "title_only": {"scores": title_scores, "report": {k: v for k, v in title_report.items() if k != "rows"}},
        }, f, indent=2, default=str)
    print("\nWrote bun_evaluation.json")


if __name__ == "__main__":
    main()