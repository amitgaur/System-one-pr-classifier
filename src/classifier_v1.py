"""
Typesafe PR classifier + model router.

Pattern: schema-first with pydantic (Python's Zod-equivalent). Every signal the
classifier emits is a typed literal; consumers get exhaustive narrowing on the
PR kind, risk flags, must-check items, and recommended model route.

Design borrowed from misospace/pr-reviewer-action but written here as a plain
Python module so we can validate against the labeled dataset and call it from
this project.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# -- PR kind (one of these — exhaustive) ----------------------------------
PRKind = Literal[
    "docs_only",
    "renovate_digest_only",
    "dependency_upgrade",
    "ci_workflow",
    "feature",
    "bug_fix",
    "refactor",
    "config_or_secret",
    "auth_or_security",
    "schema_or_migration",
    "infra_or_k8s",
    "test_only",
    "content_or_marketing",
    "experimental_branch",
    "mixed_or_unclear",
]

# -- Risk flags (zero or more) ---------------------------------------------
RiskFlag = Literal[
    "touches_secrets",
    "touches_auth",
    "touches_db_or_migration",
    "touches_public_routes",
    "touches_file_serving",
    "touches_path_handling",
    "linked_priority_p0",
    "large_change",
    "first_time_contributor",
    "ci_changes",
]

# -- Routing decisions -----------------------------------------------------
ModelTier = Literal["cheap", "smart", "human"]
ModelName = str  # we don't pin a single provider; the consumer fills it in


class ClassifiedPR(BaseModel):
    """A fully-typed classifier output. Consumers can switch on `pr_kind`."""

    pr_kind: PRKind
    confidence: float = Field(ge=0.0, le=1.0)
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    risk_score: Literal["low", "medium", "high", "critical"]
    must_check: list[str] = Field(default_factory=list)
    recommended_model_tier: ModelTier
    recommended_model_hint: str  # human-readable explanation
    signals: dict[str, list[str]] = Field(default_factory=dict)  # debug


# -- Signal rules (file paths, branch names, diff content) -----------------
RENOVATE_BRANCH = re.compile(r"^(renovate|dependabot)/", re.I)
EXPERIMENTAL_BRANCH = re.compile(
    r"(worktree-|/wip|/wip-|/worktree-|/experimental|/spike|/throwaway)",
    re.I,
)
DOCS_FILE = re.compile(r"^(\.github/|docs/|README\.md|CHANGELOG|.*\.md$)", re.I)
TEST_FILE = re.compile(r"^tests?/|/test_|/tests?\.|\.(test|spec)\.|_test\.py$", re.I)
SECRET_FILE = re.compile(r"(\.env(\..+)?$|\.pem$|\.key$|secrets?\.(ya?ml|json)|credentials?\.json)", re.I)
AUTH_FILE = re.compile(
    r"(/auth/|/login/|/oauth/|/session/|/jwt/|/token/|/permissions?/|/rbac/|/middleware/auth)",
    re.I,
)
DB_OR_MIGRATION = re.compile(r"(/migrations?/|/schema\.|\.sql$|prisma/|drizzle/|/alembic/)", re.I)
PUBLIC_ROUTE = re.compile(
    r"(/routes?/|/controllers?/|/api/|/endpoints?/|\.router\.|router\.ts|app\.get|app\.post|@app\.route)",
    re.I,
)
FILE_SERVING = re.compile(r"(send_file|send_from_directory|static/|public/|/uploads?/)", re.I)
PATH_HANDLING = re.compile(r"(os\.path\.join|pathlib|Path\(|fs\.createReadStream)", re.I)
CI_FILE = re.compile(r"^(\.github/workflows/|\.circleci/|gitlab-ci|\.drone|azure-pipelines|\.github/actions/)", re.I)
K8S_FILE = re.compile(r"(kustomization\.ya?ml|values\.ya?ml|\.helm/|/charts/|deployment\.ya?ml|statefulset\.ya?ml|cronjob\.ya?ml)", re.I)
CONFIG_FILE = re.compile(r"(^config/|/config\.|tsconfig|pyproject\.toml|setup\.cfg|poetry\.lock|pnpm-lock|yarn\.lock|package-lock|composer\.json|cargo\.toml)")

# Lockfile-only upgrade (renovate digest bump) marker
LOCKFILE_ONLY = re.compile(r"^(\S*/)?(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|composer\.lock|Cargo\.lock|go\.sum|uv\.lock)$", re.I)

# Title heuristics (lightweight, complementary to file-path rules)
TITLE_BUG = re.compile(r"\b(fix|bug|broken|crash|error|exception|regression|null|undefined|typo)\b", re.I)
TITLE_FEATURE = re.compile(r"\b(feat|feature|add|support|introduce|enable|allow)\b", re.I)
TITLE_REFACTOR = re.compile(r"\b(refactor|cleanup|clean up|reorganize|rename|restructure|simplify)\b", re.I)
TITLE_DOCS = re.compile(r"\b(docs?|documentation|readme|typo|comment)\b", re.I)
TITLE_DEPENDENCY = re.compile(r"\b(deps?|dependency|dependabot|renovate|bump|upgrade)\b", re.I)
TITLE_CI = re.compile(r"\b(ci|workflow|github actions|gitlab ci|circleci|pipeline)\b", re.I)


def classify_pr(
    title: str,
    body: str,
    file_paths: list[str],
    diff: str = "",
    branch: str = "",
    additions: int = 0,
    deletions: int = 0,
) -> ClassifiedPR:
    """
    Run the typesafe classifier over a PR.

    Pure rule-based — no model calls, deterministic, safe to run on every push.
    Mirrors the design of misospace/pr-reviewer-action.
    """
    signals: dict[str, list[str]] = {
        "matched_files_docs": [], "matched_files_test": [],
        "matched_files_secret": [], "matched_files_auth": [],
        "matched_files_db": [], "matched_files_ci": [],
        "matched_files_k8s": [], "matched_files_route": [],
        "matched_files_file_serving": [], "matched_files_path": [],
        "matched_files_lock": [], "matched_files_config": [],
    }

    n_files = len(file_paths)
    title_clean = title.strip()
    text = f"{title_clean}\n{body[:2000]}".strip()

    # --- 1. Branch hints -------------------------------------------------
    if EXPERIMENTAL_BRANCH.search(branch):
        return ClassifiedPR(
            pr_kind="experimental_branch",
            confidence=0.95,
            risk_flags=[],
            risk_score="low",
            must_check=["verify branch name intent; experimental branches should not auto-merge"],
            recommended_model_tier="cheap",
            recommended_model_hint="experimental/worktree branch — don't burn a smart model on this",
            signals={"branch": [branch]},
        )

    # --- 2. Tally file-path signals ---------------------------------------
    for path in file_paths:
        if DOCS_FILE.match(path):
            signals["matched_files_docs"].append(path)
        if TEST_FILE.match(path):
            signals["matched_files_test"].append(path)
        if SECRET_FILE.search(path):
            signals["matched_files_secret"].append(path)
        if AUTH_FILE.search(path):
            signals["matched_files_auth"].append(path)
        if DB_OR_MIGRATION.search(path):
            signals["matched_files_db"].append(path)
        if CI_FILE.match(path):
            signals["matched_files_ci"].append(path)
        if K8S_FILE.search(path):
            signals["matched_files_k8s"].append(path)
        if PUBLIC_ROUTE.search(path) or PUBLIC_ROUTE.search(diff[:20000]):
            signals["matched_files_route"].append(path)
        if FILE_SERVING.search(diff[:20000]):
            signals["matched_files_file_serving"].append(path)
        if PATH_HANDLING.search(diff[:20000]):
            signals["matched_files_path"].append(path)
        if LOCKFILE_ONLY.match(path):
            signals["matched_files_lock"].append(path)
        if CONFIG_FILE.search(path):
            signals["matched_files_config"].append(path)

    # --- 3. Renovation digest-only (lockfile only) -------------------------
    if n_files and all(LOCKFILE_ONLY.match(p) for p in file_paths):
        return ClassifiedPR(
            pr_kind="renovate_digest_only",
            confidence=0.99,
            risk_flags=[],
            risk_score="low",
            must_check=["verify no functional changes beyond lockfile hashes"],
            recommended_model_tier="cheap",
            recommended_model_hint="lockfile-only bump; route to a cheap model for a 2-second diff sanity check",
            signals=signals,
        )

    # --- 4. PR kind: start with most-specific signals, fall back to title --
    risk_flags: list[RiskFlag] = []
    must_check: list[str] = []

    # Auth/security wins over feature/refactor (highest priority for routing)
    if signals["matched_files_auth"]:
        kind: PRKind = "auth_or_security"
        confidence = 0.85
        risk_flags.append("touches_auth")
        must_check.append("review auth flow for regression")
        must_check.append("session token handling unchanged")
    elif signals["matched_files_db"]:
        kind = "schema_or_migration"
        confidence = 0.85
        risk_flags.append("touches_db_or_migration")
        must_check.append("migration data-loss risk")
        must_check.append("test on a copy of production schema")
    elif signals["matched_files_k8s"]:
        kind = "infra_or_k8s"
        confidence = 0.85
        must_check.append("validate manifest against target cluster version")
        must_check.append("resource quota / limit changes")
    elif signals["matched_files_ci"]:
        # Only call it CI-workflow if NO app code touched. CI + app = app change.
        n_ci = len(signals["matched_files_ci"])
        n_app = n_files - n_ci
        if n_app > 0:
            # Mixed: app code + CI. Don't classify as ci_workflow.
            kind = None
            confidence = 0.5
        else:
            kind = "ci_workflow"
            confidence = 0.9
            risk_flags.append("ci_changes")
            must_check.append("workflow permissions; secret references valid")
    elif signals["matched_files_secret"]:
        kind = "config_or_secret"
        confidence = 0.85
        risk_flags.append("touches_secrets")
        must_check.append("secrets not logged/exposed; secret rotation impact")
    elif signals["matched_files_docs"] and signals["matched_files_test"]:
        # Both docs and tests changed -> probably a doc+test-only maintenance PR.
        # Check whether app code also changed. If only docs+tests, classify as docs.
        only_doc_or_test = all(
            DOCS_FILE.match(p) or TEST_FILE.match(p) or p.lower().endswith(".md")
            for p in file_paths
        )
        if only_doc_or_test:
            # Both docs and tests, no app code: test_only if there's a balance,
            # otherwise docs_only. Bias toward test_only if tests dominate.
            n_docs = len(signals["matched_files_docs"])
            n_test = len(signals["matched_files_test"])
            if n_test > n_docs:
                kind = "test_only"
                confidence = 0.75
            else:
                kind = "docs_only"
                confidence = 0.75
        else:
            kind = None  # mixed; fall through to title heuristics
            confidence = 0.5
    elif signals["matched_files_docs"] and not (signals["matched_files_test"] or signals["matched_files_route"]):
        # Pure docs: only docs files, no test or route changes
        if all(DOCS_FILE.match(p) or p.lower().endswith(".md") for p in file_paths):
            kind = "docs_only"
            confidence = 0.92
        else:
            kind = "docs_only"
            confidence = 0.7
    elif signals["matched_files_test"] and not signals["matched_files_docs"]:
        # If ONLY test files (no app code), classify test_only.
        # If tests + app code, let title heuristics decide (bug_fix or feature).
        non_test_paths = [p for p in file_paths if not TEST_FILE.match(p)]
        if not non_test_paths:
            kind = "test_only"
            confidence = 0.85
        else:
            kind = None  # fall through to title heuristics
            confidence = 0.5
    elif TITLE_DEPENDENCY.search(text) or (signals["matched_files_lock"] and len(signals["matched_files_lock"]) == n_files):
        kind = "dependency_upgrade"
        confidence = 0.8
        must_check.append("breaking API changes in updated dependencies")
        must_check.append("run full test suite after upgrade")
    else:
        kind = None
        confidence = 0.3

    # If file-path signals didn't give us a confident kind, fall back to title heuristics.
    if kind is None:
        if TITLE_BUG.search(text):
            kind = "bug_fix"
            confidence = 0.7
        elif TITLE_FEATURE.search(text):
            kind = "feature"
            confidence = 0.7
        elif TITLE_REFACTOR.search(text):
            kind = "refactor"
            confidence = 0.7
        elif TITLE_DOCS.search(text):
            kind = "docs_only"
            confidence = 0.7
        elif TITLE_CI.search(text):
            kind = "ci_workflow"
            confidence = 0.7
        else:
            kind = "mixed_or_unclear"
            confidence = 0.3

    # --- 5. Cross-cutting risk detection ------------------------------------
    if signals["matched_files_route"]:
        risk_flags.append("touches_public_routes")
        must_check.append("route access controls; unintended public endpoints")
    if signals["matched_files_file_serving"]:
        risk_flags.append("touches_file_serving")
        must_check.append("file path sanitization; directory traversal")
    if signals["matched_files_path"]:
        risk_flags.append("touches_path_handling")
        must_check.append("path traversal; edge-case paths (null bytes, symlinks)")

    total_changes = additions + deletions
    if total_changes > 500 or n_files > 15:
        risk_flags.append("large_change")
        must_check.append("reviewers should pay attention — large surface area")

    # --- 6. Risk score & model route ---------------------------------------
    high_risk = {"touches_secrets", "touches_auth", "touches_db_or_migration",
                 "touches_public_routes", "touches_file_serving", "touches_path_handling"}
    medium_risk = {"ci_changes", "linked_priority_p0", "large_change"}

    high = sum(1 for f in risk_flags if f in high_risk)
    med = sum(1 for f in risk_flags if f in medium_risk)
    if high >= 1:
        risk_score: Literal["low", "medium", "high", "critical"] = "critical" if high >= 2 else "high"
    elif med >= 2:
        risk_score = "medium"
    else:
        risk_score = "low"

    # --- 7. Model routing decision ----------------------------------------
    # Aligned with misospace/pr-reviewer-action's `review_routing_mode: auto`:
    # - "cheap"  -> local small model (Ollama qwen3, llama.cpp, etc.)
    # - "smart"  -> frontier model (Claude Sonnet/Opus, GPT-5, Gemini 2.5 Pro)
    # - "human"  -> route to a human reviewer (no model call)
    if kind in {"docs_only", "renovate_digest_only"}:
        tier: ModelTier = "cheap"
        hint = "docs/lockfile-only — local small model is sufficient"
    elif risk_score in {"high", "critical"}:
        tier = "smart"
        hint = (f"high-risk {kind} — escalate to a smart model "
                "(Claude Sonnet/Opus or GPT-5); never let a cheap model approve")
    elif kind in {"experimental_branch", "mixed_or_unclear"}:
        tier = "cheap"
        hint = "experimental / unclear scope — cheap model for triage, then a human decides"
    elif risk_score == "medium":
        tier = "smart"
        hint = f"medium-risk {kind} — smart model recommended for thorough review"
    else:  # low risk + clear kind
        if kind in {"bug_fix", "feature", "refactor"} and total_changes < 80:
            tier = "cheap"
            hint = f"small {kind} — cheap model can handle this"
        else:
            tier = "smart"
            hint = f"{kind} with {total_changes} lines — smart model for nuanced review"

    # Promote: a huge change always escalates, regardless of kind
    if total_changes > 1500 and tier == "cheap":
        tier = "smart"
        hint += " (escalated: >1500 line change)"

    return ClassifiedPR(
        pr_kind=kind,
        confidence=confidence,
        risk_flags=sorted(set(risk_flags)),
        risk_score=risk_score,
        must_check=sorted(set(must_check)),
        recommended_model_tier=tier,
        recommended_model_hint=hint,
        signals=signals,
    )


if __name__ == "__main__":
    # Self-test
    print("typesafe classifier ready")