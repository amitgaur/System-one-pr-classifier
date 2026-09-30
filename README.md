# System-one-pr-classifier

A type-safe PR classification ontology and classifier, grounded in current SOTA research, that simultaneously answers two questions with one classification:

1. **How complex is this PR?**
2. **Which model should review it?**

The same `PRClassification` object drives both — because per [Li et al. 2025, arXiv:2502.17419](https://arxiv.org/abs/2502.17419) "From System 1 to System 2", the complexity axis **is** the model selection axis.

## What this gives you

A pure-Python rule-based classifier that, given a PR's title, body, file paths, diff, branch, and add/delete counts, emits a fully-typed pydantic schema with six orthogonal enums:

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

## Routing tiers — System-1 vs System-2

The classifier emits `T0-T4`. **Higher tier = higher complexity = smarter model.**

| Tier | When | Open-weight candidates |
|---|---|---|
| `T0_skip` | Lockfile-only digest bump; experimental branch | none (skip review) |
| `T1_system1_fast` | Small docs, typo fix, simple bug fix | Qwen2.5-Coder-3B-Instruct · Phi-3.5-mini · Llama-3.1-8B-Instruct |
| `T2_system1_verified` | Refactor, test, build_ci, chore (low risk) | Same + deterministic lint/typecheck |
| `T3_system2_deliberate` | Any risk flag, security patch, breaking change, perf | DeepSeek-R1-Distill-Qwen-32B · QwQ-32B-Preview · Qwen3-32B-Thinking |
| `T4_human_gate` | First-time contributor + risk flags; secrets exposure | human reviewer |

The `complexity_score` is the continuous version of `review_tier`. Same axis, monotonic mapping:

```
severity=low       -> complexity_score=0.20 -> T1_system1_fast
severity=medium    -> complexity_score=0.50 -> T2_system1_verified
severity=high      -> complexity_score=0.75 -> T3_system2_deliberate
severity=critical  -> complexity_score=0.92 -> T3_system2_deliberate (or T4)
```

This is the design claim that makes the dual-purpose contract work. See `docs/DESIGN.md` for the full justification.

## Repository layout

```
.
├── README.md                                  # you are here
├── docs/
│   ├── DESIGN.md                              # dual-purpose design justification
│   ├── ONTOLOGY.md                            # taxonomy layer-by-layer
│   ├── CODEX_ADVERSARIAL_BRIEF.md             # review brief (for codex)
│   └── adversarial_review.md                   # adversarial review (generated)
├── taxonomy/
│   └── ontology.py                            # 4-layer research-grounded taxonomy
├── src/
│   ├── classifier.py                          # v2 ontology-based classifier (canonical)
│   └── classifier_v1.py                       # v1 15-kind enum (kept for backward-compat)
├── evals/
│   ├── validate_v1_on_issues.py               # Rami/issues eval
│   ├── validate_v1_on_bun.py                  # 55 oven-sh/bun PRs (v1)
│   ├── validate_v2_on_bun.py                  # 55 oven-sh/bun PRs (v2 ontology)
│   ├── run_on_real_prs.py                     # Amit's 4 real PRs (v1)
│   └── adversarial_tests.py                   # regression tests from adversarial review
├── examples/
│   ├── ontology_on_real_prs.py                # canonical demo: Amit's 4 PRs through v2
│   └── synthetic_escalation.py                # 8 demo cases (auth, perf, lockfile, etc.)
└── data/
    ├── raw_prs.json                           # Amit's 4 real PRs (title/body/files/diff)
    ├── bun_prs.json                           # 55 oven-sh/bun PRs with diffs
    ├── bun_pr_index.json                      # index of which PRs were sampled
    ├── labeled_dataset.json                   # Rami/multi-label issues dataset
    ├── routing_decisions.json                 # v1 routing output for the 4 PRs
    └── bun_evaluation.json                    # v2 evaluation metrics
```

## Install

```bash
pip install pydantic>=2.0
```

That's it. No venv needed for inference (the system Python has pydantic 2.13+).

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
# -> "high-risk change - escalate to System-2 reasoning model
#     (DeepSeek-R1-Distill-Qwen-32B / QwQ-32B / Qwen3-32B-Thinking)"
```

## Run the demos

```bash
python examples/synthetic_escalation.py    # 8 demo cases
python examples/ontology_on_real_prs.py    # 4 of Amit's real PRs
```

## Run the evaluations

```bash
python evals/validate_v2_on_bun.py         # macro F1 across 55 Bun PRs
python evals/validate_v1_on_bun.py         # same eval, v1 (15-kind) classifier for diff
python evals/run_on_real_prs.py            # v1 routing on Amit's 4 PRs
python evals/adversarial_tests.py          # regression tests from adversarial review
```

## Evaluation results (oven-sh/bun, n=55 PRs)

The ontology classifier was evaluated against a sample of 55 real `oven-sh/bun` PRs (96k stars, Rust+JS runtime — diverse enough to cover most realistic PR types).

Per-intent P/R/F1 on Bun:
| Intent | F1 |
|---|---|
| docs | **0.82** (recall=1.0) |
| build_ci | 0.60 |
| perf | 0.36 |
| feature | 0.36 |
| bug_fix | 0.30 |
| chore / test / refactor | 0.00 (low support, over-fire from title heuristics) |

Routing distribution on the 55 PRs:
- T1_system1_fast: 23 (small docs, typo fixes)
- T2_system1_verified: 17 (refactors, tests, chore)
- T3_system2_deliberate: 15 (high/critical risk)

Severity distribution: 42 low / 7 medium / 5 high / 1 critical — only 6 of 55 PRs warrant a System-2 model, matching [Wang 2025](https://arxiv.org/abs/2602.13377)'s ~10% figure for focused peer review.

## Research grounding

The taxonomy is built on four peer-reviewed / survey-grade sources:

1. **Wang et al. 2025**, [arXiv:2602.13377](https://arxiv.org/abs/2602.13377) — survey of 99 code-review papers, 5 domains × 18 sub-tasks. We adopt the D1–D5 domain literals.
2. **Mäntylä & Lassenius 2009**, [IEEE TSE 35(3):430-448](https://doi.org/10.1109/TSE.2008.71) — DOI 10.1109/TSE.2008.71. Foundational split of functional vs evolvability defects. We adopt "75% of review findings are subjective/evolvability" as the rationale for System-2 routing on subjective work.
3. **Tufano & Bavota 2025**, [arXiv:2503.09510](https://arxiv.org/abs/2503.09510) — systematic literature review of 119 papers on automated code-review tasks. Source for the code-review automation landscape, NOT a PR-intent taxonomy. (Earlier drafts of this README cited a different Tufano paper on self-admitted ChatGPT usage; that paper was about AI tool usage in OSS, not code review. Fixed after adversarial review.)
4. **Li et al. 2025**, [arXiv:2502.17419](https://arxiv.org/abs/2502.17419) — canonical taxonomy of System-1 (fast/heuristic) vs System-2 (slow/chain-of-thought) reasoning. We adopt the T1–T3 tier structure.

### On the dual-purpose design (complexity_score ↔ review_tier)

This project's design collapses **PR complexity** and **required model intelligence** into the same axis. This is a **design choice**, not a result proven by SOTA:

- The Li 2025 survey describes complexity and reasoning-depth in the same conceptual frame, which licenses treating them as a single axis.
- The Wang 2025 survey, Mäntylä-Lassenius 2009, and the System-1/2 literature all support routing PRs to higher-tier reasoning models as complexity/risk increases.
- However, dedicated routing papers — **RouteLLM, R2-Router, InferenceDynamics** — use *multi-axis* decisions (cost, capability profile, budget). Collapsing to one axis is a deliberate simplification; multi-axis routing is future work.

See `docs/adversarial_review.md` for the full critique and the routing-paper analysis.

See `taxonomy/ontology.py` for the full layer-by-layer mapping and `docs/ONTOLOGY.md` for the rationale.

## License

MIT. See `LICENSE`.

## Contributing

Open an issue. PRs welcome but expect adversarial review.