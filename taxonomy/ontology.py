"""
PR Classification Ontology — research-grounded design.

Built by synthesizing four peer-reviewed/survey-grade sources:

  [1] Shaowei Wang et al., "A Survey of Code Review Benchmarks and Evaluation
      Practices in Pre-LLM and LLM Era", arXiv:2602.13377 (2025). Survey of
      99 papers, 2015-2025. Defines 5 high-level code-review domains and
      18 fine-grained sub-tasks.
  [2] Mäntylä & Lassenius, "What Types of Defects Are Really Discovered in
      Code Reviews?", IEEE TSE 35(3):430-448 (2009; accepted Aug 2008,
      published 2009). DOI: 10.1109/TSE.2008.71. Foundational split:
      functional defects vs evolvability defects. The paper reports
      "75% of defects found during the review do not affect the visible
      functionality of the software" - i.e. evolvability.
  [3] Tufano & Bavota, "Automating Code Review: A Systematic Literature
      Review", arXiv:2503.09510 (2025). SLR of 119 papers on automated code
      review tasks (NOT a PR classification taxonomy). Use as the source for
      code-review automation landscape, not PR intent categories.
  [4] Li et al., "From System 1 to System 2: A Survey of Reasoning Large
      Language Models", arXiv:2502.17419 (2025). Defines the
      System-1 (fast/heuristic) vs System-2 (slow/chain-of-thought) model
      families used for routing.

PR-Level ontology (the work the PR performs):
  ----------------------------------------------------------------------
  Layer 0 — Domain (top, from [1]):
    D1 Change Understanding & Analysis
    D2 Peer Review
    D3 Review Assessment & Analysis
    D4 Code Refinement
    D5 Review Prioritization / Selection

  Layer 1 — Intent (the PR's stated purpose):
    I1 Feature:   introduces a new capability
    I2 Bug fix:   corrects incorrect behavior (Mäntylä "functional defect")
    I3 Refactor:  restructures without behavior change (Mäntylä "evolvability")
    I4 Docs:      only documentation changes
    I5 Test:      only test changes
    I6 Build/CI:  only build / CI / workflow changes
    I7 Chore:     housekeeping (deps bump, formatting, chore-labeled work)
    I8 Perf:      performance-targeted change (often cross-cuts I1/I2/I3)
    I9 Revert:    undoes a prior merge
    I10 Security: explicit security patch (often cross-cuts I1/I2)

  Layer 2 — Surface (what the change physically touches, drives routing):
    S1 App code:        *.ts/*.{js,py,rs,go} (runtime behaviour)
    S2 Test code:       tests/, *_test.*, *.spec.*
    S3 Documentation:   docs/**, *.md, *.mdx
    S4 Build/CI:        .github/workflows/, Makefile, Dockerfile
    S5 Config:          .env*, tsconfig, pyproject, lockfiles
    S6 Schema/Migration:prisma/, drizzle/, alembic/, *.sql, migrations/
    S7 Infra/K8s:       *.yaml (k8s), terraform/, helm/
    S8 Generated/Bundle:dist/, build/, *.min.js (often noisy)
    S9 Dep upgrade:     package.json version bump + lockfile
    S10 Public surface: routes, controllers, RPC, public API

  Layer 3 — Risk (orthogonal, drives model tier escalation):
    R1 Touches auth / authz
    R2 Touches secrets / credentials
    R3 Touches DB / migration / schema
    R4 Touches public API routes (breaking surface)
    R5 Touches file serving / path handling (path traversal surface)
    R6 Linked to a priority-P0 / security issue
    R7 First-time contributor
    R8 Change size > 500 LOC (heuristic threshold)
    R9 Touches crypto / TLS / FFI boundary
    R10 Touches concurrency primitives (locks, atomics)

  Layer 4 — Severity (orthogonal, drives reviewer urgency):
    SEV1 critical   (any R1|R2|R3|R4|R5|R6 OR cross-cutting I8/I10)
    SEV2 high       (any of R7|R8 OR R9 OR R10)
    SEV3 medium     (no risk flags, but change size > 200 OR perf work)
    SEV4 low        (no risk flags, small change)

The final `pr_classification` is the cross-product:
  - pr_intent    (Layer 1)
  - pr_surfaces  (Layer 2, multi-select)
  - pr_risks     (Layer 3, multi-select)
  - pr_severity  (Layer 4)
  - review_domain(D1-D5, which of [1]'s 5 domains the PR belongs to)


Routing decision (the System 1 / System 2 axis, [4]):
  ----------------------------------------------------------------------
  Inspired by RouteLLM, R2-Router, InferenceDynamics, and litellm's
  auto-router. The classifier emits a `review_tier`:

  T0 SKIP — no review needed (lockfile-only digest bump, typo-only docs).
            Per [1], ~10% of PRs are "low impact changes that are
            deferred" — these match T0.

  T1 SYSTEM_1_FAST — local small model, no chain-of-thought.
            Open candidates (per [4] §3.2 and HuggingFace leaderboards):
              - Qwen2.5-Coder-3B-Instruct   (no-thinking, ~3GB VRAM)
              - Qwen2.5-Coder-7B-Instruct   (no-thinking, ~7GB VRAM)
              - DeepSeek-Coder-V2-Lite-Instruct (16B MoE, ~9GB active)
              - Llama-3.1-8B-Instruct       (no-thinking)
              - Phi-3.5-mini-instruct       (3.8B)
              - Qwen2.5-7B-Instruct         (no-thinking baseline)
            Use case: docs, trivial bug fixes, rename-only refactors,
            tests-only additions, chore, small features.

  T2 SYSTEM_1_VERIFIED — local small model + deterministic checks.
            Same System-1 model as T1, but with the must_check checklist
            enforced programmatically (lint, type check, security linter).
            Use case: medium-risk features, refactors without risk flags.

  T3 SYSTEM_2_DELIBERATE — frontier reasoning model, chain-of-thought.
            Open-weight candidates (per [4] §4.3 and HuggingFace):
              - DeepSeek-R1-Distill-Qwen-32B   (SOTA open-weight reasoning)
              - DeepSeek-R1-Distill-Llama-70B  (SOTA, fits 2xA100 80GB)
              - QwQ-32B-Preview               (open-weight, 32B reasoning)
              - Qwen3-32B-Thinking            (newest, hybrid thinking mode)
              - Open-Reasoning-Zen-32B         (open community model)
              - Llama-3.3-70B-Instruct + self-consistency
            Use case: any SEV1/SEV2 risk flag, schema migration, security
            surfaces, perf work, breaking API changes.

  T4 HUMAN_GATE — must go to a human, model cannot sign off.
            Use case: first-time contributor with risk flags, secrets
            exposure risk, author and reviewer overlap, controversial
            architectural change. Per [2], 75% of review findings are
            evolvability (subjective) — these benefit most from humans.


Reference mapping back to SOTA:
  ----------------------------------------------------------------------
  - "Code refactoring" sub-task in [1] -> our pr_intent=I3, pr_surface=S1
  - "Defect detection" sub-task in [1] -> pr_intent=I2 + review_domain=D2
  - "Review assessment" in [1]        -> review_domain=D3
  - "Prioritization/Selection" in [1] -> review_domain=D5 (T0 = skip)
  - "Functional defect" in [2]        -> pr_intent=I2
  - "Evolvability defect" in [2]      -> pr_intent=I3 (refactor-style)
  - Tufano "Code generation"          -> pr_intent=I1 (feature)
  - Tufano "Testing"                  -> pr_intent=I5
  - Tufano "Documentation"            -> pr_intent=I4
  - [4] System-1 thinking             -> T1, T2
  - [4] System-2 thinking             -> T3
"""