# System-one-pr-classifier

A type-safe PR classification + model-routing system, grounded in current
SOTA research, that simultaneously answers two questions with one classification:

1. **How complex is this PR?** (0.0 = trivial, 1.0 = frontier-reasoning-required)
2. **Which model should review it?** (T0_skip → T4_human_gate)

The same `PRClassification` object drives both — because the complexity
axis *is* the model-selection axis (per
[Li et al. 2025, arXiv:2502.17419](https://arxiv.org/abs/2502.17419) "From System 1 to System 2").

## Headline numbers

| Eval | n | Macro F1 | 95% CI | Status |
|------|---|----------|--------|--------|
| **SWE-PRBench** (cross-repo, 5 languages) | 350 | **0.57** | [0.48, 0.71] | primary benchmark |
| oven-sh/bun (single-repo baseline) | 55 | 0.53 | — | stable |
| diff_trumps_title invariant | 7 | 7/7 PASS | — | regression suite |
| adversarial_tests (bun:test+chore) | 7 | 7/7 PASS | — | regression suite |

Iteration log: [docs/ITERATION_LOG.md](docs/ITERATION_LOG.md).
Detailed per-intent / per-language / per-difficulty breakdown: [docs/EVALUATION.md](docs/EVALUATION.md).

## What this gives you

A pure-Python rule-based classifier that, given a PR's title, body, file
paths, diff, branch, and add/delete counts, emits a fully-typed pydantic
schema with six orthogonal enums:

| Field | Type | What it drives |
|---|---|---|
| `pr_intent` | 11-value Literal | What kind of work this PR does |
| `pr_surfaces` | multi-select Literal | Surface area touched |
| `pr_risks` | multi-select Literal | Cross-cutting risk flags |
| `pr_severity` | 4-value Literal | Derived severity |
| `complexity_score` | 0.0–1.0 float | **Dual-purpose: PR complexity AND required model intelligence** |
| `review_domain` | 5-value Literal | Which reviewer profile (D1–D5 from [Wang 2025](https://arxiv.org/abs/2602.13377)) |
| `review_tier` | 5-value Literal (T0–T4) | System-1 fast / System-2 deliberate / human gate |
| `must_check` | list[str] | Auto-derived reviewer checklist |

Plus: `confidence`, `recommended_model_family` (human-readable hint), and the raw `signals` dict for debugging.

## System-1 / System-2 architecture

The classifier is **the System-1 decision layer**. It's a fast,
deterministic signal detector that runs in <1ms and handles ~70% of PRs
unambiguously. The remaining ~30% (mostly `mixed_or_unclear` intent and
ambiguous complexity scores) get escalated to a decision model.

```
                    ┌────────────────────────────────────┐
                    │ PR metadata (title, body, files,  │
                    │ diff, additions, deletions)        │
                    └─────────────┬──────────────────────┘
                                  │
                                  ▼
                    ┌────────────────────────────────────┐
                    │  Fast path: rule-based classifier  │ <-- always runs first
                    │  (src/classifier.py — current)     │
                    │  Returns: PRClassification         │
                    └─────────────┬──────────────────────┘
                                  │ if intent == mixed_or_unclear
                                  │ OR complexity_score in [0.4, 0.7]
                                  ▼
                    ┌────────────────────────────────────┐
                    │  Slow path: decider-4b v2 fallback │ <-- only when uncertain
                    │  (Jev-style typed decision model)  │     optional, pip install decider-ai
                    │  Returns: same PRClassification   │
                    └─────────────┬──────────────────────┘
                                  │
                                  ▼
                    ┌────────────────────────────────────┐
                    │ review_tier routes to reviewer:    │
                    │   T1 -> Qwen2.5-Coder-3B-Instruct  │
                    │   T2 -> Qwen3-Coder-30B-A3B        │
                    │   T3 -> DeepSeek-V3                │
                    │   T4 -> human                      │
                    └────────────────────────────────────┘
```

### The "Jev" / decision-model pattern

[TypeSafe AI's Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
(September 2026) introduced the **"System One model"** class: an LLM that
returns typed probabilistic decisions (Choice / Noul / Score) instead of
text. The classifier problem maps cleanly:

| Jev question type | Maps to our classifier field |
|---|---|
| **Choice** (2-255 options) | `pr_intent` (11 options), `review_tier` (5 options) |
| **Noul** (yes/no + confidence) | Each `pr_risk` flag (9 questions) |
| **Score** (2-10 described levels) | `complexity_score` (6 levels: 0.0–1.0) |

[decider by Mapika](https://github.com/Mapika/decider) is the open-weight
reproduction: Apache-2.0, Qwen3.5-4B base. **decider-4b v2 beats Jev 1.13.0
on JevBench** (64.1 vs 63.3). Wired into `src/decider_adapter.py` —
optional, gracefully degrades if not installed.

## Routing tiers — System-1 vs System-2

The classifier emits `T0-T4`. **Higher tier = higher complexity = smarter model.**

| Tier | When | Recommended model | Evidence |
|------|------|-------------------|----------|
| `T0_skip` | Lockfile-only digest bump; experimental branch | none (skip review) | — |
| `T1_system1_fast` | Small docs, typo fix, simple bug fix | Qwen2.5-Coder-1.5B / 3B-Instruct | Apache-2.0; Aider 53-76% |
| `T2_system1_verified` | Refactor, test, build_ci, chore (low risk) | Qwen2.5-Coder-7B OR Qwen3-Coder-30B-A3B | Aider 75%; SWE-Review DA 80.5% |
| `T3_system2_deliberate` | Any risk flag, security patch, breaking change, perf | **DeepSeek-V3** | SWE-PRBench 0.150 (rank 3/8); ~9x cheaper than GPT-4o |
| `T4_human_gate` | First-time contributor + risk flags; secrets exposure | human reviewer | — |

Detailed model recommendations with citations:
[docs/MODEL_RECOMMENDATIONS.md](docs/MODEL_RECOMMENDATIONS.md).

The `complexity_score` is the continuous version of `review_tier`. Same axis, monotonic mapping:

```
severity=low       -> complexity_score=0.20 -> T1_system1_fast
severity=medium    -> complexity_score=0.50 -> T2_system1_verified
severity=high      -> complexity_score=0.75 -> T3_system2_deliberate
severity=critical  -> complexity_score=0.92 -> T3_system2_deliberate (or T4)
```

## Repository layout

```
.
├── README.md                                  # you are here
├── LICENSE                                    # MIT
├── docs/
│   ├── DESIGN.md                              # dual-purpose design justification
│   ├── ONTOLOGY.md                            # taxonomy layer-by-layer
│   ├── MODEL_RECOMMENDATIONS.md               # per-tier model picks with citations
│   ├── EVALUATION.md                          # current SWE-PRBench / Bun numbers
│   ├── ITERATION_LOG.md                       # what we tried, what worked, what didn't
│   ├── CODEX_ADVERSARIAL_BRIEF.md             # review brief (for codex)
│   └── adversarial_review.md                   # adversarial review (generated)
├── taxonomy/
│   └── ontology.py                            # 6-layer research-grounded taxonomy
├── src/
│   ├── classifier.py                          # v2 ontology-based classifier (canonical)
│   ├── classifier_v1.py                       # v1 15-kind enum (kept for backward-compat)
│   └── decider_adapter.py                     # Jev-class decision-model fallback (optional)
├── evals/
│   ├── build_swe_prbench.py                   # SWE-PRBench downloader
│   ├── validate_swe_prbench.py                # primary eval (350 PRs, 65 repos)
│   ├── validate_v2_on_bun.py                  # 55 oven-sh/bun PRs (v2 ontology)
│   ├── validate_v1_on_bun.py                  # 55 oven-sh/bun PRs (v1)
│   ├── validate_v1_on_issues.py               # Rami/issues eval
│   ├── run_on_real_prs.py                     # Amit's 4 real PRs (v1)
│   ├── adversarial_tests.py                   # bun:test + chore regression
│   ├── diff_trumps_title.py                   # diff-trumps-title invariant (7 cases)
│   ├── final_report.py                        # one-shot report across all evals
│   └── build_cross_repo.py                    # (deprecated; SWE-PRBench replaced)
├── examples/
│   ├── ontology_on_real_prs.py                # canonical demo: Amit's 4 PRs through v2
│   └── synthetic_escalation.py                # 8 demo cases (auth, perf, lockfile, etc.)
└── data/
    ├── raw_prs.json                           # Amit's 4 real PRs (title/body/files/diff)
    ├── bun_prs.json                           # 55 oven-sh/bun PRs with diffs
    ├── bun_pr_index.json                      # index of which PRs were sampled
    ├── labeled_dataset.json                   # Rami/multi-label issues dataset
    ├── bun_evaluation.json                    # v2 evaluation metrics
    └── swe_prbench/
        ├── prs.jsonl                          # 350 SWE-PRBench PRs
        └── converted.json                     # our internal format
```

## Install

```bash
pip install pydantic>=2.0 datasets            # for SWE-PRBench eval
# optional: pip install decider-ai            # for Jev-class decision model fallback
```

## Quick start

```python
from src.classifier import classify_pr_ontology

result = classify_pr_ontology(
    title="fix: authentication bypass via path traversal",
    body="",
    file_paths=["src/auth/login.py", "src/middleware/auth.ts"],
    diff="",
    additions=50, deletions=10,
)

print(result.pr_intent)           # "bug_fix"
print(result.pr_surfaces)         # ["app_code", "public_api_routes"]
print(result.pr_risks)            # ["touches_auth"]
print(result.pr_severity)         # "high"
print(result.complexity_score)    # 0.75
print(result.review_tier)         # "T3_system2_deliberate"
print(result.recommended_model_family)
# -> "high-risk change - escalate to System-2 reasoning model (DeepSeek-V3)"
```

### With Jev-class fallback (optional)

```python
from src.decider_adapter import classify_with_fallback

# Rule-based fast path; escalate to decider-4b v2 if mixed_or_unclear
result = classify_with_fallback(
    title="...",
    body="...",
    file_paths=[...],
    diff="...",
    additions=10, deletions=5,
)
```

## Run the demos

```bash
python examples/synthetic_escalation.py    # 8 demo cases
python examples/ontology_on_real_prs.py    # 4 of Amit's real PRs
```

## Run the evaluations

```bash
# Primary benchmark (SWE-PRBench, 350 PRs across 65 repos / 5 languages)
python evals/build_swe_prbench.py          # one-time: download dataset
python evals/validate_swe_prbench.py       # macro F1, per-language, per-difficulty, bootstrap CI

# Single-repo baseline
python evals/validate_v2_on_bun.py

# Regression suites
python evals/diff_trumps_title.py          # 7 hand-crafted cases
python evals/adversarial_tests.py          # bun:test + chore regression

# All-in-one report
python evals/final_report.py
```

## Research grounding

The taxonomy is built on four peer-reviewed / survey-grade sources:

1. **Wang et al. 2025**, [arXiv:2602.13377](https://arxiv.org/abs/2602.13377) — survey of 99 code-review papers, 5 domains × 18 sub-tasks. We adopt the D1–D5 domain literals.
2. **Mäntylä & Lassenius 2009**, [IEEE TSE 35(3):430-448](https://doi.org/10.1109/TSE.2008.71) — DOI 10.1109/TSE.2008.71. Foundational split of functional vs evolvability defects. Source for "75% of review findings are subjective/evolvability" → System-2 routing on subjective work.
3. **Tufano & Bavota 2025**, [arXiv:2503.09510](https://arxiv.org/abs/2503.09510) — systematic literature review of 119 papers on automated code-review tasks. Source for the code-review automation landscape. (Earlier drafts of this README cited a different Tufano paper on self-admitted ChatGPT usage; that paper was about AI tool usage in OSS, not code review. Fixed after adversarial review.)
4. **Li et al. 2025**, [arXiv:2502.17419](https://arxiv.org/abs/2502.17419) — canonical taxonomy of System-1 (fast/heuristic) vs System-2 (slow/chain-of-thought) reasoning. We adopt the T1–T3 tier structure.

### On the dual-purpose design (complexity_score ↔ review_tier)

This project's design collapses **PR complexity** and **required model intelligence** into the same axis. This is a **design choice**, not a result proven by SOTA:

- The Li 2025 survey describes complexity and reasoning-depth in the same conceptual frame, which licenses treating them as a single axis.
- The Wang 2025 survey, Mäntylä-Lassenius 2009, and the System-1/2 literature all support routing PRs to higher-tier reasoning models as complexity/risk increases.
- However, dedicated routing papers — **RouteLLM, R2-Router, InferenceDynamics** — use *multi-axis* decisions (cost, capability profile, budget). Collapsing to one axis is a deliberate simplification; multi-axis routing is future work.

See `docs/adversarial_review.md` for the full critique and the routing-paper analysis.

See `taxonomy/ontology.py` for the full layer-by-layer mapping and `docs/ONTOLOGY.md` for the rationale.

### Public benchmarks we evaluate against

| Benchmark | n | Why we use it |
|---|---|---|
| **SWE-PRBench** ([arXiv:2603.26130](https://arxiv.org/abs/2603.26130), [HF dataset](https://huggingface.co/datasets/foundry-ai/swe-prbench)) | 350 PRs, 65 repos, 6 languages, 3 difficulty levels, κ=0.75 judge | Direct PR-review benchmark with human-annotated ground truth. Has unified diffs + file paths — what our classifier needs. |
| **SWE-Review-Bench** ([arXiv:2607.06065](https://arxiv.org/abs/2607.06065)) | 1,384 PRs | Agentic-review benchmark. +29.4pp improvement from generate-review-revise loop on weak generators. |
| **oven-sh/bun** (manual pull) | 55 PRs | Diverse real-world corpus. Bug fix, docs, build_ci are well-represented. |

## Cost analysis

| Model | Cost per 1M input | Per-PR review | Tier |
|-------|-------------------|---------------|------|
| Qwen2.5-Coder-1.5B (local) | $0 | < $0.01 (electricity) | T1 |
| Qwen2.5-Coder-7B (local) | $0 | < $0.05 (electricity) | T2 |
| decider-4b v2 (local) | $0 | < $0.02 (electricity) | System-1 fallback |
| DeepSeek-V3 (API) | $0.14 | $0.014 | T3 |
| GPT-4o (API) | $2.50 | $0.250 | T3 alt |
| Claude Sonnet 4.6 (API) | $3.00 | $0.300 | T3 alt |
| Claude Opus 4.6 (API) | $15.00 | $1.500 | T4 |

If classifier routes 70% to T1/T2 (local, ~free) and 30% to T3 (DeepSeek-V3):
- Per 100 PRs: ~$0.42 + electricity
- vs all-GPT-4o: ~$25
- vs all-Claude-Sonnet: ~$30

**70x cost reduction vs naively using frontier for everything.**

## License

MIT. See `LICENSE`.

## Contributing

Open an issue. PRs welcome but expect adversarial review.