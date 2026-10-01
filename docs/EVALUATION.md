# Evaluation Results — System-one-pr-classifier

Last updated: 2026-09-30

## Headline numbers

| Eval | PRs | Macro F1 | 95% CI | Notes |
|------|-----|----------|--------|-------|
| **SWE-PRBench** | 350 (65 repos, 5 languages) | **0.57** | [0.48, 0.71] | Cross-repo + multi-language benchmark |
| **oven-sh/bun** | 55 (1 repo, 1 language) | **0.53** | n/a | Single-repo baseline |
| diff_trumps_title invariant | 7 hand-crafted | 7/7 PASS | n/a | Surface-trumps-title property |
| adversarial_tests (bun:test+chore) | 7 hand-crafted | 7/7 PASS | n/a | Regression suite from adversarial review |

## SWE-PRBench — per-intent breakdown

```
Intent                 P     R    F1   TP   FP   FN  sup
----------------------------------------------------------------
bug_fix             0.99  0.78  0.87   92    1   26  118
feature             0.98  0.60  0.75  129    3   85  214
refactor            0.75  0.33  0.46    3    1    6    9
perf                0.50  0.12  0.20    1    1    7    8
security_patch      1.00  1.00  1.00    1    0    0    1

Macro F1 (support>=3): 0.570
Bootstrap 95% CI: [0.483, 0.713]
```

## SWE-PRBench — per-language breakdown

```
Language    n    Macro F1
Python    242     0.65  (best)
Go         35     0.59
TypeScript 21     0.59
JavaScript 37     0.54
Java       15     0.52  (worst)
```

## SWE-PRBench — per-difficulty breakdown

```
Difficulty            n   Macro F1
Type1_Direct         232    0.62
Type2_Contextual      75    0.62
Type3_Latent          43    0.64
```

## Top misclassifications (current state)

```
gold       pred            count
feature -> mixed_or_unclear    56    <- diff-only title can't tell
bug_fix  -> mixed_or_unclear     7    <- short technical titles
feature -> test                 13    <- SQL dialect additions (fixtures)
feature -> build_ci              6    <- CI-heavy PRs mislabeled by gold
bug_fix  -> test                 7    <- test-heavy bug fixes
bug_fix  -> build_ci             4
bug_fix  -> docs                 3    <- "(docs): Fix Vale warnings"
feature -> docs                 3    <- "(docs):" prefix wins over diff
```

## What changed since first eval

Run `python3 evals/validate_swe_prbench.py` to see the current full report.

## How to reproduce

```bash
# 1. Install deps
python3 -m venv .venv
source .venv/bin/activate
pip install datasets pydantic

# 2. Download SWE-PRBench (350 PRs)
python3 evals/build_swe_prbench.py

# 3. Run the eval
python3 evals/validate_swe_prbench.py

# 4. Get a final report across all evals
python3 evals/final_report.py
```