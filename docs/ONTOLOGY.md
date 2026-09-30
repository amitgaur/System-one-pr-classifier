# Ontology — Layer-by-Layer

The PR classification ontology has four layers that map directly to the
`PRClassification` schema fields. Each layer is grounded in a specific
SOTA source.

## Layer 0 — Review domain (5 literals, [Wang et al. 2025](https://arxiv.org/abs/2602.13377))

Maps to one of 5 code-review domains defined by Wang et al. in their
survey of 99 papers on automated code review.

| Literal | When |
|---|---|
| `D1_change_understanding` | Default: PR needs to be understood before review |
| `D2_peer_review` | Risk flags present; needs focused senior review |
| `D3_review_assessment` | Pure test/CI changes — assess quality of test coverage |
| `D4_code_refinement` | Refactor — refinement is the only goal |
| `D5_prioritization_selection` | Low-priority changes; can be deferred |

Wang et al. found ~10% of PRs warrant `D2_peer_review`. Our Bun corpus
hit this number (5/55 = 9%).

## Layer 1 — PR intent (11 literals)

What the PR *does*. Ground-truth derived from multiple sources:

| Literal | Source | When |
|---|---|---|
| `feature` | General | Introduces a new capability |
| `bug_fix` | [Mäntylä & Lassenius 2009](https://doi.org/10.1109/TSE.2008.71) (functional defect) | Corrects incorrect behavior |
| `refactor` | [Mäntylä & Lassenius 2009](https://doi.org/10.1109/TSE.2008.71) (evolvability) | Restructures without behavior change |
| `docs` | [Wang et al. 2025](https://arxiv.org/abs/2602.13377) | Documentation only |
| `test` | General | Test only |
| `build_ci` | [Tufano & Bavota 2025](https://arxiv.org/abs/2503.09510) | Build/CI only |
| `chore` | General | Housekeeping (deps, format) |
| `perf` | General | Performance-targeted |
| `revert` | General | Undoes prior merge |
| `security_patch` | General | Explicit security fix |
| `mixed_or_unclear` | Fallback | Doesn't fit cleanly |

**Honest limitation:** `test_only` and `chore` PRs in the Bun eval are
under-detected (F1=0) because the title heuristics are too narrow. See
`docs/adversarial_review.md` for the recommended fixes.

## Layer 2 — Surfaces touched (10 literals, multi-select)

What the change physically touches. Determined by file-path regexes +
diff patterns.

| Literal | Path patterns |
|---|---|
| `app_code` | `.ts`, `.py`, `.rs`, `.go`, `.java` etc. |
| `test_code` | `tests/`, `*_test.*`, `*.spec.*` |
| `documentation` | `docs/`, `README.md`, `*.mdx?` |
| `build_ci` | `.github/workflows/`, `Dockerfile`, `Makefile` |
| `config` | `.env*`, `tsconfig`, `pyproject.toml`, lockfiles |
| `schema_migration` | `prisma/`, `drizzle/`, `alembic/`, `migrations/`, `*.sql` |
| `infra_k8s` | `kustomization.yaml`, `values.yaml`, helm charts |
| `generated_bundle` | `dist/`, `build/`, `node_modules/`, `*.min.js` |
| `dep_upgrade` | `package-lock.json`, `yarn.lock`, `Cargo.lock` etc. |
| `public_api_routes` | `/auth/`, `/login/`, `/api/v*/`, `app.use(express.static)`, etc. |

## Layer 3 — Risk flags (11 literals, multi-select)

Cross-cutting flags that drive severity escalation.

| Literal | Trigger |
|---|---|
| `touches_auth` | `/auth/`, `/login/`, `/jwt/`, etc. |
| `touches_secrets` | `.env*`, `.pem`, `*.key` |
| `touches_db_or_migration` | `prisma/`, `alembic/`, `*.sql` |
| `touches_public_api` | `/api/`, `/routes/`, `@app.route` |
| `touches_file_serving` | `send_file`, `express.static` |
| `touches_path_handling` | `os.path.join`, `pathlib.Pure` |
| `touches_crypto_or_ffi` | `/crypto/`, `/tls/`, `node:crypto`, `ffi.*` |
| `touches_concurrency` | `threading.Lock`, `Mutex`, `Arc<` |
| `linked_priority_p0` | (future) PR body mentions linked P0 issue |
| `first_time_contributor` | (future) GitHub `author_association == FIRST_TIME_CONTRIBUTOR` |
| `large_change` | `(additions + deletions) > 500` |

**Honest limitation:** `linked_priority_p0` and `first_time_contributor`
are in the Literal enum but never set in the current `_detect_risks`. To
enable them, plumb the GitHub PR metadata through `function signature.

## Layer 4 — Severity (4 literals, derived)

Derived from Layer 3 (risks) + Layer 1 (intent):

```
critical = (high_risks >= 2) OR (intent == security_patch)
high     = (high_risks >= 1)
medium   = (medium_risks >= 2) OR (intent == perf)
low      = (no risk flags) AND (intent not in security_patch/perf)
```

Where `high_risks = {touches_auth, touches_secrets, touches_db_or_migration,
touches_public_api, touches_file_serving, touches_path_handling}` and
`medium_risks = {touches_crypto_or_ffi, touches_concurrency, large_change}`.

## Layer 5 — Complexity score (0.0–1.0 float, dual-purpose)

Continuous version of severity. Same axis as `review_tier`, monotonic mapping.

```
low       -> 0.20  -> T1_system1_fast
medium    -> 0.50  -> T2_system1_verified
high      -> 0.75  -> T3_system2_deliberate
critical  -> 0.92  -> T3_system2_deliberate (or T4)
```

This is the **dual-purpose field** that answers both
"how complex is this PR?" and "which model should review it?".

See `docs/DESIGN.md` for the full design justification, including the
honest note on what prior work does and doesn't support.