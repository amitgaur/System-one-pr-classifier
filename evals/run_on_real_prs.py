"""
Run the typesafe classifier over Amit's 4 real PRs and report kind + risk +
recommended model route. Each PR includes file_paths + diff so the full
classifier signal set is exercised.

Uses the v1 (15-kind) classifier for backward-compatibility with the original
4-PR routing results. For the ontology-based v2, see
`evals/validate_v2_on_bun.py` and `examples/ontology_on_real_prs.py`.
"""
from __future__ import annotations

import json

from src.classifier_v1 import classify_pr


with open("data/raw_prs.json") as f:
        prs = json.load(f)


def hr(label: str) -> str:
    return f"\n{label}\n{'-' * len(label)}"


print("=" * 70)
print("TYPESAFE PR CLASSIFIER + MODEL ROUTER — REAL PR EVALUATION")
print("=" * 70)

routing_summary: list[dict] = []
for r in prs:
    repo = r["_repo"]
    num = r["number"]
    title = r.get("title", "")
    body = r.get("body", "")
    branch = r.get("headRefName", "")
    additions = r.get("additions", 0)
    deletions = r.get("deletions", 0)
    file_paths = r.get("all_file_paths", [])
    diff = r.get("diff", "")

    result = classify_pr(
        title=title,
        body=body,
        file_paths=file_paths,
        diff=diff,
        branch=branch,
        additions=additions,
        deletions=deletions,
    )

    print(hr(f"{repo}#{num}"))
    print(f"Title:        {title}")
    print(f"Branch:       {branch}")
    print(f"Files ({len(file_paths)}):")
    for p in file_paths[:6]:
        print(f"  - {p}")
    if len(file_paths) > 6:
        print(f"  ... +{len(file_paths)-6} more")
    print(f"Diff size:    +{additions}/-{deletions}")
    print()
    print(f"PR KIND:        {result.pr_kind}")
    print(f"CONFIDENCE:     {result.confidence:.2f}")
    print(f"RISK SCORE:     {result.risk_score}")
    print(f"RISK FLAGS:     {result.risk_flags or '—'}")
    print(f"MUST CHECK:")
    for m in result.must_check:
        print(f"  • {m}")
    if not result.must_check:
        print("  • (none)")
    print(f"RECOMMENDED MODEL TIER: {result.recommended_model_tier}")
    print(f"  hint: {result.recommended_model_hint}")
    print(f"SIGNALS:        {sum(len(v) for v in result.signals.values())} matched")
    for sig, hits in result.signals.items():
        if hits:
            print(f"  • {sig}: {len(hits)} hits")

    routing_summary.append({
        "pr": f"{repo}#{num}",
        "title": title,
        "pr_kind": result.pr_kind,
        "risk_score": result.risk_score,
        "tier": result.recommended_model_tier,
        "flags": result.risk_flags,
    })


print()
print("=" * 70)
print("ROUTING SUMMARY")
print("=" * 70)
print(f"{'PR':<35} {'KIND':<22} {'RISK':<10} {'TIER':<8}")
print("-" * 75)
for s in routing_summary:
    print(f"{s['pr']:<35} {s['pr_kind']:<22} {s['risk_score']:<10} {s['tier']:<8}")

cheap_count = sum(1 for s in routing_summary if s["tier"] == "cheap")
smart_count = sum(1 for s in routing_summary if s["tier"] == "smart")
print(f"\nRouting: {cheap_count} cheap, {smart_count} smart")

with open("routing_decisions.json", "w") as f:
    json.dump(routing_summary, f, indent=2)