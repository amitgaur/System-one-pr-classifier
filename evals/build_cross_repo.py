"""
Cross-repo PR corpus builder.

Pulls labeled PRs from multiple diverse public repos. Each repo gets ~20-30 PRs
across their label taxonomy.

Why: a single repo's labels are biased. Bun (Rust+JS runtime) is dominated by
app_code + test_code. A Python ML repo will have different surface ratios.
A docs-only repo will have no test code at all. We need diversity to claim
"the classifier works on real PRs in general".

Repos chosen for label-taxonomy coverage of our 8 mappable intents:
  - oven-sh/bun: bug, enhancement, docs, build, breaking, chore, typescript,
    transpiler, bundler, bun:test, bun:ffi, node:fs, cli, wasm
  - vercel/next.js: bug, enhancement, docs (TypeScript/Rust, web framework)
  - huggingface/transformers: docs, Tests (Python ML — very different surface)
  - expressjs/express: bug, deps, enhancement, docs (Node.js minimal API)
  - kubernetes/kubernetes: bug, feature, docs, ci, area/, sig/ (K8s infra)

Each PR gets fetched with title, body, files, diff, labels, additions/deletions.
Stored as JSON in data/cross_repo/<repo>_<num>.json.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from collections import Counter


REPOS = [
    "vercel/next.js",
    "huggingface/transformers",
    "expressjs/express",
    "kubernetes/kubernetes",
]

# Labels to search for. We pick labels whose semantics align with our
# 8 mappable intents (bug_fix, feature, docs, test, build_ci, chore,
# refactor, perf). Some labels are repo-specific and we'll have to map
# them via the gold map in the eval.
LABELS_PER_REPO = {
    "vercel/next.js": ["bug", "enhancement", "docs", "typescript"],
    "huggingface/transformers": ["Documentation", "Tests"],
    "expressjs/express": ["bug", "enhancement", "deps", "docs"],
    "kubernetes/kubernetes": ["bug", "feature", "docs", "area/dependency", "area/test"],
}


def search_prs(repo: str, label: str, limit: int = 10) -> list[dict]:
    """Find closed PRs with a given label on a repo."""
    out = subprocess.run(
        [
            "gh", "search", "prs",
            "--repo", repo,
            "--label", label,
            "--state", "closed",
            "--limit", str(limit),
            "--json", "number,title,labels,url",
        ],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        print(f"  search failed for {repo} {label}: {out.stderr.strip()}")
        return []
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return []


def fetch_pr(repo: str, num: int) -> dict | None:
    """Fetch full PR metadata + diff + files."""
    out = subprocess.run(
        [
            "gh", "pr", "view", str(num), "--repo", repo,
            "--json", "number,title,body,baseRefName,labels,files,additions,"
                       "deletions,changedFiles,createdAt,closedAt,mergedAt",
        ],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return None
    try:
        meta = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None

    files_out = subprocess.run(
        ["gh", "pr", "diff", str(num), "--repo", repo, "--name-only"],
        capture_output=True, text=True,
    )
    meta["all_file_paths"] = [l.strip() for l in files_out.stdout.splitlines() if l.strip()]

    diff_out = subprocess.run(
        ["gh", "pr", "diff", str(num), "--repo", repo],
        capture_output=True, text=True,
    )
    meta["diff"] = diff_out.stdout[:100_000]
    return meta


def main():
    out_dir = Path("data/cross_repo")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Track unique PRs across all repos and labels
    seen = set()
    collected = []

    for repo in REPOS:
        print(f"\n=== {repo} ===")
        for label in LABELS_PER_REPO.get(repo, []):
            print(f"  searching {label}...", end=" ")
            hits = search_prs(repo, label, limit=10)
            print(f"{len(hits)} hits")

            for h in hits:
                n = h["number"]
                if (repo, n) in seen:
                    continue
                seen.add((repo, n))

                meta = fetch_pr(repo, n)
                if not meta:
                    continue
                # Filter: must have file paths and diff
                if not meta.get("all_file_paths") or not meta.get("diff"):
                    continue
                meta["_repo"] = repo
                meta["_label_seed"] = label
                collected.append(meta)
                time.sleep(0.15)  # gentle on rate limits
                if len(collected) % 10 == 0:
                    print(f"    collected {len(collected)} so far")

    print(f"\nTotal collected: {len(collected)} PRs from {len(REPOS)} repos")
    print(f"By repo:")
    repo_counts = Counter(p["_repo"] for p in collected)
    for r, n in repo_counts.most_common():
        print(f"  {r}: {n}")

    # Save as one JSON file
    out_file = out_dir / "all_prs.json"
    with open(out_file, "w") as f:
        json.dump(collected, f)
    print(f"\nSaved to {out_file}")

    # Also save the index
    index = [{"repo": p["_repo"], "number": p["number"],
              "title": p["title"], "labels": [l["name"] for l in p["labels"]],
              "label_seed": p["_label_seed"]} for p in collected]
    with open(out_dir / "index.json", "w") as f:
        json.dump(index, f, indent=2)


if __name__ == "__main__":
    main()