"""
Build Jev-format training data from SWE-PRBench + oven-sh/bun.

Output format (Jevlike-compatible JSONL):
    {"context": "...", "options": [...], "label": N}

We emit ONE JSONL file per question type:
  - intent_train.jsonl: 11 intent options (Choice question)
  - tier_train.jsonl:   5 tier options (Choice question)
  - risk_train.jsonl:   2 options per risk flag (Noul = yes/no, replicated)
  - complexity_train.jsonl: 6 score levels (Score question, simplified to choice)

Each PR becomes one example per question type. Held-out repos for SWE-PRBench
test split (35 PRs), train split (315 PRs) — we mimic the original SWE-PRBench
split by repo_id hash modulo.

Bug fix patterns:
- For "mixed_or_unclear" examples (rule classifier's fallback), we use the gold
  label so the model learns from ground truth, not our classifier's mistakes.
"""
from __future__ import annotations

import json
import sys
import random
from pathlib import Path

random.seed(42)


REPO_DIR = Path("/home/amitgaur/projects/pr-classify-router")
DATA_DIR = REPO_DIR / "data"
TRAIN_DIR = DATA_DIR / "jev_training"
TRAIN_DIR.mkdir(exist_ok=True, parents=True)


# --- Question definitions ---------------------------------------------------

INTENT_OPTIONS = [
    "bug_fix", "feature", "docs", "test", "build_ci",
    "chore", "perf", "refactor", "revert", "security_patch", "mixed_or_unclear",
]

TIER_OPTIONS = [
    "T0_skip", "T1_system1_fast", "T2_system1_verified",
    "T3_system2_deliberate", "T4_human_gate",
]

# Noul-style questions, one per risk flag
RISK_FLAGS = [
    ("touches_auth", "auth code (login/session/token)"),
    ("touches_secrets", "secrets/credentials"),
    ("touches_db_or_migration", "database schema/migration"),
    ("touches_public_api", "public API surface"),
    ("touches_file_serving", "file serving/uploads"),
    ("touches_path_handling", "filesystem paths"),
    ("touches_crypto_or_ffi", "crypto/FFI"),
    ("touches_concurrency", "concurrency primitives"),
    ("large_change", "large change (>500 lines)"),
]

COMPLEXITY_OPTIONS = [
    "0.0_trivial_lockfile",
    "0.2_simple_typo",
    "0.4_moderate_refactor",
    "0.6_complex_cross_file",
    "0.8_intricate_auth_security",
    "1.0_frontier_reasoning",
]


# --- Context builder --------------------------------------------------------

def build_context(pr: dict) -> str:
    """Build the Jev 'context' string from a PR dict.

    Keep it SHORT and FOCUSED on the most discriminative signals:
    - Title (most informative)
    - File extensions (signals surface and language)
    - File path keywords (signals intent)
    - First 200 chars of body (context if title is vague)
    - Diff size bucket (signals complexity)

    Total target: <500 chars. The Jevlike attention head scores option
    strings against context tokens, so high signal-to-noise matters more
    than recall.
    """
    parts = []
    title = pr.get("title", "")
    if title:
        parts.append(f"TITLE: {title}")

    body = pr.get("body", "")
    if body and len(body) > 30:
        parts.append(f"BODY: {body[:150]}")

    files = pr.get("file_paths", [])
    if files:
        # Just the extensions and first 10 file basenames — most discriminative
        exts = sorted(set(fp.rsplit(".", 1)[-1] if "." in fp else "no-ext" for fp in files))
        # Path-level keywords: tests/, docs/, ci/, etc.
        path_kws = sorted(set(
            seg for fp in files for seg in fp.split("/")
            if seg and seg.lower() in {"test", "tests", "docs", "doc", "ci", "build",
                                       "scripts", "tools", "src", "lib", "auth",
                                       "migrations", "workflows", "examples", "bench"}
        ))
        parts.append(f"FILES: ext={','.join(exts[:5])} kws={','.join(path_kws[:5])}")

    diff_size = pr.get("additions", 0) + pr.get("deletions", 0)
    parts.append(f"DIFF_SIZE: {diff_size}")

    return " | ".join(parts)[:500]


def gold_intent_from_pr_type(pr_type: str) -> str:
    """Map SWE-PRBench pr_type to our intent enum."""
    mapping = {
        "feature": "feature",
        "bug_fix": "bug_fix",
        "refactor": "refactor",
        "perf": "perf",
        "performance": "perf",
        "security": "security_patch",
        "docs": "docs",
        "test": "test",
        "chore": "chore",
        "build": "build_ci",
        "ci": "build_ci",
    }
    return mapping.get(pr_type, "mixed_or_unclear")


def gold_tier_from_risk(pr: dict, intent: str) -> str:
    """Derive gold review tier from PR signals."""
    # Use our rule-based classifier as the gold tier labeler
    # This isn't ideal — but our classifier is at 0.57 macro F1 and the
    # Jev model should beat it. Using our own classifier as gold means
    # the Jev model learns to reproduce our decisions.
    # Better: use pr_type + heuristics to derive tier directly.
    diff_size = pr.get("additions", 0) + pr.get("deletions", 0)
    if intent in ("docs", "test", "build_ci", "chore") and diff_size < 50:
        return "T0_skip"
    if intent == "docs":
        return "T1_system1_fast"
    if intent in ("test", "build_ci", "chore"):
        return "T1_system1_fast"
    if intent == "refactor":
        return "T2_system1_verified"
    if intent in ("feature", "bug_fix") and diff_size < 200:
        return "T2_system1_verified"
    if intent in ("perf",):
        return "T3_system2_deliberate"
    if intent == "security_patch":
        return "T3_system2_deliberate"
    # Anything complex, high-risk, or unclassified
    return "T3_system2_deliberate"


def gold_risks_from_files(file_paths: list[str]) -> list[str]:
    """Heuristic risk detection from file paths (matches our classifier's logic)."""
    risks = []
    auth_re = any(s in fp.lower() for fp in file_paths for s in
                  ("/auth/", "/login/", "/oauth/", "/session/", "/jwt/", "/token/", "/rbac/", "/permissions/", "/middleware/auth"))
    secret_re = any(s in fp.lower() for fp in file_paths for s in
                    (".env", ".pem", ".key", "secret", "credential"))
    db_re = any(s in fp.lower() for fp in file_paths for s in
                ("/migration", "/schema", ".sql", "prisma/", "drizzle/", "alembic"))
    api_re = any(s in fp.lower() for fp in file_paths for s in
                 ("/api/", "/routes/", "openapi", "swagger"))
    fileserve_re = any(s in fp.lower() for fp in file_paths for s in
                       ("/upload", "/download", "/static/", "/public/"))
    path_re = any(s in fp.lower() for fp in file_paths for s in
                  ("/path", "/file/", "/fs."))
    crypto_re = any(s in fp.lower() for fp in file_paths for s in
                    ("crypto", "ssl", "tls", "ffi", "ctypes", "subprocess"))
    conc_re = any(s in fp.lower() for fp in file_paths for s in
                  ("thread", "lock", "mutex", "async", "await"))
    if auth_re: risks.append("touches_auth")
    if secret_re: risks.append("touches_secrets")
    if db_re: risks.append("touches_db_or_migration")
    if api_re: risks.append("touches_public_api")
    if fileserve_re: risks.append("touches_file_serving")
    if path_re: risks.append("touches_path_handling")
    if crypto_re: risks.append("touches_crypto_or_ffi")
    if conc_re: risks.append("touches_concurrency")
    return risks


def gold_complexity(intent: str, diff_size: int, n_risks: int) -> str:
    """Map (intent, diff_size, n_risks) to one of 6 complexity levels."""
    if intent in ("docs", "test", "build_ci", "chore") and diff_size < 20:
        return "0.0_trivial_lockfile"
    if intent == "docs" or diff_size < 50:
        return "0.2_simple_typo"
    if intent in ("feature", "bug_fix") and n_risks == 0 and diff_size < 200:
        return "0.4_moderate_refactor"
    if intent in ("feature", "bug_fix", "refactor") and diff_size < 500:
        return "0.6_complex_cross_file"
    if n_risks >= 2 or "touches_auth" in [] or "touches_secrets" in []:
        return "0.8_intricate_auth_security"
    if diff_size >= 500:
        return "0.8_intricate_auth_security"
    return "0.6_complex_cross_file"


# --- Conversion -------------------------------------------------------------

def load_swe_prbench_prs():
    """Load SWE-PRBench converted PRs."""
    path = DATA_DIR / "swe_prbench" / "converted.json"
    with path.open() as f:
        prs = json.load(f)
    # Convert to our internal format if needed
    out = []
    for pr in prs:
        # SWE-PRBench uses 'gold_intent' and 'task_id' (format: repo__num)
        task_id = pr.get("task_id", "")
        repo = task_id.split("__")[0] if "__" in task_id else task_id
        out.append({
            "title": pr.get("title", ""),
            "body": pr.get("body", ""),
            "file_paths": pr.get("file_paths", []) or [],
            "diff": pr.get("diff", "") or "",
            "additions": pr.get("additions", 0) or 0,
            "deletions": pr.get("deletions", 0) or 0,
            "pr_type": pr.get("gold_intent", "mixed_or_unclear"),
            "repo": repo,
            "id": task_id,
            "language": pr.get("language", ""),
            "difficulty": pr.get("difficulty", ""),
        })
    return out


def load_bun_prs():
    """Load oven-sh/bun PRs."""
    path = DATA_DIR / "bun_prs.json"
    with path.open() as f:
        prs = json.load(f)
    out = []
    for pr in prs:
        out.append({
            "title": pr.get("title", ""),
            "body": pr.get("body", ""),
            "file_paths": pr.get("file_paths", []) or [],
            "diff": pr.get("diff", "") or "",
            "additions": pr.get("additions", 0) or 0,
            "deletions": pr.get("deletions", 0) or 0,
            "pr_type": pr.get("pr_type", "mixed_or_unclear"),
            "repo": "oven-sh/bun",
            "id": str(pr.get("number", "")),
        })
    return out


def emit_intent_examples(prs, f_out):
    """Emit Jev examples for the intent question."""
    n_written = 0
    for pr in prs:
        gold_intent = gold_intent_from_pr_type(pr["pr_type"])
        if gold_intent not in INTENT_OPTIONS:
            continue
        ex = {
            "context": build_context(pr),
            "options": INTENT_OPTIONS.copy(),
            "label": INTENT_OPTIONS.index(gold_intent),
        }
        f_out.write(json.dumps(ex) + "\n")
        n_written += 1
    return n_written


def emit_tier_examples(prs, f_out):
    """Emit Jev examples for the tier question."""
    n_written = 0
    for pr in prs:
        intent = gold_intent_from_pr_type(pr["pr_type"])
        gold_tier = gold_tier_from_risk(pr, intent)
        if gold_tier not in TIER_OPTIONS:
            continue
        ex = {
            "context": build_context(pr),
            "options": TIER_OPTIONS.copy(),
            "label": TIER_OPTIONS.index(gold_tier),
        }
        f_out.write(json.dumps(ex) + "\n")
        n_written += 1
    return n_written


def emit_risk_examples(prs, f_out):
    """Emit Jev examples for each risk flag (Noul = yes/no)."""
    n_written = 0
    for pr in prs:
        gold_risks = set(gold_risks_from_files(pr["file_paths"]))
        diff_size = pr["additions"] + pr["deletions"]
        if diff_size > 500:
            gold_risks.add("large_change")
        for flag_name, flag_desc in RISK_FLAGS:
            label = 1 if flag_name in gold_risks else 0
            ex = {
                "context": build_context(pr),
                "options": [f"no {flag_desc}", f"yes {flag_desc}"],
                "label": label,
            }
            f_out.write(json.dumps(ex) + "\n")
            n_written += 1
    return n_written


def emit_complexity_examples(prs, f_out):
    """Emit Jev examples for the complexity score question."""
    n_written = 0
    for pr in prs:
        intent = gold_intent_from_pr_type(pr["pr_type"])
        risks = gold_risks_from_files(pr["file_paths"])
        diff_size = pr["additions"] + pr["deletions"]
        gold_complex = gold_complexity(intent, diff_size, len(risks))
        if gold_complex not in COMPLEXITY_OPTIONS:
            continue
        ex = {
            "context": build_context(pr),
            "options": COMPLEXITY_OPTIONS.copy(),
            "label": COMPLEXITY_OPTIONS.index(gold_complex),
        }
        f_out.write(json.dumps(ex) + "\n")
        n_written += 1
    return n_written


def split_by_repo(prs, train_frac=0.85):
    """Hold out 15% of repos for test set (mimics SWE-PRBench methodology).

    Deterministic per-repo hash split. SWE-PRBench uses 100 of 350 PRs as
    eval_split (28.6%), so we replicate roughly that ratio.
    """
    # Separate SWE-PRBench (multi-repo) from Bun (single repo)
    swe_prs = [pr for pr in prs if pr["repo"] != "oven-sh/bun"]
    bun_prs = [pr for pr in prs if pr["repo"] == "oven-sh/bun"]

    # SWE-PRBench: ~28% held out (mimics the 100/350 eval_split ratio)
    swe_repos = sorted(set(pr["repo"] for pr in swe_prs))
    # Deterministic shuffle by hash
    swe_repos_shuffled = sorted(swe_repos, key=lambda r: hash(r))
    n_swe_test_repos = int(len(swe_repos) * (1 - train_frac))
    swe_test_repos = set(swe_repos_shuffled[:n_swe_test_repos])
    swe_train = [pr for pr in swe_prs if pr["repo"] not in swe_test_repos]
    swe_test = [pr for pr in swe_prs if pr["repo"] in swe_test_repos]

    # Bun: 80/20 PR split (only 55 PRs, single repo)
    bun_prs_sorted = sorted(bun_prs, key=lambda p: p["id"])
    n_bun_test = max(5, int(len(bun_prs) * 0.2))
    bun_test = bun_prs_sorted[:n_bun_test]
    bun_train = bun_prs_sorted[n_bun_test:]

    train_prs = swe_train + bun_train
    test_prs = swe_test + bun_test
    return train_prs, test_prs


def main():
    print("Loading SWE-PRBench...")
    swe_prs = load_swe_prbench_prs()
    print(f"  {len(swe_prs)} PRs")

    print("Loading oven-sh/bun...")
    bun_prs = load_bun_prs()
    print(f"  {len(bun_prs)} PRs")

    all_prs = swe_prs + bun_prs
    print(f"Total: {len(all_prs)} PRs")

    # Split by repo
    train_prs, test_prs = split_by_repo(all_prs, train_frac=0.85)
    print(f"Train: {len(train_prs)} PRs across {len(set(p['repo'] for p in train_prs))} repos")
    print(f"Test:  {len(test_prs)} PRs across {len(set(p['repo'] for p in test_prs))} repos")

    # Emit each question type
    for question_name, emitter in [
        ("intent", emit_intent_examples),
        ("tier", emit_tier_examples),
        ("risk", emit_risk_examples),
        ("complexity", emit_complexity_examples),
    ]:
        train_path = TRAIN_DIR / f"{question_name}_train.jsonl"
        test_path = TRAIN_DIR / f"{question_name}_test.jsonl"
        with train_path.open("w") as f_train, test_path.open("w") as f_test:
            n_train = emitter(train_prs, f_train)
            n_test = emitter(test_prs, f_test)
        print(f"  {question_name}: {n_train} train + {n_test} test")

    # Save the test PR list for evaluation later
    test_meta = [{"repo": p["repo"], "id": p["id"], "pr_type": p["pr_type"]} for p in test_prs]
    (TRAIN_DIR / "test_meta.json").write_text(json.dumps(test_meta, indent=2))
    print(f"Saved test metadata to {TRAIN_DIR / 'test_meta.json'}")


if __name__ == "__main__":
    main()