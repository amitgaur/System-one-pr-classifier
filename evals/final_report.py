"""
Final eval report combining SWE-PRBench + Bun + diff_trumps_title invariant tests.

Reads all eval outputs and prints a summary table.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO = Path(".")
SWE_JSON = REPO / "data/swe_prbench/converted.json"


def run(cmd: list[str]) -> dict:
    """Run a Python script and parse its stdout lines starting with 'HEADLINE:' if present."""
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    out = r.stdout
    info = {}
    for line in out.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip().lower()
            v = v.strip()
            if k in ("macro f1", "bug_fix f1", "feature f1", "test f1",
                     "docs f1", "build_ci f1", "perf f1", "refactor f1",
                     "macro f1 95% ci", "median macro f1",
                     "total prs", "correct", "repos", "languages"):
                info[k] = v
    return info


def main():
    print("=" * 70)
    print("FINAL EVAL REPORT")
    print("=" * 70)

    print("\n--- SWE-PRBench (350 PRs, 65 repos, 5 languages) ---")
    sw = run(["python3", "evals/validate_swe_prbench.py"])
    for k, v in sw.items():
        print(f"  {k:25s} {v}")

    print("\n--- oven-sh/bun (55 PRs, single repo) ---")
    bun = run(["python3", "evals/validate_v2_on_bun.py"])
    for k, v in bun.items():
        print(f"  {k:25s} {v}")

    print("\n--- diff_trumps_title invariant (7 hand-crafted cases) ---")
    r = subprocess.run(["python3", "evals/diff_trumps_title.py"],
                       capture_output=True, text=True, cwd=REPO)
    if "All diff-trumps-title invariants hold" in r.stdout:
        print(f"  Status: PASS")
    else:
        print(f"  Status: FAIL")
        print(r.stdout[-1000:])

    print("\n--- adversarial_tests (bun:test + chore regression cases) ---")
    r = subprocess.run(["python3", "evals/adversarial_tests.py"],
                       capture_output=True, text=True, cwd=REPO)
    if "All adversarial" in r.stdout or "passed" in r.stdout:
        # Print just the summary line
        for line in r.stdout.splitlines():
            if "passed" in line.lower() or "failed" in line.lower():
                print(f"  {line.strip()}")
                break
    else:
        print(f"  {r.stdout[-1000:]}")

    print("\n" + "=" * 70)
    print("HEADLINE")
    print("=" * 70)
    if "macro f1" in sw:
        print(f"SWE-PRBench macro F1:    {sw.get('macro f1')} (CI: {sw.get('macro f1 95% ci', 'n/a')})")
    if "macro f1" in bun:
        print(f"Bun macro F1:            {bun.get('macro f1')}")
    print(f"\nRepo:  github.com/amitgaur/System-one-pr-classifier")
    print(f"Commit: see git log")


if __name__ == "__main__":
    main()