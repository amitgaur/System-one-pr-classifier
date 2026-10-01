"""
Download SWE-PRBench from HuggingFace and convert it to our classifier input format.

SWE-PRBench format:
  - prs.jsonl: 350 PR records with metadata + diffs
  - annotations/<id>_human.json: ground-truth human reviewer comments

We collapse this into our format:
  {
    "title": pr.title,
    "body": pr.body,
    "all_file_paths": [...],
    "diff": pr.diff_patch,
    "labels": [...]  # derived from annotation comment types
  }

The label mapping is the trickiest part. SWE-PRBench human comments are issue
findings (one PR may have multiple findings of different types). We need to
map each issue type to one of our 8 intent labels.

Issue types in SWE-PRBench are derived from the human comments themselves
(not pre-labeled), so we'll classify each comment by keyword matching:
  - contains "bug", "error", "broken", "incorrect", "fix" -> bug_fix
  - contains "test", "missing test" -> test
  - contains "doc", "documentation" -> docs
  - contains "refactor", "naming", "style" -> refactor
  - contains "perf", "performance", "slow" -> perf
  - contains "chore", "format", "lint" -> chore
  - contains "security", "vuln", "CVE" -> security_patch
  - else -> feature (default)
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
import re


# Patterns matched in lowercase comment body
ISSUE_PATTERNS = {
    "bug_fix": [r"\bbug\b", r"\bbroken\b", r"\bincorrect\b", r"\bfix\b", r"\berror\b",
                r"\bfails?\b", r"\bcrash\b", r"\bnpe\b", r"\bnull pointer\b",
                r"\boff-?by-?one\b", r"\brace condition\b", r"\bmemory leak\b"],
    "test": [r"\badd a test\b", r"\bmissing test\b", r"\bunit test\b",
             r"\btest case\b", r"\bcoverage\b", r"\btest\b"],
    "docs": [r"\bdocumentation\b", r"\bdocs\b", r"\bdocstring\b",
             r"\breadme\b", r"\bcomment\b", r"\bexplain\b"],
    "refactor": [r"\brefactor\b", r"\brename\b", r"\bnaming\b", r"\bstyle\b",
                 r"\bclean up\b", r"\bsimplif\b", r"\bduplication\b"],
    "perf": [r"\bperf(ormance)?\b", r"\bslow\b", r"\boptimi[sz]e\b",
             r"\bbenchmark\b"],
    "chore": [r"\bchore\b", r"\bformat(ting)?\b", r"\blint\b", r"\bprettier\b",
              r"\bblack\b", r"\bisort\b"],
    "security_patch": [r"\bsecurity\b", r"\bvuln(erabilit)?y\b", r"\bcve\b",
                       r"\bsanitiz\b", r"\binjection\b", r"\bxss\b"],
}


def classify_comment(text: str) -> str:
    """Map a human reviewer comment to one of our intent labels."""
    t = text.lower()
    # Priority order matters - bug before test (a comment about test can mention bug)
    for label, pats in [
        ("security_patch", ISSUE_PATTERNS["security_patch"]),
        ("perf", ISSUE_PATTERNS["perf"]),
        ("refactor", ISSUE_PATTERNS["refactor"]),
        ("docs", ISSUE_PATTERNS["docs"]),
        ("chore", ISSUE_PATTERNS["chore"]),
        ("test", ISSUE_PATTERNS["test"]),
        ("bug_fix", ISSUE_PATTERNS["bug_fix"]),
    ]:
        for pat in pats:
            if re.search(pat, t):
                return label
    return "feature"  # default


def download_swe_prbench():
    """Use the HF datasets API to download SWE-PRBench to data/swe_prbench/."""
    out_dir = Path("data/swe_prbench")
    out_dir.mkdir(parents=True, exist_ok=True)

    class DateTimeEncoder(json.JSONEncoder):
        def default(self, obj):
            if hasattr(obj, 'isoformat'):
                return obj.isoformat()
            return super().default(obj)

    # Try via the HF datasets library
    try:
        from datasets import load_dataset
        print("Loading SWE-PRBench via datasets library...")
        ds = load_dataset("foundry-ai/swe-prbench", "prs", split="train")
        print(f"Loaded {len(ds)} PRs from 'prs' config")
        # Save as JSONL for portability
        with open(out_dir / "prs.jsonl", "w") as f:
            for item in ds:
                f.write(json.dumps(item, cls=DateTimeEncoder) + "\n")
        print(f"Saved to {out_dir / 'prs.jsonl'}")

        # Also load annotations config
        ann_ds = load_dataset("foundry-ai/swe-prbench", "eval_split", split="train")
        print(f"Loaded {len(ann_ds)} eval_split records")
        # Save these to a separate file
        with open(out_dir / "annotations.jsonl", "w") as f:
            for item in ann_ds:
                f.write(json.dumps(item, cls=DateTimeEncoder) + "\n")
        print(f"Saved to {out_dir / 'annotations.jsonl'}")

        return out_dir / "prs.jsonl", out_dir / "annotations.jsonl"
    except Exception as e:
        print(f"datasets.load_dataset failed: {e}")
        import traceback
        traceback.print_exc()
        raise


def load_annotations(annotations_path: Path | None) -> dict[str, dict]:
    """Load annotations from the JSONL file (eval_split config).

    Returns {task_id: {pr_type, human_review_comments, difficulty, language, ...}}.
    The eval_split config has 100 PRs with pr_type labels and comment bodies.
    """
    annotations = {}
    if not annotations_path or not annotations_path.exists():
        print(f"WARNING: annotations path {annotations_path} not found")
        return annotations
    with open(annotations_path) as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            tid = item.get("task_id") or item.get("instance_id")
            annotations[tid] = {
                "pr_type": item.get("pr_type"),
                "difficulty": item.get("difficulty"),
                "language": item.get("language"),
                "comments": [c.get("body", "") for c in item.get("human_review_comments", [])],
                "severity": item.get("severity"),
                "rvs_score": item.get("rvs_score"),
                "title": item.get("title"),
                "description": item.get("description"),
                "diff_patch": item.get("diff_patch"),
                "changed_files": item.get("changed_files", []),
            }
    print(f"Loaded {len(annotations)} annotation entries")
    return annotations


def convert(prs_path: Path, annotations: dict[str, dict], out_path: Path):
    """Convert SWE-PRBench records to our internal format.

    The prs.jsonl already contains pr_type labels for all 350 PRs.
    The eval_split config (100 PRs) has additional reviewer comments.
    """
    converted = []
    with open(prs_path) as f:
        for line in f:
            if not line.strip():
                continue
            pr = json.loads(line)
            tid = pr.get("task_id")
            ann = annotations.get(tid, {})

            # pr_type is in prs.jsonl for all 350 PRs
            gold_intent = pr.get("pr_type") or ann.get("pr_type") or "feature"
            comments = ann.get("comments", [])

            # File paths: prefer from annotation (cleaner), else parse diff
            file_paths = pr.get("changed_files", [])
            if not file_paths:
                file_paths = re.findall(r"^\+\+\+ b/(.+)$", pr.get("diff_patch", ""), re.M)

            converted.append({
                "task_id": tid,
                "title": pr.get("title") or "",
                "body": pr.get("description") or "",
                "file_paths": file_paths,
                "diff": pr.get("diff_patch", ""),
                "language": pr.get("language", ""),
                "difficulty": pr.get("difficulty", ""),
                "rvs_score": pr.get("rvs_score"),
                "gold_intent": gold_intent,
                "comments": comments,
                "n_substantive_comments": pr.get("num_substantive_comments", len(comments)),
            })
    with open(out_path, "w") as f:
        json.dump(converted, f)
    print(f"Converted {len(converted)} PRs -> {out_path}")
    return converted


def main():
    prs_path, ann_path = download_swe_prbench()
    annotations = load_annotations(ann_path)
    out_path = Path("data/swe_prbench/converted.json")
    converted = convert(prs_path, annotations, out_path)

    # Quick distribution summary
    from collections import Counter
    primary = Counter(p["gold_intent"] for p in converted)
    print("\nGold intent distribution:")
    for k, v in primary.most_common():
        print(f"  {k:18s} {v:3d}  ({100*v/len(converted):.1f}%)")

    lang = Counter(p["language"] for p in converted)
    print("\nLanguage distribution:")
    for k, v in lang.most_common():
        print(f"  {k:18s} {v:3d}")

    diff = Counter(p["difficulty"] for p in converted)
    print("\nDifficulty distribution:")
    for k, v in diff.most_common():
        print(f"  {k:18s} {v:3d}")


if __name__ == "__main__":
    main()