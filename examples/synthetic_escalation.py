"""
Synthetic escalation demo — proves the ontology classifier correctly
escalates risk-touching PRs to System-2 reasoning models, and demotes
trivial changes to System-1 / skip.
"""
from __future__ import annotations

import sys
sys.path.insert(0, ".")
from src.classifier import classify_pr_ontology


CASES = [
    {
        "name": "Authentication bypass fix",
        "title": "fix: authentication bypass via path traversal",
        "body": "",
        "file_paths": ["src/auth/login.py", "src/middleware/auth.ts"],
        "diff": "",
        "additions": 50, "deletions": 10,
    },
    {
        "name": "Bun X509Certificate PR (real #15585)",
        "title": "[WIP] feat(node/crypto): add `X509Certificate`",
        "body": "",
        "file_paths": ["src/node/crypto/x509.zig", "src/bun.js/WebCrypto.zig"],
        "diff": "",
        "additions": 2000, "deletions": 50,
    },
    {
        "name": "DB migration",
        "title": "feat(db): add users.email_unique",
        "body": "",
        "file_paths": ["prisma/schema.prisma", "prisma/migrations/0001_email.sql"],
        "diff": "",
        "additions": 150, "deletions": 10,
    },
    {
        "name": "Performance work",
        "title": "perf: speed up JSON parse 30%",
        "body": "",
        "file_paths": ["src/parse.js"],
        "diff": "",
        "additions": 80, "deletions": 30,
    },
    {
        "name": "Lockfile digest bump",
        "title": "chore(deps): update lockfile",
        "body": "",
        "file_paths": ["package-lock.json"],
        "diff": "",
        "additions": 5, "deletions": 5,
    },
    {
        "name": "Experimental branch",
        "title": "WIP: stuff",
        "body": "",
        "file_paths": ["src/foo.ts"],
        "diff": "",
        "additions": 100, "deletions": 0,
        "branch": "worktree-foo-bar-baz",
    },
    {
        "name": "Typo fix",
        "title": "fix: typo in error message",
        "body": "",
        "file_paths": ["src/error.rs"],
        "diff": "",
        "additions": 2, "deletions": 2,
    },
    {
        "name": "Docs-only",
        "title": "docs: clarify site delivery",
        "body": "",
        "file_paths": ["README.md", "coaching/site/README.md"],
        "diff": "",
        "additions": 50, "deletions": 5,
    },
]


def main():
    print("=" * 78)
    print("ONTOLOGY CLASSIFIER — Synthetic escalation demo")
    print("=" * 78)

    for c in CASES:
        r = classify_pr_ontology(
            title=c["title"],
            body=c.get("body", ""),
            file_paths=c["file_paths"],
            diff=c.get("diff", ""),
            branch=c.get("branch", ""),
            additions=c.get("additions", 0),
            deletions=c.get("deletions", 0),
        )
        print(f"\n>>> {c['name']}")
        print(f"    title: {c['title']}")
        print(f"    intent={r.pr_intent:14s} tier={r.review_tier:22s} sev={r.pr_severity:8s}")
        print(f"    surfaces={r.pr_surfaces}")
        print(f"    risks={r.pr_risks}")
        print(f"    -> {r.recommended_model_family}")


if __name__ == "__main__":
    main()