"""
Regression tests — diff-trumps-title invariant (Fix 2).

The invariant this file enforces:

  If the file paths (and optionally diff content) give a clear signal
  about the PR's nature, the classifier MUST trust that signal even
  when the PR title says something different.

Why: titles are written after the fact, are often terse, and frequently
use prefixes like "feat", "fix", "chore" that are *typed by muscle
memory*, not by actual content. The diff is ground truth.

These tests are designed to FAIL on the original code (where title
heuristics could win over an unambiguous surface signal) and PASS after
the fix.

Each test case has:
  - title: the noisy title
  - file_paths: the ground truth
  - diff: the actual changed content (optional)
  - expected_intent: what the diff says the PR is
  - expected_surfaces: what surfaces the diff shows
"""
from __future__ import annotations

import sys
sys.path.insert(0, ".")
from src.classifier import classify_pr_ontology


CASES = [
    # ============================================================
    # Invariant 1: ALL files are test files -> intent must be `test`
    # even if title says `feat` or `fix` or has no prefix.
    # ============================================================
    {
        "name": "All files test, title says 'feat(test)'",
        "title": "feat(test): add new test runner case",
        "file_paths": [
            "test/js/bun/test/test-failing.test.ts",
            "test/js/bun/test/fixtures/case-1.fixture.ts",
            "test/js/bun/test/fixtures/case-2.fixture.ts",
        ],
        "expected_intent": "test",
        "expected_surfaces": ["test_code"],
        "invariant": "All files are tests; title prefix ignored",
    },
    {
        "name": "All files test, title says 'fix(test)'",
        "title": "fix(test): broken assertion in toThrow",
        "file_paths": [
            "test/js/bun/test/expectations.test.ts",
            "src/bun.js/test/expect.zig",
        ],
        "expected_intent": "test",
        "expected_surfaces": ["test_code"],
        "invariant": "All files are tests; title 'fix' prefix ignored",
    },
    {
        "name": "All files test, title has no prefix",
        "title": "Update test fixtures for done callbacks",
        "file_paths": [
            "test/js/bun/test/done-callback.test.ts",
            "test/js/bun/test/fixtures/done-cb/case-1.fixture.ts",
            "test/js/bun/test/fixtures/done-cb/case-2.fixture.ts",
        ],
        "expected_intent": "test",
        "expected_surfaces": ["test_code"],
        "invariant": "No prefix; surface is unambiguous",
    },

    # ============================================================
    # Invariant 2: ALL files are build/CI infra -> intent must be
    # `build_ci` even if title says `fix`.
    # ============================================================
    {
        "name": "All files Makefile + Dockerfile, title 'Fix $PATH'",
        "title": "Fix $PATH on dev container",
        "file_paths": [
            ".devcontainer/devcontainer.json",
            "Dockerfile.devcontainer",
        ],
        "expected_intent": "build_ci",
        "expected_surfaces": ["build_ci"],
        "invariant": "Devcontainer + Dockerfile = build/CI; title 'Fix' prefix ignored",
    },
    {
        "name": "Only Makefile change, title 'Fix .PHONY'",
        "title": "Fix missing .PHONY for vendor-without-check",
        "file_paths": ["Makefile"],
        "expected_intent": "build_ci",
        "expected_surfaces": ["build_ci"],
        "invariant": "Makefile-only change; no app code surface",
    },
    {
        "name": "Only CI workflow change, title says 'feat'",
        "title": "feat: add release-please workflow",
        "file_paths": [".github/workflows/release-please.yml"],
        "expected_intent": "build_ci",
        "expected_surfaces": ["build_ci"],
        "invariant": "Only .github/workflows/* files; build-ci wins",
    },

    # ============================================================
    # Invariant 3: Mixed test + app code with `test:` prefix in
    # conventional-commit style -> still classified by surface mix.
    # If test dominates, intent is `test`.
    # ============================================================
    {
        "name": "Test code dominates, title 'feat(test)'",
        "title": "feat(test): add more test cases for done callbacks",
        "file_paths": [
            "src/bun.js/test/jest.zig",
            "test/js/bun/test/done-async.test.ts",
            "test/js/bun/test/done-callback.test.ts",
            "test/js/bun/test/fixtures/case-1.fixture.ts",
            "test/js/bun/test/fixtures/case-2.fixture.ts",
            "test/js/bun/test/fixtures/case-3.fixture.ts",
            "test/js/bun/test/fixtures/case-4.fixture.ts",
            "test/js/bun/test/fixtures/case-5.fixture.ts",
        ],
        "expected_intent": "test",
        "expected_surfaces_must_include": ["test_code"],
        "invariant": "Test code dominates (7/8 files); intent is test",
    },
]


def run_case(case):
    r = classify_pr_ontology(
        title=case["title"],
        body="",
        file_paths=case["file_paths"],
        diff="",
        additions=0,
        deletions=0,
    )
    errors = []

    if "expected_intent" in case and r.pr_intent != case["expected_intent"]:
        errors.append(
            f"intent: expected={case['expected_intent']} got={r.pr_intent}"
        )

    if "expected_surfaces" in case:
        if sorted(r.pr_surfaces) != sorted(case["expected_surfaces"]):
            errors.append(
                f"surfaces: expected={case['expected_surfaces']} "
                f"got={r.pr_surfaces}"
            )

    if "expected_surfaces_must_include" in case:
        for s in case["expected_surfaces_must_include"]:
            if s not in r.pr_surfaces:
                errors.append(
                    f"surfaces: expected to include {s} got={r.pr_surfaces}"
                )

    return errors, r


def main():
    print("=" * 78)
    print("REGRESSION — diff-trumps-title invariant (Fix 2)")
    print("=" * 78)
    print()

    passed = 0
    failed = 0
    for case in CASES:
        errors, r = run_case(case)
        marker = "PASS" if not errors else "FAIL"
        print(f"[{marker}] {case['name']}")
        print(f"        invariant: {case['invariant']}")
        if errors:
            for e in errors:
                print(f"          -> {e}")
            failed += 1
        else:
            passed += 1
        print(f"        result: intent={r.pr_intent}  "
              f"surfaces={r.pr_surfaces}  tier={r.review_tier}")
        print()

    print("-" * 78)
    print(f"Results: {passed}/{len(CASES)} passed, {failed} failed")
    print("-" * 78)
    if failed > 0:
        print("\nFix needed: surface detection must dominate title heuristics.")
        sys.exit(1)
    else:
        print("\nAll diff-trumps-title invariants hold.")
        sys.exit(0)


if __name__ == "__main__":
    main()