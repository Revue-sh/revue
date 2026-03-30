# Session Continuation
**Updated:** 2026-03-30 22:14 GMT | **For:** Next session

---

## Completed this session

### CI/CD Pipeline — Fully working end-to-end

| Item | Details |
|---|---|
| **Bitbucket branch protection** | Configured — PR-only merges, CI gate, no direct push |
| **Playwright E2E tests** | Scaffolded + 14/14 passing. `tests/e2e/` in `src/web/`. REVUE-74 ✅ |
| **Playwright MCP** | Configured in `~/Library/Application Support/Claude/claude_desktop_config.json` |
| **REVUE-65 — Conversion analytics** | `/conversion` route, tier breakdown, referral tracking. PR #7 merged ✅ |
| **REVUE-75 — DoD SDLC fix** | Jira Done only after PR merged. PR merged ✅ |
| **REVUE-76 — Nuitka build pipeline** | `build/` scripts, parallel Bitbucket pipeline (Linux x86_64, ARM64, macOS ARM64). PR #8 merged ✅ |
| **REVUE-79 — Literal type for comment_style** | `AIConfig.comment_style: Literal["per-issue", "summary"]`. PR #15 ✅ |
| **All 7 epics Done on Jira** | E6 epic closed ✅ |

### Taiga → Jira migration
- All 69 stories migrated. Board: https://urukia.atlassian.net/jira/software/projects/REVUE/boards/101
- Mapping file: `Projects/revue.io/scripts/taiga_to_jira_mapping.json`
- Taiga kept running as legacy reference only
- Jira is now primary. Commit format: `type(scope)[REVUE-XX]: description`

### Bitbucket pipeline debugging & fixes (PR #15 — merged ✅)
Long session. Key fixes landed on `main`:
- `python:3.12` image for AI review step (needs git)
- `pip install openai anthropic httpx pyyaml` directly (no editable install — `setuptools.backends.legacy` missing from slim image)
- `PYTHONPATH=src` + `python3 src/revue/cli.py` with `if __name__ == "__main__"` guard (was missing — root cause of silent exit)
- `git diff origin/$BITBUCKET_PR_DESTINATION_BRANCH...HEAD` for diff (no curl/API needed)
- `.revue.yml` added to repo root (`api_key_env: AI_API_KEY`)
- License validator URL changed to `https://revue-io.fly.dev/api/license/validate` (api.revue.io not yet DNS configured)
- License key for CI: `lic_8af0c4ff679df6100510319561d2f2bb` (account: `ci@revue.io`, Fly.io production DB)

### AI review features (PR #15)
- `--comment-style per-issue` (default): one inline Bitbucket comment per finding
- `--comment-style summary`: one grouped comment per file
- Configurable from `.revue.yml` under `output.comment_style`
- Professional markdown: severity badge line → summary → high/medium inline → low/info collapsed in `<details>`
- 4-step pipeline flow logged: Parsing → AI Review → Consolidation → Verdict
- Field normalisation across model variations (`issue`/`message`/`title` etc.)
- Empty findings skipped (no blank comments)

### Process improvements
- **Jira ticket format** now mandatory: User Story, Background, ACs, Test Cases, Out of Scope, Dependencies
- Documented in `docs/story-dod-checklist.md` (top of file) and `TOOLS.md`
- **Bitbucket webhook** registered for Jira PR integration (auto-links PRs to tickets)

### Bitbucket repo variables (current state)
| Variable | Value | Notes |
|---|---|---|
| `AI_API_KEY` | Anthropic API key | secured |
| `AI_MODEL` | `claude-sonnet-4-5` | update from haiku after testing |
| `AI_PROVIDER` | `anthropic` | |
| `BITBUCKET_API_TOKEN` | Bitbucket token | secured |
| `BITBUCKET_USERNAME` | Bitbucket username | |
| `REVUE_LICENSE_KEY` | `lic_8af0c4ff679df6100510319561d2f2bb` | secured, ci@revue.io account |

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

**New open stories (post-MVP):**
- **REVUE-79** — Literal type for comment_style ✅ Done
- **REVUE-80** — Replace print() with logging — CLOSED as Won't Fix (print is correct for CI progress output)
- **REVUE-81** — Pipeline respects `agents_allowed` from license — **To Do, no blockers**
- **REVUE-82** — Wire full orchestration engine for Indie/Pro — **Blocked on REVUE-81 + Pro-tier license**

---

## Remaining work — next steps

### 1. REVUE-81 — Pipeline respects `agents_allowed` (immediate, no blockers)
- File: `src/revue/core/pipeline.py`
- `validate_license()` already returns `license_info.agents_allowed`
- Wire it: `pipeline.run()` reads `license_info.agents_allowed`, only activates those agents
- Log: `[revue] Agents: orchestrator, code-quality-expert, consolidator`
- Tests: 3 new unit tests (free tier, pro tier, empty fallback)
- Branch: `feat/REVUE-81-pipeline-agents-allowed`

### 2. REVUE-82 — Full orchestration engine (blocked)
- Blocked on REVUE-81 + Pro-tier license resolution
- Resolve Pro-tier dependency first: upgrade `ci@revue.io` to Pro on Fly.io OR implement `REVUE_TIER_OVERRIDE` env var
- Valid values for REVUE_TIER_OVERRIDE: `free | indie | pro | enterprise_starter | enterprise_growth | enterprise_plus`
- Only honoured when `APP_ENV != production`

### 3. DNS — `api.revue.io`
- CNAME `api.revue.io` → `revue-io.fly.dev` in domain registrar
- Then: `flyctl certs create api.revue.io`
- Then: revert `VALIDATE_URL` in `license_validator.py` back to `https://api.revue.io/license/validate`

### 4. AI_MODEL in Bitbucket
- Currently `claude-haiku-4-5` for testing
- Update to `claude-sonnet-4-5` for production quality reviews

### 5. Stripe setup (pre-launch)
```bash
fly secrets set STRIPE_SECRET_KEY=sk_live_...
fly secrets set STRIPE_WEBHOOK_SECRET=whsec_...
fly secrets set STRIPE_PRICE_INDIE_MONTHLY=price_...
fly secrets set STRIPE_PRICE_PRO_MONTHLY=price_...
fly secrets set STRIPE_PRICE_ENT_STARTER=price_...
fly secrets set STRIPE_PRICE_ENT_GROWTH=price_...
```

### 6. Stale branches to clean up
`hotfix/pipeline-install`, `hotfix/pipeline-pythonpath`, `hotfix/pipeline-setuptools`, `fix/REVUE-76-pipeline-yaml-comments`, `chore/fix-dod-sdlc-order` — all superseded by PR #15.

---

## Open PRs
None — all merged.

---

## Key file locations
- Pipeline: `src/revue/core/pipeline.py`
- CLI: `src/revue/cli.py`
- License validator: `src/revue/core/license_validator.py` (VALIDATE_URL points to fly.dev)
- CI config: `bitbucket-pipelines.yml`
- Client config: `.revue.yml` (in repo root)
- DoD + ticket template: `docs/story-dod-checklist.md`
- Jira mapping: `scripts/taiga_to_jira_mapping.json`
- E2E tests: `src/web/tests/e2e/`

---

## Continuation prompt

```
Read Projects/revue.io/docs/session-continuation.md for full context.

Status: All 7 epics Done. Full Bitbucket CI/CD pipeline working — AI review
posts inline comments per-issue on every PR using claude-sonnet-4-5.

Next story: REVUE-81 — Pipeline respects agents_allowed from license validation
File to edit: src/revue/core/pipeline.py
First action: read license_info.agents_allowed from validate_license() response
and pass it to the agent selection logic. Log active agents. Add 3 unit tests.
Branch: feat/REVUE-81-pipeline-agents-allowed

Blockers: REVUE-82 needs Pro-tier license — resolve before starting that story.
DNS: api.revue.io CNAME → revue-io.fly.dev (not yet configured).
AI_MODEL in Bitbucket: change from claude-haiku-4-5 → claude-sonnet-4-5.
```
