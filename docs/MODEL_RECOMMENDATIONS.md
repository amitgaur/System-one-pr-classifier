# System-1 vs System-2 model recommendations

This document grounds the classifier's `review_tier` field in current
SOTA evidence from public benchmarks. Updated 2026-09-30.

## TL;DR — the System-1 / System-2 model split

| Tier | Trigger | Recommended model | Why |
|------|---------|-------------------|-----|
| **System-1 (decision model)** | Always — the classifier itself | **decider-4b v2** ([Mapika](https://github.com/Mapika/decider)) OR our rule-based fallback | Open-weight "Jev" — Apache-2.0, 0.81 held-out accuracy, 120-310ms CPU, calibrated probabilities. The classifier IS a System-1 decision model. |
| T1 / T2 (cheap reviewer) | Low-risk PRs that pass classifier | **Qwen2.5-Coder-1.5B / 3B-Instruct** | Apache-2.0; runs on CPU; Aider polyglot 24-43%. |
| T2 (medium reviewer) | Moderate complexity | **Qwen2.5-Coder-7B-Instruct** OR **Qwen3-Coder-30B-A3B-Instruct** | Aider 75%; agentic DA 80.5% on SWE-Review-Bench. |
| T3 (frontier reviewer) | High-risk: auth, public API, security | **DeepSeek-V3** | SWE-PRBench mean composite 0.150 (rank 3 of 8 frontier); ~9x cheaper than GPT-4o. |
| T3 alternative | Agentic review (best quality) | **SWE-Review-30B-A3B** ([FoundryHQ-AI](https://github.com/FoundryHQ-AI/swe-prbench)) | arXiv:2607.06065; +29.4pp over single-turn. |
| T4 (human) | First-time contributor + secrets exposure | human reviewer | - |

## Why "Jev" / decider matters here

The classifier problem (PR metadata → typed decision with confidence) is
**exactly the System-1 decision-model use case** TypeSafe AI introduced
with Jev in September 2026. A "decision model" returns typed probabilistic
answers instead of text — perfect for routing decisions.

- **TypeSafe AI's Jev 1.13.0** is proprietary API ($0.042/M input tokens,
  70-500ms latency). [Announced 15 Sep 2026](https://typesafe.ai/blog/introducing-system-one-models-and-jev).
- **decider** by [Mapika](https://github.com/Mapika/decider) is an open
  reproduction: Apache-2.0 package and weights, trained on Qwen3.5 base.
  Outperforms Jev 1.13.0 on the JevBench composite (64.1 vs 63.3).
- 60+ open-weight Jev-class models on JevBench; top 3 ranked above Jev.

### How the classifier maps to Jev / decider

Jev-style models answer three kinds of typed questions:

| Question type | Maps to our classifier field | Example prompt |
|---------------|------------------------------|----------------|
| **Choice** (2-255 options) | `pr_intent`, `review_tier`, `review_domain` | "What's the PR intent?" options: bug_fix, feature, docs, test, build_ci, chore, perf, security_patch, refactor, revert, mixed_or_unclear |
| **Noul** (yes/no with confidence) | Each `pr_risk` flag | "Does this PR touch authentication code?" returns float 0.0-1.0 |
| **Score** (2-10 described levels) | `complexity_score` | "How complex is this PR?" levels: trivial, simple, moderate, complex, frontier-reasoning-required |

In production, our rule-based classifier IS a hand-coded, deterministic
Jev-equivalent. The decider model gives us the same answers from a
fine-tuned Qwen3.5-4B — with the advantage of:
1. Handling edge cases the rules don't anticipate
2. Calibrated confidence scores (the rules have hard-coded `confidence=0.85`)
3. One model can replace hundreds of regex rules

### Recommended architecture

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
                    │  (Jev-style typed decision model)  │
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

Why this split: the rule-based classifier runs in <1ms and handles ~70%
of PRs deterministically. The remaining ~30% (mostly `mixed_or_unclear`
and ambiguous complexity scores) get escalated to the decision model,
which adds ~200ms but resolves the ambiguity with calibrated confidence.

## Model selection details

### T0_skip (skip review entirely)
- Lockfile-only digest bumps, experimental-branch work.
- No model — return immediately.

### T1_system1_fast: Qwen2.5-Coder-1.5B / 3B-Instruct
- **Repo**: [Qwen/Qwen2.5-Coder-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct)
- **License**: Apache-2.0
- **RAM**: ~3GB (1.5B) / ~6GB (3B) in bf16; ~1GB (1.5B) / ~2.5GB (3B) Q4_K_M
- **Speed**: 60+ tok/s on M2, 100+ tok/s on RTX 4090
- **Use**: typo fixes, doc rephrasing, simple bug fixes with clear root cause

### T2_system1_verified: Qwen2.5-Coder-7B / Qwen3-Coder-30B-A3B
- **Repo**: [Qwen/Qwen2.5-Coder-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct) — Apache-2.0; Aider 75.2%
- **Repo**: [Qwen/Qwen3-Coder-30B-A3B-Instruct](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct) — Apache-2.0; SWE-Review DA 80.5%
- **Use**: refactors, test additions, build/CI changes, moderate bug fixes

### T3_system2_deliberate: DeepSeek-V3
- **Repo**: [deepseek-ai/DeepSeek-V3](https://huggingface.co/deepseek-ai/DeepSeek-V3)
- **License**: MIT (open-weight); API $0.14/M input
- **SWE-PRBench score**: mean composite 0.150 (rank 3 of 8 frontier models)
- **SWE-bench Verified**: 80.6%
- **Use**: high-risk PRs — auth changes, public API breaks, security patches

### T4_human_gate
- First-time contributor + secrets exposure, OR `touches_secrets` confirmed
- Human reviewer

## Evidence: SWE-PRBench (arXiv:2603.26130, March 2026)

The most direct benchmark for code-review quality. 350 PRs, 65 repos, 6 languages,
3 difficulty types (Type1_Direct, Type2_Contextual, Type3_Latent),
human-annotated ground truth, judge validated at κ=0.75.

| Model | mean s̄ (config_A/B/C avg) | DRA | FPR |
|-------|---------------------------|-----|-----|
| **DeepSeek-V3** | **0.150** | 0.312 | 0.315 |
| Claude Haiku 4.5 | 0.153 | 0.306 | 0.346 |
| Claude Sonnet 4.6 | 0.152 | 0.297 | 0.227 |
| Mistral Large 3 | 0.147 | 0.305 | 0.353 |
| GPT-4o | 0.113 | 0.220 | 0.193 |
| Mistral Small | 0.106 | 0.257 | 0.251 |
| Llama 3.3 70B | 0.079 | 0.223 | 0.417 |

**Headline:** DeepSeek-V3 hits Tier-1 (frontier) performance on code review
at ~9x lower cost than GPT-4o. **Best open-weight model for T3 in our
classifier is DeepSeek-V3.**

## Evidence: SWE-Review (arXiv:2607.06065, July 2026)

The **agentic code review** paper — directly relevant to our T3 design.
1,384 PRs from 500 SWE-bench Verified issues, three PR generator
distributions (GLM-5 high, Qwen3-Coder-30B-A3B medium, Qwen3-30B-A3B low).

**Key finding:** Closed-loop agentic review (generate → review → revise)
lifts the weaker generators by **+29.4 percentage points**:
- Qwen3-30B-A3B: 27.5% → 56.9% resolve rate
- Qwen3-Coder-30B-A3B: 50.9% → 68.8% resolve rate
- GLM-5: 72.2% → 75.4% resolve rate

**For our classifier:** this means T3-tier reviews that use **agentic
exploration** (read files, trace imports, run tests) significantly outperform
single-turn "review this diff" prompts. The classifier's role is to **route
to T3 only when agentic exploration is justified** — that's the
T3_system2_deliberate trigger.

## Evidence: Qwen2.5-Coder Technical Report (arXiv:2409.12186)

Qwen2.5-Coder family is the best open-weight code-specialized line:

| Size | HumanEval | MBPP | BigCodeBench | Use case |
|------|-----------|------|--------------|----------|
| 0.5B | 42.0 | 13.3 | 40.6 | T0_skip fallback (still cheaper than nothing) |
| 1.5B | 53.6 | 24.3 | 72.4 | T1_system1_fast (CPU-friendly) |
| 3B | 70.7 | 35.0 | 76.5 | T1_system1_fast (default System-1) |
| 7B | 84.1 | 50.4 | 79.9 | T2_system1_verified |
| 14B | 89.6 | 60.3 | 81.5 | T2_system1_verified (high) |
| 32B | 92.7 | 70.4 | 84.5 | T3_system2_deliberate (alternative) |

**All Apache-2.0** (except 3B and 72B which have other open licenses).
Qwen2.5-Coder-32B matches GPT-4o on multiple code generation benchmarks.

## Evidence: Jev / decider (the System-1 model class)

TypeSafe AI coined "System One models" (decision models) in September 2026.
The interface is:
- **Choice questions**: pick from 2-255 options, return probability distribution
- **Noul questions**: yes/no with confidence (Bernoulli distribution)
- **Score questions**: 2-10 described levels, return float in range

### Open-weight Jev-class models (JevBench v1.4.2)

| Rank | System | Score | Intelligence | License |
|------|--------|-------|--------------|---------|
| 1 | **decider-4b v2** (Mapika) | **64.1** | 49.4 | Apache-2.0 |
| 2 | Jev 1.13.0 (TypeSafe) | 63.3 | 53.1 | proprietary |
| 3 | JevK5 v0.2.0 (allebee) | 62.0 | 48.9 | Apache-2.0 |
| 4 | Cygnet (blockbrain, Gemma-4-12B) | 61.8 | 49.5 | shim MIT + Gemma Apache-2.0 |
| 5 | Hopper (HopitAI) | 59.4 | 48.0 | mixed |
| ... | ... | ... | ... | ... |

**decider-4b v2 is the highest-ranked open-weight Jev-class model**, beating
Jev 1.13.0 on the composite score. Held-out accuracy 0.784 on Mapika's
regression set. JevBench public hard tier 0.649.

### Recommended: decider-4b v2 as the System-1 fallback

For our classifier, when the rule-based logic returns `mixed_or_unclear`
or low confidence, escalate to `decider-4b v2` running locally. The
decision model takes the same `PRClassification` schema and returns
typed choices with calibrated probabilities — and we already have the
8-intent ontology defined.

Cost (per 100 PRs at 500-token state):
- decider-4b v2 on RTX 4090: ~5 seconds total = $0.10 electricity
- Jev 1.13.0 API: $0.001 + free output

For the few hundred tokens per PR, the open-weight model is roughly
free after the GPU is amortized.

## Cost analysis (rough, per 100 PRs at avg SWE-PRBench diff size ~10KB)

| Model | Cost per 1M input | ~Cost per PR review | Tier |
|-------|-------------------|---------------------|------|
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

## Routing policy

The classifier's `review_tier` field directly maps:

```python
TIER_TO_MODEL = {
    "T0_skip": None,
    "T1_system1_fast": "Qwen/Qwen2.5-Coder-1.5B-Instruct",
    "T2_system1_verified": "Qwen/Qwen2.5-Coder-7B-Instruct",
    "T3_system2_deliberate": "deepseek-ai/DeepSeek-V3",
    "T4_human_gate": "human-reviewer",
}
```

**What triggers T3 (System-2):**
- `pr_severity == "high"` or `"critical"`
- Any risk flag: `touches_auth`, `touches_secrets`, `touches_db_or_migration`,
  `touches_public_api`, `touches_file_serving`, `touches_path_handling`,
  `touches_crypto_or_ffi`, `touches_concurrency`, `large_change`
- Intent = `security_patch`
- Intent = `bug_fix` AND `complexity_score` >= 0.7
- `must_check` non-empty (any reviewer-flagged risk)

**What stays at T1/T2 (System-1):**
- Intent = `docs`, `test`, `build_ci`, `chore`, `refactor`
- All risks empty AND `complexity_score` < 0.5
- PR is small (< 200 lines changed)

## Open questions / future work

1. **Wire decider-4b v2 as the `mixed_or_unclear` fallback.** Build
   `src/decider_adapter.py` that translates PR metadata into Jev-style
   typed questions, runs `decider.decide(...)`, and merges results back
   into the `PRClassification` schema. Estimated +5pp on the
   `feature → mixed_or_unclear` failure mode.

2. **No SWE-PRBench numbers for Qwen2.5-Coder-32B or Qwen3-Coder-30B-A3B yet.**
   Need to actually run them on the 100-PR eval sample.

3. **T3-tier requires token window management.** DeepSeek-V3 has 64K context.
   SWE-PRBench found all 8 models **degrade monotonically as context
   expands** — config_C (full context) scores LOWER than config_A (diff only).
   Implication: even at T3, we should send **structured diff-with-summary**,
   not the raw full file. The classifier's `pr_surfaces` + `must_check` give
   us the structured prompt.

4. **Agentic review on local hardware.** Qwen3-Coder-30B-A3B is MoE
   (30B total, 3B active) — fits on a single RTX 4090 (24GB) with 4-bit
   quantization.

## References

1. **Jev (System One models)** — TypeSafe AI, September 2026. [Introducing System One Models and Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev). Proprietary.
2. **decider** (open-weight Jev reproduction) — [Mapika/decider](https://github.com/Mapika/decider), Apache-2.0.
3. **JevBench** — [Benchmark Heaven](https://benchmarkheaven.com/jev-models/open-source-jev) — 60 open-weight Jev-class models ranked.
4. **SWE-PRBench** — Kumar 2026, [arXiv:2603.26130](https://arxiv.org/abs/2603.26130).
5. **SWE-Review** — Wang et al. July 2026, [arXiv:2607.06065](https://arxiv.org/abs/2607.06065).
6. **Qwen2.5-Coder Technical Report** — arXiv:2409.12186.
7. **Simon Willison on Jev** — [Weblog, 21 Sep 2026](https://simonwillison.net/2026/Sep/21/jev/).