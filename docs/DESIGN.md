# Design Notes — `System-one-pr-classifier`

## The dual-purpose design

The core claim of this project is that **a single `PRClassification` object
answers two questions simultaneously**:

1. **How complex is this PR?**
2. **Which model should review it?**

Both questions map to the same axis: *how much deliberate reasoning does this
change require?*  A typo fix needs no reasoning — System-1, fast.  A crypto
boundary change needs deep reasoning — System-2, deliberate.

### Research support for the dual-purpose claim

The collapse is **partially supported** by the SOTA literature, not proven:

- **Li et al. 2025** ("From System 1 to System 2", [arXiv:2502.17419](https://arxiv.org/abs/2502.17419)) describes System-1 vs and in the **same conceptual language** as complexity/heuristic-vs-deliberate. This licenses treating them as a single axis.
- **Mäntylä & Lassenius 2009** ([IEEE TSE 35(3):430-448](https://doi.org/10.1109/TSE.2008.71)) — 75% of review findings are evolvability (subjective). Subjective work *requires* deliberate review, regardless of diff size. This further supports routing complexity to reasoning tier.
- **Wang et al. 2025** ([arXiv:2602.13377](https://arxiv.org/abs/2602.13377)) — ~10% of PRs warrant focused peer review (D2 domain). A single axis (high-complexity → System-2) is sufficient to identify those PRs.

### What prior work does NOT support

Dedicated **routing papers** — RouteLLM, R2-Router, InferenceDynamics — use
**multi-axis decisions**, not single-axis:

| Paper | Axes used |
|---|---|
| RouteLLM (arXiv:2406.18665) | Cost vs quality from preference data |
| R2-Router (arXiv:2602.02823) | Model selection + output budget |
| InferenceDynamics (arXiv:2505.16303) | Capability profile × knowledge profile × cost |

Collapsing to a single axis is **a deliberate simplification**, not a proven
result. We document this honestly in `docs/adversarial_review.md`. Multi-axis
routing (cost + latency + capability profile) is future work.

## The ontology layers

The taxonomy has four orthogonal layers, each driving both complexity and routing:

```
Layer 0: Review domain (D1-D5)        <- which reviewer profile
Layer 1: PR intent (11 literals)      <- what the PR does
Layer 2: Surfaces touched (10 m-s)     <- physical scope
Layer 3: Risks (11 m-s)               <- cross-cutting flags
Layer 4: Severity (4 literals)        <- derived
Layer 5: complexity_score (0-1)       <- dual-purpose summary
```

The flow:

```
title + body + file_paths + diff + branch + add/del counts
    │
    ▼
[1] surface detection (path regexes + diff patterns)
    │
    ▼
[2] intent detection (surface-conditional rules + title regex)
    │
    ▼
[3] risk detection (cross-cutting patterns, e.g. auth/, .env*)
    │
    ▼
[4] severity = derive(risks, intent)
    │
    ▼
[5] complexity_score = severity_to_complexity(severity)
    │
    ▼
[6] review_tier = derive(severity, surfaces, risks, intent, size, branch)
    │
    ▼
[7] review_domain = derive(intent, severity, surfaces)
    │
    ▼
[8] must_check = union(per_risk_checklist, per_intent_checklist)
    │
    ▼
PRClassification (pydantic)
```

## Routing decision (the System-1 ↔ System-2 axis)

| Tier | Trigger | Open-weight candidate |
|---|---|---|
| `T0_skip` | Lockfile-only digest bump; experimental branch | none |
| `T1_system1_fast` | Docs only, low risk | Qwen2.5-Coder-3B-Instruct · Phi-3.5-mini |
| `T2_system1_verified` | Refactor, test, build_ci, chore (low risk) | + lint/typecheck |
| `T3_system2_deliberate` | Any risk flag; security patch; breaking change; perf | DeepSeek-R1-Distill-Qwen-32B · QwQ-32B · Qwen3-32B-Thinking |
| `T4_human_gate` | First-time contributor + risk flag; secrets exposure | human reviewer |

## Schema guarantees

Pydantic v2 gives us:

- **Exhaustive enums** — every Literal can be `match`ed, the type checker will tell you if you miss a case.
- **Constrained floats** — `complexity_score` and `confidence` are bounded `0 ≤ x ≤ 1`.
- **Free-list multi-select** — `pr_surfaces`, `pr_risks`, `must_check` are lists, deduplicated at the classifier return site.
- **JSON Schema export** — `PRClassification.model_json_schema()` produces a spec that can be codegen'd into TypeScript, JSON Schema for a downstream consumer, OpenAPI, etc.

## What this design is NOT

- **Not a learned classifier.** All rules are explicit. There's no model behind the scenes. Pros: explainable, deterministic, fast, no training data needed. Cons: limited to patterns we hand-wrote; corpus-specific terms won't be detected (e.g. "bun:test" PRs).
- **Not an LLM call.** The classifier emits the routing decision *without* calling a model. The model comes later, after the routing decision.
- **Not a replacement for review.** `T0_skip` is a strong claim that human review is unnecessary. We use it only for genuinely trivial changes (lockfile-only digest bumps, experimental branches).
- **Not a regression test for review decisions.** The classifier says *which model* should review; it doesn't say *what the review should find*.

## Future work

1. **Multi-axis routing** — add `cost_budget`, `latency_target`, `capability_profile` dimensions.
2. **Learned routing** — train a small DistilBERT on labeled PRs to replace the title heuristics.
3. **Repo-specific calibration** — the Bun corpus suggests different default tiers for runtime repos vs web repos vs ML repos.
4. **Tighten `public_api` regex** — current regex too loose (matches `os-signals.mdx`).
5. **Plumb `author_association`** — currently `first_time_contributor` is never set; need to add it to the function signature.