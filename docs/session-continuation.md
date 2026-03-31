# Session Continuation
**Updated:** 2026-03-31 00:10 GMT | **For:** Next session

---

## Completed this session

### REVUE-81 — Pipeline respects `agents_allowed` from license ✅ PR #16 merged
- `src/revue/core/pipeline.py`: Extract `agents_allowed` from license, log active agents, gate `agents_used` tracking
- `src/revue/tests/core/test_pipeline.py`: 3 new unit tests (free tier, pro tier, log output)
- `.bitbucket/pull_request_template.md`: New PR template for consistent, context-rich PRs
- `scripts/create-pr.sh`: Automated PR creation script (env-var driven, no hardcoded credentials)
- `docs/PR_TEMPLATE_GUIDE.md`: Team reference for PR process

### REVUE-82 — Wire full orchestration engine for paid tiers ✅ PR #17 merged
- `src/revue/core/license_validator.py`: `REVUE_TIER_OVERRIDE` env var for non-prod testing
  - Security hardened: only active in source builds (`sys.frozen` check) + `APP_ENV=development|staging`
- `src/revue/core/pipeline.py`: Full rewrite with tier-branching
  - `_run_simplified()` — free tier, single-pass `client.complete()` loop
  - `_run_orchestration()` — paid tier: shared analysis → Cleo routing → parallel agents → Nova consolidation
  - Lazy import of AIReviewer modules, graceful degradation fallback
- `src/revue/tests/core/test_license_validator.py`: 7 new tests for `REVUE_TIER_OVERRIDE` incl. security edge cases
- `src/revue/tests/core/test_pipeline.py`: 3 new orchestration tests, updated pro-tier test
- **501/501 tests passing** ✅

### Process improvements
- **PR description = AI review context**: Learned that Revue needs filled PR descriptions to avoid false positives. "Out of Scope" section is now mandatory.
- **PR workflow**: Fill description → commit → push → create PR (not the other way round)
- **Credentials security**: Fixed hardcoded email + repo slug in `scripts/create-pr.sh`

### New tickets created
| Ticket | Summary | Status |
|--------|---------|--------|
| REVUE-83 | Fix failing CI pipeline on main branch | To Do |
| REVUE-84 | Smart PR description context filtering (multi-platform) | To Do |
| REVUE-85 | Document PR description best practices for customers | To Do |

---

## Sprint & Epic State

| Epic | Stories | Done | Status |
|---|---|---|---|
| E1 — Core Review Engine | 9 | 9/9 | ✅ Complete |
| E2 — VCS Platform Integration | 9 | 9/9 | ✅ Complete |
| E3 — Agent System & Routing | 16 | 16/16 | ✅ Complete |
| E4 — Sage: The Resolver Agent | 5 | 5/5 | ✅ Complete |
| E5 — AI Backend & Configuration | 4 | 4/4 | ✅ Complete |
| E6 — Onboarding, Observability & Launch | 14 | 14/14 | ✅ Complete |
| E7 — Post-MVP Tech Debt | 8 | 8/8 | ✅ Complete |

**Post-MVP stories:**
| Ticket | Summary | Status |
|--------|---------|--------|
| REVUE-79 | Literal type for comment_style | ✅ Done |
| REVUE-80 | Replace print() with logging | Closed (Won't Fix) |
| REVUE-81 | Pipeline respects `agents_allowed` | ✅ Done |
| REVUE-82 | Wire full orchestration engine | ✅ Done |
| REVUE-83 | Fix failing CI pipeline on main | 🔲 To Do |
| REVUE-84 | Smart PR description context filtering | 🔲 To Do (blocked on REVUE-82 ✅) |
| REVUE-85 | Document PR best practices | 🔲 To Do (blocked on REVUE-84) |

---

## Remaining work — next steps

### 1. REVUE-83 — Fix failing CI pipeline on main (HIGH, no blockers)
- **First action**: Check latest failed pipeline at https://bitbucket.org/cbscd/revue/pipelines
- May be related to REVUE-81 merge (new agents_allowed logic) or env config
- Note: Bitbucket API token lacks `read:pipeline:bitbucket` scope — check via web UI
- Fix → commit → push to main → verify green

### 2. REVUE-84 — Smart PR description context filtering (unblocked now REVUE-82 is done)
- **First action**: Create `src/revue/core/vcs_adapter.py` with `VCSAdapter` protocol + `BitbucketAdapter`, `GitHubAdapter`, `GitLabAdapter`
- Auto-detect platform from env vars: `BITBUCKET_WORKSPACE`, `GITHUB_ACTIONS`, `GITLAB_CI`
- Then create `src/revue/core/pr_context.py` with `PRContextExtractor` (parse PR description sections, route relevant sections per agent)
- Section-to-agent map: orchestrator→Summary+OutOfScope, security-expert→OutOfScope+Dependencies+Changes, etc.
- Wire into `pipeline._run_orchestration()` — pass context to each agent before review
- Token efficiency target: 40-60% savings vs. naive full-description-to-all approach
- Add `--auto-detect-pr` flag to CLI

### 3. DNS — `api.revue.io`
- CNAME `api.revue.io` → `revue-io.fly.dev` in domain registrar
- Then: `flyctl certs create api.revue.io`
- Then: revert `VALIDATE_URL` in `license_validator.py` to `https://api.revue.io/license/validate`

### 4. Stripe setup (pre-launch)
```bash
fly secrets set STRIPE_SECRET_KEY=sk_live_...
fly secrets set STRIPE_WEBHOOK_SECRET=whsec_...
fly secrets set STRIPE_PRICE_INDIE_MONTHLY=price_...
fly secrets set STRIPE_PRICE_PRO_MONTHLY=price_...
```

### 5. Stale branches to clean up
`hotfix/pipeline-install`, `hotfix/pipeline-pythonpath`, `hotfix/pipeline-setuptools`, `fix/REVUE-76-pipeline-yaml-comments`, `chore/fix-dod-sdlc-order`

---

## Key architectural decisions made this session

| Decision | Rationale |
|----------|-----------|
| `REVUE_TIER_OVERRIDE` disabled in Nuitka builds (`sys.frozen`) | Prevents license bypass in distributed binaries |
| `REVUE_TIER_OVERRIDE` requires `APP_ENV=development\|staging` explicitly | Prevents spoofing with arbitrary APP_ENV values |
| Sage deferred from REVUE-82 → REVUE-84 | Sage needs VCSAdapter (being designed in REVUE-84); avoids partial implementation |
| Smart PR context filtering over SharedAnalysisResult | Per-agent filtering more token-efficient than naive full-description-to-all; aligns with REVUE-84 VCSAdapter work |
| PRContextExtractor routes sections by agent domain | Security agent gets security-relevant sections; avoids noise; ~50% token savings |

---

## Bitbucket repo variables (current state)
| Variable | Value | Notes |
|---|---|---|
| `AI_API_KEY` | Anthropic API key | secured |
| `AI_MODEL` | `claude-sonnet-4-5` | production quality |
| `AI_PROVIDER` | `anthropic` | |
| `BITBUCKET_API_TOKEN` | Bitbucket token | secured — lacks `read:pipeline` scope |
| `BITBUCKET_USERNAME` | Bitbucket username | |
| `REVUE_LICENSE_KEY` | `lic_8af0c4ff679df6100510319561d2f2bb` | secured, ci@revue.io — Free tier |

**Note**: CI license is Free tier. To test Pro-tier orchestration in CI, set `APP_ENV=staging` + `REVUE_TIER_OVERRIDE=pro` in Bitbucket repo variables (safe — not a production build).

---

## Key file locations
- Pipeline: `src/revue/core/pipeline.py`
- License validator: `src/revue/core/license_validator.py` (VALIDATE_URL → fly.dev, pending DNS)
- CLI: `src/revue/cli.py`
- CI config: `bitbucket-pipelines.yml`
- Client config: `.revue.yml` (in repo root)
- PR template: `.bitbucket/pull_request_template.md`
- PR creation script: `scripts/create-pr.sh`
- PR guide: `docs/PR_TEMPLATE_GUIDE.md`
- DoD checklist: `docs/story-dod-checklist.md`
- Jira mapping: `scripts/taiga_to_jira_mapping.json`

---

## Continuation prompt

```
Read Projects/revue.io/docs/session-continuation.md for full context.

REVUE-81 ✅ and REVUE-82 ✅ merged. 501/501 tests passing.
Full orchestration engine live for paid tiers (Cleo → parallel agents → Nova).

Next: REVUE-83 (no blockers) — fix failing CI pipeline on main.
First action: check https://bitbucket.org/cbscd/revue/pipelines for latest failure.

Then: REVUE-84 — smart PR context filtering. Start with:
  src/revue/core/vcs_adapter.py (VCSAdapter protocol + Bitbucket/GitHub/GitLab adapters)
  src/revue/core/pr_context.py (PRContextExtractor with section-to-agent map)

Process reminder: fill PR description BEFORE creating PR — Revue uses it as context.
Branch format: feat/REVUE-XX-description | Commit: type(scope)[REVUE-XX]: description
```
