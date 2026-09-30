"""
End-to-end demo: ontology-based classifier on Amit's 4 real open PRs.

This is the canonical "what does this thing actually emit" demo.
Each PR is classified with the v2 (ontology) classifier:
  - 5 typed enums (intent, surfaces, risks, severity, domain, tier)
  - system-1 vs system-2 model family recommendation
  - derived must_check checklist

Run:  python examples/ontology_on_real_prs.py
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")
from src.classifier import classify_pr_ontology


def render(r):
    print(f"  intent          : {r.pr_intent}")
    print(f"  surfaces        : {r.pr_surfaces}")
    print(f"  risks           : {r.pr_risks}")
    print(f"  severity        : {r.pr_severity}")
    print(f"  complexity_score: {r.complexity_score:.2f}  "
          f"(0=trivial, 1=frontier-reasoning-required)")
    print(f"  review_domain   : {r.review_domain}")
    print(f"  review_tier     : {r.review_tier}  [confidence={r.confidence:.2f}]")
    if r.must_check:
        print("  must_check      :")
        for m in r.must_check:
            print(f"    - {m}")
    print(f"  recommendation  : {r.recommended_model_family}")


def main():
    with open("data/raw_prs.json") as f:
        prs = json.load(f)

    print("=" * 78)
    print("ONTOLOGY CLASSIFIER — End-to-end on Amit's 4 real open PRs")
    print("=" * 78)

    for p in prs:
        print(f"\n>>> {p['_repo']}#{p['number']}: {p['title']}")
        print(f"    branch={p.get('headRefName')}  +{p.get('additions')}/-{p.get('deletions')}  files={len(p.get('all_file_paths', []))}")
        r = classify_pr_ontology(
            title=p.get("title", ""),
            body=p.get("body", "") or "",
            file_paths=p.get("all_file_paths", []),
            diff=p.get("diff", ""),
            branch=p.get("headRefName", ""),
            additions=p.get("additions", 0),
            deletions=p.get("deletions", 0),
        )
        render(r)

    print("\n" + "=" * 78)
    print("ROUTING SUMMARY")
    print("=" * 78)
    print(f"{'PR':<35} {'tier':<25} {'severity':<10}")
    print("-" * 72)
    for p in prs:
        r = classify_pr_ontology(
            title=p.get("title", ""),
            body=p.get("body", "") or "",
            file_paths=p.get("all_file_paths", []),
            diff=p.get("diff", ""),
            branch=p.get("headRefName", ""),
            additions=p.get("additions", 0),
            deletions=p.get("deletions", 0),
        )
        print(f"{p['_repo'] + '#' + str(p['number']):<35} {r.review_tier:<25} {r.pr_severity:<10}")


if __name__ == "__main__":
    main()