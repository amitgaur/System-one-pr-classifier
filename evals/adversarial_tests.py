"""
Regression tests for bun:test and chore recall bugs surfaced by
adversarial review (Fix 1: docs/adversarial_review.md).

These are the cases the classifier used to misclassify. The tests are
designed to FAIL on the original code and PASS after the fix.

The "gold" mappings here are deliberately permissive:
  - bun:test PRs that are test-only or test-dominated -> test
  - chore PRs that touch devcontainer/Makefile/Dockerfile -> build_ci
  - chore PRs that touch only README.md -> docs (already works)

We DON'T try to force "chore" as an intent — the classifier correctly
distinguishes build_infra from docs. The eval maps chore->chore which
doesn't reflect how Bun uses the label.
"""
from __future__ import annotations

import sys
sys.path.insert(0, ".")
from src.classifier import classify_pr_ontology


# Format: (name, title, body, file_paths, diff, additions, deletions, branch,
#          expected_intent, expected_min_test_files_in_surfaces, notes)
CASES = [
    # ---- bun:test PRs that should be detected as test-related ----
    {
        "name": "bun:test feat(test) — test files dominate",
        "title": "feat(test): add `test.failing`",
        "file_paths": [
            "packages/bun-types/test.d.ts",
            "src/bun.js/test/jest.zig",
            "test/js/bun/test/test-failing.test.ts",
            "test/js/bun/test/fixtures/failing-test-fails.fixture.ts",
            "test/js/bun/test/fixtures/failing-test-passes.fixture.ts",
            "test/js/bun/test/fixtures/failing-test-timeout.fixture.ts",
            "test/js/bun/test/fixtures/failing-test-fails.fixture.ts",
        ],
        "expected": "test",
        "why": "All files are test infrastructure; surfaces should be test_code only",
    },
    {
        "name": "bun:test fix(test) — test files dominate",
        "title": "fix(test): toThrow() === toThrow('')",
        "file_paths": [
            "src/bun.js/test/expect.zig",
            "test/cli/test/expectations.test.ts",
        ],
        "expected": "test",
        "why": "Both files are test code; should classify as test",
    },
    {
        "name": "bun:test add more cases — test files dominate",
        "title": "test(bun:test): add more test cases for done callbacks",
        "file_paths": [
            "src/bun.js/test/jest.zig",
            "test/js/bun/test/done-async.test.ts",
            "test/js/bun/test/done-callback.test.ts",
            "test/js/bun/test/fixtures/done-cb/done-infinity.fixture.ts",
            "test/js/bun/test/fixtures/done-cb/done-should-fail.fixture.ts",
            "test/js/bun/test/fixtures/done-cb/done-then-reject.fixture.ts",
            "test/js/bun/test/fixtures/done-cb/done-timeout-sync.fixture.ts",
            "test/js/bun/test/fixtures/done-cb/test-error-done-callback-fixture.ts",
        ],
        "expected": "test",
        "why": "Title starts with 'test('; should classify as test",
    },
    # ---- chore PRs that should be detected as build/CI work ----
    {
        "name": "chore: devcontainer $PATH fix",
        "title": "Fix $PATH on dev container",
        "file_paths": [
            ".devcontainer/devcontainer.json",
            "Dockerfile.devcontainer",
        ],
        "expected": "build_ci",
        "why": "Devcontainer + Dockerfile = build/CI infra",
    },
    {
        "name": "chore: Makefile .PHONY fix",
        "title": "Fix missing .PHONY for vendor-without-check",
        "file_paths": ["Makefile"],
        "expected": "build_ci",
        "why": "Makefile changes are build/CI work",
    },
    {
        "name": "chore: Ninja check on Debian",
        "title": "Fix check for ninja on Debian/Ubuntu",
        "file_paths": ["Makefile"],
        "expected": "build_ci",
        "why": "Makefile changes are build/CI work",
    },
    {
        "name": "chore: devcontainer build docs",
        "title": "Update build docs and commands for dev containers",
        "file_paths": [
            ".devcontainer/README.md",
            ".devcontainer/scripts/getting-started.sh",
            "Makefile",
            "README.md",
        ],
        "expected": "build_ci",
        "why": "Mix of devcontainer scripts + Makefile = build/CI",
    },
]


def run_test(case):
    r = classify_pr_ontology(
        title=case["title"],
        body="",
        file_paths=case["file_paths"],
        diff="",
        additions=0,
        deletions=0,
    )
    actual = r.pr_intent
    expected = case["expected"]
    pass_ = actual == expected
    return pass_, actual, expected, r


def main():
    print("=" * 78)
    print(f"REGRESSION TESTS — bun:test + chore recall (Fix 1)")
    print("=" * 78)
    print()

    passed = 0
    failed = 0
    for case in CASES:
        ok, actual, expected, r = run_test(case)
        marker = "PASS" if ok else "FAIL"
        print(f"[{marker}] {case['name']}")
        print(f"        expected={expected}  actual={actual}")
        print(f"        surfaces={r.pr_surfaces}  tier={r.review_tier}")
        print(f"        why: {case['why']}")
        print()
        if ok:
            passed += 1
        else:
            failed += 1

    print("-" * 78)
    print(f"Results: {passed} passed, {failed} failed (out of {len(CASES)})")
    print("-" * 78)
    if failed > 0:
        print("FAIL — fixes need to be applied")
        sys.exit(1)
    else:
        print("PASS — bun:test and chore recall fixes are working")
        sys.exit(0)


if __name__ == "__main__":
    main()