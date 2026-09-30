# Adversarial Schema Review — Codex Brief

## Goal
You are an adversarial reviewer. Your job is to **find weaknesses** in the PR
classification schema and routing logic, not to validate them.

## Inputs to read (in order)
1. `src/classifier.py` — pydantic schema, enum literals, classifier function
2. `taxonomy/ontology.py` — research-grounded taxonomy doc
3. `examples/synthetic_escalation.py` — current "happy path" demos
4. `data/raw_prs.json` — 4 real PRs that should be classified correctly
5. `data/bun_prs.json` — 55 Bun PRs used for the eval

## Attack surfaces (find weaknesses in each)

### 1. Schema-level attacks
- Try to construct `PRClassification` instances with values that violate the
  Literal types (e.g. `pr_intent="unknown"`, `pr_severity="super-critical"`,
  `review_tier="T5_blast"`).
- Try to bypass the `ge=0.0, le=1.0` constraint on `complexity_score` and
  `confidence` (negative, > 1.0, NaN, infinity).
- Try to insert non-list values into `pr_surfaces`/`pr_risks`/`must_check`,
  or inject duplicate enum values that should be deduplicated.
- Try empty strings, empty lists, very long strings (>100KB).
- Look for type-safety holes in `signals` — it's `dict[str, list[str]]`,
  can you inject other types?

### 2. Routing-logic attacks (the function `classify_pr_ontology`)
- Find a PR input where the classifier chooses a tier that contradicts the
  risk flags (e.g. high-risk surfaces but tier=T1_system1_fast).
- Find a PR where the `must_check` list is empty despite risk flags being
  present.
- Find a PR where `complexity_score` doesn't match the implied severity
  (use the `severity_to_complexity` mapping: low=0.20, medium=0.50,
  high=0.75, critical=0.92).
- Find a PR that should clearly be `T0_skip` but is routed elsewhere
  (e.g. lockfile-only without the `chore` intent).
- Find a PR that has a `T4_human_gate` signal (e.g. first-time contributor)
  but doesn't actually flag the risk.

### 3. Ontology-coverage attacks
- Find a real PR in `data/bun_prs.json` whose true kind doesn't map cleanly
  to any of the 11 `Intent` values.
- Find a real PR whose risk doesn't map cleanly to any of the 11 `Risk`
  values.
- Find a domain where the 5 `Domain` literals (Wang 2025) are too coarse —
  e.g. a PR that genuinely needs "peer review" (D2) but the classifier
  routes it to "change understanding" (D1).

### 4. False-positive attacks on the routing logic
- Construct a PR that LOOKS risky (matches `touches_secrets` because of a
  string like `key=value` in a markdown doc) but is genuinely trivial.
- Construct a PR that LOOKS like a lockfile-only digest bump but actually
  contains code changes.

### 5. False-negative attacks (the dangerous ones — risks the classifier MISSES)
- A PR with no obvious risk-flag file paths but the body describes an
  auth-breaking schema change in plain prose.
- A PR that touches both auth AND tests in a way that gets classified as
  `test_only` instead of `auth_or_security`.
- A PR from a first-time contributor that's a 5-line typo fix — does the
  classifier correctly *not* flag this as T4_human_gate?
- A PR with a branch name like `hotfix/payment-security` — does the
  experimental-branch regex incorrectly T0-skip it?

## Output format

Produce a single markdown file: `docs/adversarial_review.md` with sections:

### Schema Findings
For each: file:line, the issue, the attack input, the expected rejection,
the actual behavior, severity (P0 critical / P1 important / P2 nice-to-fix).

### Routing-Logic Findings
Same format, but focus on the function `classify_pr_ontology` and the
helpers it calls (`_severity`, `_tier`, `_domain`).

### Ontology-Coverage Findings
For each: what real PR is misclassified, what's missing from the taxonomy,
proposed fix (Literal extension? new flag? cross-cutting rule?).

### Recommendations
Top 3-5 prioritized changes. Be specific — give file paths and exact
patch sketches.

## How to actually run the attacks
You have shell access and python access. Use them:

```bash
# Schema-level: try to construct invalid PRClassification
python3 -c "
from src.classifier import PRClassification
# Try to break it
try:
    p = PRClassification(pr_intent='unknown', complexity_score=2.0)
    print('FAIL: accepted invalid')
except Exception as e:
    print(f'OK rejected: {type(e).__name__}')
"

# Routing-level: run classifier on adversarial inputs
python3 -c "
import sys; sys.path.insert(0, '.')
from src.classifier import classify_pr_ontology
# Adversarial case: looks like auth, actually trivial
r = classify_pr_ontology(
    title='docs: api key naming convention',
    body='',
    file_paths=['docs/api-keys.md'],
    diff='',
)
print(r.review_tier, r.pr_risks)
"
```

For each attack, actually run it, capture the output, and report what
you found.

## What NOT to do
- Don't write any code changes to the schema. This is review-only.
- Don't make up findings without running the attack. Every finding
  needs a reproducer (the python command + the observed output).
- Don't be polite. The point of adversarial review is to find things
  that are wrong.

When done, print the path to `docs/adversarial_review.md` as your final
output so I can read it.