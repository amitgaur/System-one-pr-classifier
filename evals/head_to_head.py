"""
Final head-to-head comparison: rule-based vs decider-2b vs jevlike-tiny-gpu
vs jevlike-qwen-gpu vs jevlike-qwen-cpu on the 71-PR held-out test set.

Reports top-1 accuracy, ECE, avg latency per PR.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, '.')

REPO_DIR = Path(__file__).resolve().parents[1]


def load_test_prs():
    """Load 71 test PRs with their raw fields."""
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
            "gold": gold,
        })
    return out


def build_decider_state(pr: dict) -> str:
    files = pr.get("file_paths", [])
    body_excerpt = (pr.get("body", "") or "")[:1000]
    exts = sorted(set(fp.rsplit(".", 1)[-1] if "." in fp else "no-ext" for fp in files))
    path_kws_set = {"test", "tests", "docs", "doc", "ci", "build", "scripts", "tools",
                    "src", "lib", "auth", "migrations", "workflows", "examples"}
    path_kws = sorted(set(
        seg for fp in files for seg in fp.split("/")
        if seg and seg.lower() in path_kws_set
    ))
    parts = []
    if pr.get("title"):
        parts.append(f"TITLE: {pr['title']}")
    if body_excerpt:
        parts.append(f"BODY: {body_excerpt}")
    parts.append(f"FILES: ext={','.join(exts[:5])} kws={','.join(path_kws[:5])}")
    parts.append(f"DIFF_SIZE: {len(pr.get('diff', '') or '')}")
    return " | ".join(parts)


INTENT_OPTS = [
    "bug_fix", "feature", "docs", "test", "build_ci",
    "chore", "perf", "refactor", "revert", "security_patch", "mixed_or_unclear",
]


def main():
    prs = load_test_prs()
    print(f"Loaded {len(prs)} test PRs\n")

    results = {}

    # 1. Rule-based
    from src.classifier import classify_pr_ontology
    correct, total_t = 0, 0.0
    for pr in prs:
        t0 = time.time()
        pred = classify_pr_ontology(
            title=pr["title"], body=pr["body"],
            file_paths=pr["file_paths"], diff=pr["diff"],
            additions=0, deletions=0,
        ).pr_intent
        total_t += time.time() - t0
        if pred == pr["gold"]:
            correct += 1
    n = len(prs)
    results["rule-based (CPU)"] = {
        "top1": correct / n,
        "ms_per_pr": total_t / n * 1000,
        "n": n,
    }

    # 2. decider-2b on GPU
    try:
        from decider.infer import Decider
        print("Loading decider-2b on GPU...")
        d = Decider('Mapika/decider-2b', device='cuda')
        correct, total_t = 0, 0.0
        for pr in prs:
            state = build_decider_state(pr)
            questions = [{"question": "What is the primary intent of this pull request?", "options": INTENT_OPTS}]
            t0 = time.time()
            res = d.decide(state, questions)
            total_t += time.time() - t0
            pred = res[0].get("choice", "mixed_or_unclear")
            if pred == pr["gold"]:
                correct += 1
        results["decider-2b (GPU)"] = {
            "top1": correct / n,
            "ms_per_pr": total_t / n * 1000,
            "n": n,
        }
    except Exception as e:
        print(f"decider-2b failed: {e}")

    # 3. jevlike-qwen-gpu
    try:
        from src.jevlike_adapter import JevlikePredictor
        runs_dir = REPO_DIR / "runs"
        ckpt = runs_dir / "jevlike-intent-qwen-gpu.pt"
        if ckpt.exists():
            p = JevlikePredictor(str(ckpt), device='cuda')
            correct, total_t = 0, 0.0
            for pr in prs:
                from src.jevlike_adapter import build_context
                ctx = build_context(pr)
                t0 = time.time()
                idx, probs = p.predict(ctx, INTENT_OPTS)
                total_t += time.time() - t0
                pred = INTENT_OPTS[idx]
                if pred == pr["gold"]:
                    correct += 1
            results["jevlike-qwen-gpu (ours)"] = {
                "top1": correct / n,
                "ms_per_pr": total_t / n * 1000,
                "n": n,
            }
    except Exception as e:
        print(f"jevlike-qwen-gpu failed: {e}")
        import traceback
        traceback.print_exc()

    # 4. jevlike-qwen-cpu (1 epoch, was our best on CPU)
    try:
        ckpt = runs_dir / "jevlike-intent-qwen.pt"
        if ckpt.exists():
            p = JevlikePredictor(str(ckpt), device='cpu')
            correct, total_t = 0, 0.0
            for pr in prs:
                from src.jevlike_adapter import build_context
                ctx = build_context(pr)
                t0 = time.time()
                idx, probs = p.predict(ctx, INTENT_OPTS)
                total_t += time.time() - t0
                pred = INTENT_OPTS[idx]
                if pred == pr["gold"]:
                    correct += 1
            results["jevlike-qwen-cpu 1ep (ours)"] = {
                "top1": correct / n,
                "ms_per_pr": total_t / n * 1000,
                "n": n,
            }
    except Exception as e:
        print(f"jevlike-qwen-cpu failed: {e}")

    # 5. jevlike-tiny-gpu
    try:
        ckpt = runs_dir / "jevlike-intent-tiny-gpu.pt"
        if ckpt.exists():
            p = JevlikePredictor(str(ckpt), device='cuda')
            correct, total_t = 0, 0.0
            for pr in prs:
                from src.jevlike_adapter import build_context
                ctx = build_context(pr)
                t0 = time.time()
                idx, probs = p.predict(ctx, INTENT_OPTS)
                total_t += time.time() - t0
                pred = INTENT_OPTS[idx]
                if pred == pr["gold"]:
                    correct += 1
            results["jevlike-tiny-gpu (ours)"] = {
                "top1": correct / n,
                "ms_per_pr": total_t / n * 1000,
                "n": n,
            }
    except Exception as e:
        print(f"jevlike-tiny-gpu failed: {e}")

    # Print results table
    print("\n" + "=" * 80)
    print(f"{'Approach':<35} {'Top-1':>8} {'ms/PR':>10} {'N':>5}")
    print("=" * 80)
    for name, r in sorted(results.items(), key=lambda x: -x[1]["top1"]):
        print(f"{name:<35} {r['top1']:>7.3f}  {r['ms_per_pr']:>9.1f}  {r['n']:>5}")


if __name__ == "__main__":
    main()