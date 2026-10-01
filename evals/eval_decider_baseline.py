"""
Run decider-2b (or 4b) on the held-out 71-PR test set as a baseline.

This is the "open-weight Jev" baseline — what we want to beat.

Uses decider's typed question interface: for each PR, we ask:
  - 1 Choice question with 11 intent options
  - 1 Choice question with 5 tier options
  - 9 Noul questions (one per risk flag)

We measure top-1 accuracy on intent vs gold label.
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


def build_decider_state(pr: dict) -> dict:
    """Build a state dict for decider from a PR — same format as
    src/decider_adapter.py for consistency."""
    files = pr.get("file_paths", [])
    diff_size = len(pr.get("diff", ""))
    body_excerpt = (pr.get("body", "") or "")[:1000]

    # File extensions and path keywords
    exts = sorted(set(fp.rsplit(".", 1)[-1] if "." in fp else "no-ext" for fp in files))
    path_kws_set = {"test", "tests", "docs", "doc", "ci", "build", "scripts", "tools",
                    "src", "lib", "auth", "migrations", "workflows", "examples"}
    path_kws = sorted(set(
        seg for fp in files for seg in fp.split("/")
        if seg and seg.lower() in path_kws_set
    ))

    return {
        "title": pr.get("title", ""),
        "body_excerpt": body_excerpt,
        "file_count": len(files),
        "primary_extensions": ",".join(exts[:5]),
        "path_keywords": ",".join(path_kws[:5]),
        "diff_size": diff_size,
    }


def state_to_text(state: dict) -> str:
    """Flatten state dict to text for decider."""
    parts = []
    if state.get("title"):
        parts.append(f"TITLE: {state['title']}")
    if state.get("body_excerpt"):
        parts.append(f"BODY: {state['body_excerpt']}")
    parts.append(f"FILES: ext={state.get('primary_extensions', '')} kws={state.get('path_keywords', '')}")
    parts.append(f"DIFF_SIZE: {state.get('diff_size', 0)}")
    return " | ".join(parts)


INTENT_OPTS = [
    "bug_fix", "feature", "docs", "test", "build_ci",
    "chore", "perf", "refactor", "revert", "security_patch", "mixed_or_unclear",
]


def main():
    print("Loading decider-2b on GPU...")
    from decider.infer import Decider
    d = Decider('Mapika/decider-2b', device='cuda')
    print("Loaded.")

    prs = load_test_prs()
    print(f"Loaded {len(prs)} test PRs")

    correct = 0
    confusion = {}
    total_t = 0.0

    for i, pr in enumerate(prs):
        gold = pr["gold"]
        state = build_decider_state(pr)
        context = state_to_text(state)

        questions = [{
            "question": "What is the primary intent of this pull request?",
            "options": INTENT_OPTS,
        }]

        t0 = time.time()
        try:
            results = d.decide(context, questions)
            pred = results[0].get("choice", "mixed_or_unclear")
            pred_conf = results[0].get("confidence", 0.0)
        except Exception as e:
            print(f"[{i}] Error: {e}")
            pred = "mixed_or_unclear"
            pred_conf = 0.0
        elapsed = time.time() - t0
        total_t += elapsed

        if pred == gold:
            correct += 1
        confusion.setdefault(gold, {}).setdefault(pred, 0)
        confusion[gold][pred] += 1

        if (i + 1) % 10 == 0:
            print(f"  [{i+1}/{len(prs)}] running accuracy: {correct/(i+1):.3f}, "
                  f"avg time: {total_t/(i+1)*1000:.1f}ms")

    n = len(prs)
    print(f"\n=== Decider-2b intent on {n} held-out PRs ===")
    print(f"Top-1 accuracy: {correct}/{n} = {correct/n:.3f}")
    print(f"Avg time per PR: {total_t/n*1000:.1f}ms")
    print(f"\nConfusion:")
    for gold, preds in sorted(confusion.items()):
        print(f"  {gold}: {dict(sorted(preds.items()))}")


if __name__ == "__main__":
    main()