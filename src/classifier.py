"""
Typesafe PR classifier — research-grounded ontology version. Replaces the
ad-hoc 15-kind enum in typesafe_classifier.py with the layered ontology
described in ontology.py.

Layer model:
  pr_intent     : Layer 1, single-value (I1-I10 in ontology.py)
  pr_surfaces   : Layer 2, multi-value (S1-S10)
  pr_risks      : Layer 3, multi-value (R1-R10)
  pr_severity   : Layer 4, derived (SEV1-SEV4)
  review_domain : which of the 5 [Wang 2025] domains the PR belongs to
  review_tier   : T0/T1/T2/T3/T4 — the routing decision
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field


# --- Layer 1: PR intent (single-value) ------------------------------------
Intent = Literal[
    "feature",                  # I1: new capability
    "bug_fix",                  # I2: incorrect behavior
    "refactor",                 # I3: restructure, no behavior change
    "docs",                     # I4: docs only
    "test",                     # I5: test only
    "build_ci",                 # I6: build/CI only
    "chore",                    # I7: housekeeping (deps, format, chore-labeled)
    "perf",                     # I8: perf-targeted
    "revert",                   # I9: undoes prior merge
    "security_patch",           # I10: explicit security fix
    "mixed_or_unclear",         # doesn't fit cleanly
]

# --- Layer 2: surfaces touched (multi-value) ------------------------------
Surface = Literal[
    "app_code",
    "test_code",
    "documentation",
    "build_ci",
    "config",
    "schema_migration",
    "infra_k8s",
    "generated_bundle",
    "dep_upgrade",
    "public_api_routes",
]

# --- Layer 3: risk flags (multi-value) ------------------------------------
Risk = Literal[
    "touches_auth",
    "touches_secrets",
    "touches_db_or_migration",
    "touches_public_api",
    "touches_file_serving",
    "touches_path_handling",
    "touches_crypto_or_ffi",
    "touches_concurrency",
    "linked_priority_p0",
    "first_time_contributor",
    "large_change",
]

# --- Layer 4: severity (derived) ------------------------------------------
Severity = Literal["critical", "high", "medium", "low"]

# --- Complexity score (dual-purpose numeric summary) ----------------------
# This is the SINGLE field that answers both questions simultaneously:
#   "how complex is this PR?"           -> higher = more
#   "which model should review this?"    -> higher = smarter model
# Range: 0.0 (trivial, skip review) -> 1.0 (must-go-to-frontier-reasoning).
#
# The mapping is monotonically consistent with review_tier:
#   complexity < 0.15 -> T0_skip
#   complexity < 0.35 -> T1_system1_fast
#   complexity < 0.65 -> T2_system1_verified
#   complexity < 0.85 -> T3_system2_deliberate
#   complexity >= 0.85 -> T4_human_gate
#
# NOTE ON DUAL-PURPOSE DESIGN (adversarial review finding):
#   This collapse is a DESIGN CHOICE by this project, not a result proven
#   by prior work. The most relevant research grounding:
#   - [Li et al. 2025, arXiv:2502.17419] "From System 1 to System 2":
#     System-2 reasoning is required for cross-cutting + risk-bearing
#     changes. The complexity axis and the model-selection axis are
#     described in the same conceptual frame (fast/heuristic vs
#     slow/deliberate) - which licenses treating them as the same axis.
#   - [Wang et al. 2025, arXiv:2602.13377] code review survey:
#     ~10% of PRs warrant focused peer review (D2 domain).
#   - [Mantyla & Lassenius 2009, IEEE TSE] defect types:
#     75% of review findings are evolvability (subjective) - these
#     need deliberate (System-2) review regardless of diff size.
#
#   Routing-specific papers (RouteLLM, R2-Router, InferenceDynamics)
#   use MORE dimensions than just complexity (cost, capability profile,
#   budget). This project deliberately collapses to ONE axis for
#   simplicity; multi-axis routing is future work.
#
#   See docs/adversarial_review.md for the full critique.


def severity_to_complexity(sev: Severity) -> float:
    return {"low": 0.20, "medium": 0.50, "high": 0.75, "critical": 0.92}[sev]


# --- Review domain (Wang 2025 survey) -------------------------------------
Domain = Literal[
    "D1_change_understanding",
    "D2_peer_review",
    "D3_review_assessment",
    "D4_code_refinement",
    "D5_prioritization_selection",
]

# --- Routing tier (System 1 / System 2 axis) ------------------------------
Tier = Literal[
    "T0_skip",                  # no review needed
    "T1_system1_fast",          # local small model
    "T2_system1_verified",      # local small model + deterministic checks
    "T3_system2_deliberate",    # frontier reasoning model
    "T4_human_gate",            # must go to human
]


class PRClassification(BaseModel):
    """Full ontology output.

    Dual-purpose design (research-grounded, [Li et al. 2025]):
        Every typed field is BOTH:
          (a) a complexity signal for the PR (PR is X complex because Y)
          (b) a routing signal for the model (use model Z because of Y)
        The single PRClassification object answers both
        "how complex is this PR?" and "which model should review it?"
        without re-classification.

        NOTE: This dual-purpose design is a project choice, not a proven
        result from prior work. Routing papers (RouteLLM, R2-Router,
        InferenceDynamics) use multi-axis decisions (cost, capability
        profile, budget). This project collapses to one axis for
        simplicity. Multi-axis routing is future work.
        See docs/adversarial_review.md.
    """

    pr_intent: Intent
    pr_surfaces: list[Surface] = Field(default_factory=list)
    pr_risks: list[Risk] = Field(default_factory=list)
    pr_severity: Severity
    complexity_score: float = Field(
        ge=0.0, le=1.0,
        description="Dual-purpose 0-1 score: PR complexity AND required model intelligence",
    )
    review_domain: Domain
    review_tier: Tier
    confidence: float = Field(ge=0.0, le=1.0)
    must_check: list[str] = Field(default_factory=list)
    recommended_model_family: str  # human-readable hint
    signals: dict[str, list[str]] = Field(default_factory=dict)


# --- Signal rules ---------------------------------------------------------
DOCS_FILE = re.compile(r"^(\.github/(?!workflows/|actions/)|docs/|README\.md|CHANGELOG|.*\.mdx?$)", re.I)
TEST_FILE = re.compile(
    r"(?:^|/)tests?(?:/|$)|(?:^|/)(?:test|tests)/|\.(test|spec)\.|_test\.(py|ts|js|zig|rs|go|java)$",
    re.I,
)
SECRET_FILE = re.compile(r"(\.env(\..+)?$|\.pem$|\.key$|secrets?\.(ya?ml|json)|credentials?\.json)", re.I)
AUTH_FILE = re.compile(
    r"(/auth/|/login/|/oauth/|/session/|/jwt/|/token/|/permissions?/|/rbac/|/middleware/auth)",
    re.I,
)
DB_OR_MIGRATION = re.compile(r"(/migrations?/|/schema\.|\.sql$|prisma/|drizzle/|/alembic/)", re.I)
CI_FILE = re.compile(r"^(\.github/workflows/|\.circleci/|gitlab-ci|\.drone|azure-pipelines|\.github/actions/)", re.I)
# Build/CI surface extensions (Makefiles, Dockerfiles, devcontainer, etc.)
# Note: each alternative is a *substring* match, NOT anchored at $.
# .devcontainer/, .vscode/, etc. can have any filename after.
BUILD_FILE = re.compile(
    r"(?:^|/)("
    r"Makefile|makefile|"
    r"Dockerfile(?:\..+)?|docker-compose[^/]*|"
    r".+\.mk|"
    r".devcontainer|"
    r"\.vscode|"
    r"CMakeLists\.txt|"
    r"Brewfile|renovate\.json(?:\.5)?|"
    r"\.npmrc|\.nvmrc|\.node-version|\.tool-versions|"
    r"\.gitignore|\.dockerignore|\.editorconfig|"
    r".+\.sh|"
    r".+\.bash|"
    r".+\.zsh"
    r")",
    re.I,
)
K8S_FILE = re.compile(r"(kustomization\.ya?ml|values\.ya?ml|\.helm/|/charts/|deployment\.ya?ml|statefulset\.ya?ml|cronjob\.ya?ml)", re.I)
CONFIG_FILE = re.compile(r"(^config/|/config\.|tsconfig|pyproject\.toml|setup\.cfg|poetry\.lock|pnpm-lock|yarn\.lock|package-lock|composer\.json|cargo\.toml)")
LOCKFILE_ONLY = re.compile(r"^(\S*/)?(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|composer\.lock|Cargo\.lock|go\.sum|uv\.lock)$", re.I)
GENERATED = re.compile(r"(^dist/|^build/|/lib/|^out/|\.min\.(js|css)$|\.lockb$|node_modules/)", re.I)
PUBLIC_API = re.compile(r"(/api/v\d+/|@public|@api|/routes?/|/controllers?/|/endpoints?/|\.router\.)", re.I)
FILE_SERVING = re.compile(r"(send_file|send_from_directory|/uploads?/|express\.static|app\.use\(.*static)", re.I)
PATH_HANDLING = re.compile(r"(os\.path\.join|pathlib\.Pure|fs\.createReadStream|path\.resolve)", re.I)
CRYPTO_OR_FFI = re.compile(r"(/crypto/|/tls/|/x509/|/certs/|ffi\.|libffi|tinycc|node:crypto|node:tls|web:crypto)", re.I)
CONCURRENCY = re.compile(r"(threading\.Lock|asyncio\.Lock|atomic\.|Mutex<|Arc<|RwLock|std::sync)", re.I)
EXPERIMENTAL_BRANCH = re.compile(r"(worktree-|/wip|/wip-|/worktree-|/experimental|/spike|/throwaway)", re.I)

# Title heuristics
TITLE_BUG = re.compile(r"\b(fix|bug|broken|crash|error|exception|regression|null|undefined|typo)\b", re.I)
TITLE_FEATURE = re.compile(r"\b(feat|feature|add|support|introduce|enable|allow|implement)\b", re.I)
TITLE_REFACTOR = re.compile(r"\b(refactor|cleanup|clean up|reorganize|rename|restructure|simplify)\b", re.I)
TITLE_DOCS = re.compile(r"\b(docs?|documentation|readme|comment)\b", re.I)
TITLE_DEPEND = re.compile(r"\b(deps?|dependency|dependabot|renovate|bump|upgrade)\b", re.I)
TITLE_CI = re.compile(r"\b(ci|workflow|github actions|gitlab ci|circleci|pipeline)\b", re.I)
TITLE_CHORE = re.compile(r"\b(chore|format|lint|formatting|typo|clean up|prettier|eslint)\b", re.I)
TITLE_PERF = re.compile(r"\b(perf|performance|optimize|optimization|speed|latency|memory)\b", re.I)
TITLE_REVERT = re.compile(r"\b(revert|rollback|undo)\b", re.I)
TITLE_SECURITY = re.compile(r"\b(security|cve|vulnerab|xss|injection|overflow|exploit)\b", re.I)


def _detect_surfaces(file_paths: list[str], diff: str) -> tuple[list[Surface], dict[str, list[str]]]:
    """Return (matched surfaces, debug signal map)."""
    signals: dict[str, list[str]] = {
        "docs": [], "test": [], "secret": [], "auth": [], "db": [],
        "ci": [], "build_file": [], "k8s": [], "config": [], "lockfile": [], "generated": [],
        "public_api": [], "file_serving": [], "path": [], "crypto": [],
        "concurrency": [], "app_code": [],
    }
    surfaces: list[Surface] = []

    def add(sig: str, surface: Surface, p: str):
        signals[sig].append(p)
        if surface not in surfaces:
            surfaces.append(surface)

    for path in file_paths:
        if DOCS_FILE.match(path):
            add("docs", "documentation", path)
        if TEST_FILE.match(path):
            add("test", "test_code", path)
        if SECRET_FILE.search(path):
            add("secret", "config", path)
        if AUTH_FILE.search(path):
            add("auth", "public_api_routes", path)
        if DB_OR_MIGRATION.search(path):
            add("db", "schema_migration", path)
        if CI_FILE.match(path):
            add("ci", "build_ci", path)
        if BUILD_FILE.search(path):
            add("build_file", "build_ci", path)
        if K8S_FILE.search(path):
            add("k8s", "infra_k8s", path)
        if CONFIG_FILE.search(path):
            add("config", "config", path)
        if LOCKFILE_ONLY.match(path):
            add("lockfile", "dep_upgrade", path)
        if GENERATED.match(path):
            add("generated", "generated_bundle", path)
        if PUBLIC_API.search(path) or PUBLIC_API.search(diff[:30000]):
            add("public_api", "public_api_routes", path)
        if FILE_SERVING.search(diff[:30000]):
            add("file_serving", "public_api_routes", path)
        if PATH_HANDLING.search(diff[:30000]):
            add("path", "app_code", path)
        if CRYPTO_OR_FFI.search(path) or CRYPTO_OR_FFI.search(diff[:30000]):
            add("crypto", "app_code", path)
        if CONCURRENCY.search(diff[:30000]):
            add("concurrency", "app_code", path)
        # Files that don't match any other pattern default to app_code
        if "app_code" not in surfaces and not any(
            signals[k] for k in ["docs", "test", "ci", "build_file", "k8s", "lockfile", "generated"]
        ):
            add("app_code", "app_code", path)

    # Ensure app_code is included if the diff shows actual source changes
    # BUT don't override when the change is purely documentation.
    if (any(ext in diff[:30000] for ext in [".ts",".js",".py",".rs",".go",".java"])
            and "app_code" not in surfaces
            and surfaces != ["documentation"]):
        surfaces.append("app_code")

    return surfaces, signals


def _detect_risks(surfaces: list[Surface], signals: dict[str, list[str]], additions: int, deletions: int) -> list[Risk]:
    """Cross-cutting risk flag detection."""
    risks: list[Risk] = []
    if signals.get("auth"):
        risks.append("touches_auth")
    if signals.get("secret"):
        risks.append("touches_secrets")
    if signals.get("db"):
        risks.append("touches_db_or_migration")
    if signals.get("public_api"):
        risks.append("touches_public_api")
    if signals.get("file_serving"):
        risks.append("touches_file_serving")
    if signals.get("path"):
        risks.append("touches_path_handling")
    if signals.get("crypto"):
        risks.append("touches_crypto_or_ffi")
    if signals.get("concurrency"):
        risks.append("touches_concurrency")
    if (additions + deletions) > 500:
        risks.append("large_change")
    return risks


def _severity(risks: list[Risk], intent: Intent) -> Severity:
    """Derive severity from risk + intent."""
    high_risks = {"touches_auth", "touches_secrets", "touches_db_or_migration",
                  "touches_public_api", "touches_file_serving", "touches_path_handling"}
    medium_risks = {"touches_crypto_or_ffi", "touches_concurrency", "large_change"}
    high = sum(1 for r in risks if r in high_risks)
    medium = sum(1 for r in risks if r in medium_risks)
    if intent in ("security_patch",) or high >= 1:
        return "critical" if high >= 2 else "high"
    if medium >= 2 or intent == "perf":
        return "medium"
    return "low"


def _domain(intent: Intent, severity: Severity, surfaces: list[Surface]) -> Domain:
    """Map to one of [Wang 2025]'s 5 review domains."""
    if intent in ("docs", "chore") and severity == "low":
        return "D5_prioritization_selection"  # can be deferred
    if intent in ("test", "build_ci") and "test_code" in surfaces:
        return "D3_review_assessment"
    if intent == "refactor":
        return "D4_code_refinement"
    if severity in ("critical", "high") or intent == "security_patch":
        return "D2_peer_review"
    return "D1_change_understanding"


def _tier(severity: Severity, intent: Intent, surfaces: list[Surface],
          risks: list[Risk], total_changes: int, branch: str) -> tuple[Tier, str]:
    """Route to T0-T4."""
    # T0: lockfile-only digest bumps
    if surfaces and all(s in ("dep_upgrade", "config") for s in surfaces) and intent == "chore":
        return "T0_skip", "lockfile-only digest bump — no review needed (Wang D5)"
    if branch and EXPERIMENTAL_BRANCH.search(branch):
        return "T0_skip", "experimental branch — don't auto-merge, skip review"

    # T4: human gate
    if "first_time_contributor" in risks:
        return "T4_human_gate", "first-time contributor — human review required"

    # T3: System-2 reasoning model
    high_risks = {"touches_auth", "touches_secrets", "touches_db_or_migration",
                  "touches_public_api", "touches_file_serving", "touches_path_handling",
                  "touches_crypto_or_ffi"}
    if any(r in risks for r in high_risks) or severity in ("critical", "high") or intent == "security_patch":
        return "T3_system2_deliberate", (
            "high-risk change — escalate to System-2 reasoning model "
            "(DeepSeek-R1-Distill-Qwen-32B / QwQ-32B / Qwen3-32B-Thinking)"
        )

    # T0: trivial docs/chore that are non-substantive
    if intent in ("docs",) and severity == "low":
        return "T1_system1_fast", "docs only, low risk — System-1 local model (Qwen2.5-Coder-3B)"

    # T2: System-1 verified (with deterministic checks)
    if intent in ("refactor", "test", "build_ci", "chore") and severity == "low":
        return "T2_system1_verified", "structural change, low risk — System-1 model + lint/typecheck"

    # T1: small features / bug fixes, no risk flags
    if intent in ("bug_fix", "feature", "perf") and total_changes < 200 and severity == "low":
        return "T1_system1_fast", (
            f"small {intent} — System-1 local model sufficient "
            "(Qwen2.5-Coder-7B / DeepSeek-Coder-V2-Lite)"
        )

    # Default: medium-risk System-2
    if severity == "medium":
        return "T3_system2_deliberate", (
            "medium-risk change — System-2 reasoning model recommended"
        )

    # Fallback: System-1 verified
    return "T2_system1_verified", "default — System-1 model with deterministic checks"


def classify_pr_ontology(
    title: str,
    body: str,
    file_paths: list[str],
    diff: str = "",
    branch: str = "",
    additions: int = 0,
    deletions: int = 0,
) -> PRClassification:
    """Classify a PR using the layered ontology."""
    n_files = len(file_paths)
    text = f"{title.strip()}\n{(body or '')[:2000]}".strip()

    surfaces, signals = _detect_surfaces(file_paths, diff)
    total_changes = additions + deletions

    # --- Layer 1: intent detection ----------------------------------------
    # INVARIANT: diff signals (file paths + content) trump title heuristics
    # when the surface is unambiguous. Title is only the fallback when the
    # surface is mixed or empty.
    intent: Intent | None = None

    n_test = len(signals.get("test", []))
    n_app = sum(1 for p in file_paths
                if not TEST_FILE.match(p) and not DOCS_FILE.match(p)
                and not CI_FILE.match(p) and not BUILD_FILE.search(p)
                and not CONFIG_FILE.search(p) and not LOCKFILE_ONLY.match(p)
                and not GENERATED.match(p))

    # Renovation digest-only (lockfile only)
    if n_files and all(LOCKFILE_ONLY.match(p) for p in file_paths):
        intent = "chore"  # but with T0_skip tier

    # Surface-conditional rules (DIFF WINS OVER TITLE)
    # 1. Pure surface: every file is the same category -> that category wins
    if surfaces and all(s == "build_ci" for s in surfaces):
        intent = "build_ci"
    elif surfaces and all(s == "documentation" for s in surfaces):
        intent = "docs"
    elif surfaces and all(s == "test_code" for s in surfaces):
        intent = "test"
    elif surfaces and all(s == "dep_upgrade" for s in surfaces):
        intent = "chore"
    elif surfaces and all(s == "schema_migration" for s in surfaces):
        intent = "feature"  # schema work is typically a feature
    # 2. Mixed surface: test dominates -> test wins
    elif n_test > 0 and n_test > n_app:
        intent = "test"
    # 3. Mixed but mostly build/CI + docs (e.g. devcontainer + README)
    elif "build_ci" in surfaces and "documentation" not in surfaces and "app_code" not in surfaces:
        intent = "build_ci"
    # 3b. Build/CI dominates over a small app_code presence
    elif ("build_ci" in surfaces
          and "documentation" not in surfaces
          and len(signals.get("ci", [])) + len(signals.get("build_file", [])) >= max(1, n_app)):
        intent = "build_ci"
    # 4. Surface clear (one signal, no app_code)
    elif len(surfaces) == 1 and "app_code" not in surfaces:
        # Trust the single surface signal
        intent_map = {
            "build_ci": "build_ci",
            "documentation": "docs",
            "test_code": "test",
            "schema_migration": "feature",
            "config": "chore",
            "infra_k8s": "build_ci",
            "generated_bundle": "chore",
        }
        intent = intent_map.get(surfaces[0])

    # Title indicates test-scope work (Conventional Commit "test:" prefix or
    # bracketed "(test)" / "(bun:test)" prefix), regardless of surface mix.
    if intent in ("bug_fix", "feature", "refactor"):
        if re.match(r"^\s*(test|tests)\s*(\(|:)", title):
            if n_test > 0:
                intent = "test"

    # Fallback: title heuristics, ONLY when surface is ambiguous
    if intent is None:
        if TITLE_REVERT.search(text):
            intent = "revert"
        elif TITLE_SECURITY.search(text):
            intent = "security_patch"
        elif TITLE_PERF.search(text):
            intent = "perf"
        elif TITLE_CI.search(text):
            intent = "build_ci"
        elif TITLE_DEPEND.search(text):
            intent = "chore"
        elif TITLE_CHORE.search(text):
            intent = "chore"
        elif TITLE_BUG.search(text):
            intent = "bug_fix"
        elif TITLE_REFACTOR.search(text):
            intent = "refactor"
        elif TITLE_FEATURE.search(text):
            intent = "feature"
        elif TITLE_DOCS.search(text):
            intent = "docs"

    if intent is None:
        intent = "mixed_or_unclear"

    # --- Layer 3: risks ----------------------------------------------------
    risks = _detect_risks(surfaces, signals, additions, deletions)

    # --- Layer 4: severity -------------------------------------------------
    severity = _severity(risks, intent)

    # --- Routing ----------------------------------------------------------
    tier, hint = _tier(severity, intent, surfaces, risks, total_changes, branch)

    # --- Review domain -----------------------------------------------------
    domain = _domain(intent, severity, surfaces)

    # --- must_check from risks --------------------------------------------
    must_check: list[str] = []
    risk_to_check = {
        "touches_auth": "review auth flow for regression; session token handling",
        "touches_secrets": "secrets not logged/exposed; rotation impact",
        "touches_db_or_migration": "migration data-loss risk; test on prod-schema copy",
        "touches_public_api": "route access controls; unintended public endpoints",
        "touches_file_serving": "path sanitization; directory traversal",
        "touches_path_handling": "path traversal; null bytes; symlinks",
        "touches_crypto_or_ffi": "FFI/crypto boundary integrity; memory safety",
        "touches_concurrency": "race conditions; deadlock potential",
        "large_change": "reviewer should pay attention — large surface area",
    }
    for r in sorted(set(risks)):
        if r in risk_to_check:
            must_check.append(risk_to_check[r])
    if intent == "perf":
        must_check.append("benchmark before/after; no regression on baseline paths")
    if intent == "security_patch":
        must_check.append("CVE reference; threat model coverage")

    # Confidence based on intent clarity
    if intent in ("docs", "test", "build_ci", "chore", "perf", "security_patch", "revert") and tier != "T0_skip":
        confidence = 0.85
    elif intent in ("bug_fix", "feature", "refactor") and risks:
        confidence = 0.8
    elif intent in ("bug_fix", "feature", "refactor"):
        confidence = 0.7
    else:
        confidence = 0.4

    return PRClassification(
        pr_intent=intent,
        pr_surfaces=sorted(set(surfaces)),
        pr_risks=sorted(set(risks)),
        pr_severity=severity,
        complexity_score=severity_to_complexity(severity),
        review_domain=domain,
        review_tier=tier,
        confidence=confidence,
        must_check=sorted(set(must_check)),
        recommended_model_family=hint,
        signals=signals,
    )


if __name__ == "__main__":
    print("ontology classifier ready")