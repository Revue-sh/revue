# Session Continuation
**Updated:** 2026-03-27 16:46 GMT | **For:** Next session

---

## Completed this session

### Process & Quality
- **Full DoD review** of all 30 previously completed stories — checked code, tests, and ACs against the DoD checklist
- **DoD checklist updated** (`story-dod-checklist.md`) — added mandatory item 8: Board Sync (code review → QA → Taiga → epic status → fallback to kanban-board.md if Taiga down)
- **Sage confidence threshold** investigated and fixed — threshold was 0.70 (not PRD's 0.90); made configurable via `.revue.yml` (`review.min_confidence`); customer-facing doc written (`docs/sage-confidence-threshold.md`); Taiga Story 28 AC updated
- **Taiga board fixed** — all orphan stories (37–61) linked to correct epics, 13 implemented stories closed, 5 duplicates archived

### E7 — Post-MVP Tech Debt (8/8 ✅ COMPLETE)
All 8 tech debt stories completed, committed to both repos, closed on Taiga:

| Story | What was done |
|-------|--------------|
| **[70]** | `FileChange.language` field added + 12 edge case tests (empty diff, binary, rename, >10 files) |
| **[71]** | Agent timeout made configurable (`review.agent_timeout_seconds`, default 90s per PRD) |
| **[72]** | Language-aware DI filters (Swinject, Koin, Dagger, Angular) + linter suppression (SwiftLint, Detekt, Checkstyle, ESLint, noqa) + `build_filters()` configurable |
| **[73]** | `verify_webhook_signature(payload, signature)` added to `VCSAdapter` Protocol; both adapters comply; `isinstance()` enforced at runtime |
| **[74]** | `post_review_comment` canonical name (compat alias kept); GitHub `get_diff()` paginated (100/page); `GitLabAdapter.set_review_status()` for blocking mode |
| **[75]** | Proper `action.yml` composite GitHub Action created (`revue-io/action@v1`); `entrypoint.sh`; full `README.md` with inputs/outputs/examples/migration guide; 22 structural tests |
| **[76]** | Cleo size heuristic changed from file-count to line-count: `<50 lines → team-quick`, `>500 lines → team-full-review`; `team-quick = [maya, nova]` added to presets |
| **[77]** | 7 team YAML files in `src/AIReviewer/teams/`; `team_loader.py` with `load_team()`, `load_all_teams()`, `get_team_agents()`; `cleo_router.py` uses `_TEAM_REGISTRY` (YAML-backed, falls back to presets) |

### Repo hygiene
- **Standalone git repo** initialised at `Projects/revue.io/` — 34 commits transplanted from workspace repo using `git subtree split`; `.gitignore` added
- **All commits now go to both repos** — `Projects/revue.io/` and `workspace-bmad/`
- **E6 sprint triage** — 5 duplicate stories [39–43] archived on Taiga; 6 clean stories [62–67] confirmed as active E6 backlog

---

## Epic & Sprint State

| Epic | Stories | Done | Status |
|------|---------|------|--------|
| E1 — Core Review Engine | 9 | 9/9 | ✅ Complete |
| E2 — VCS Platform Integration | 9 | 9/9 | ✅ Complete |
| E3 — Agent System & Routing | 16 | 16/16 | ✅ Complete |
| E4 — Sage: The Resolver Agent | 5 | 5/5 | ✅ Complete |
| E5 — AI Backend & Configuration | 4 | 4/4 | ✅ Complete |
| E6 — Onboarding, Observability & Launch | 6 active | 0/6 | 🔜 Next sprint |
| E7 — Post-MVP Tech Debt | 8 | 8/8 | ✅ Complete |

**Test count:** 426 passing (0 failures) — up from 311 at session start (+115 new tests)

---

## Remaining work — E6 Active Backlog

### Delivery order (per architectural dependencies)

```
[62] Onboarding UI ──► [63] Free tier + [64] Stripe (parallel)
                    ──► [65] Run history ──► [66] Analytics
[67] Docs ─────────────────────────────── (parallel, unblocked)
```

### Story details

**[62] Workspace onboarding UI** *(L — ~1 week)*
Full-stack web app: sign-up, GitHub App install, GitLab OAuth, workspace config.
**First action:** Decide stack (React + FastAPI? Next.js? HTMX?), then scaffold `src/web/` directory.

**[63] Free tier enforcement — BYOK, 100 runs/month** *(M — ~2 days)*
Run counter in DB, enforce cap in webhook handler, return 429 with message when exceeded.
**First action:** Add `RunCounter` model to DB schema, wire into webhook handler.

**[64] Stripe billing — Pro and Team tier** *(L — ~1 week)*
Stripe Checkout integration, webhook for subscription events, feature flags per tier.
**First action:** Create Stripe account, configure products/prices, scaffold `src/billing/`.

**[65] Run history dashboard** *(M — ~2 days)*
Table view: date, PR/MR title, files reviewed, findings count, status, link to review.
**First action:** Add `ReviewRun` model to DB, expose GET `/api/runs` endpoint.
**Depends on:** [62] workspace exists, [63] run tracking in place.

**[66] Basic analytics — finding trends** *(M — ~2 days)*
Charts: findings by category and severity over time, false positive rate.
**First action:** Aggregate query over `ReviewRun.findings`, wire to `/api/analytics` endpoint.
**Depends on:** [65] run history stored.

**[67] Documentation site** *(M — ~2 days)*
Getting started guide (GitHub + GitLab), `.revue.yml` reference, agent descriptions, Sage docs.
**First action:** Choose docs framework (Docusaurus? MkDocs?), scaffold `docs-site/` directory.
**Unblocked** — can start any time in parallel.

---

## Key architectural decisions from this session

1. **Sage confidence threshold = 70 (not 90)** — deliberate. Pattern registry lowest confidence is 70 (`missing_null_check`). Setting 90 would make 3 patterns unreachable. Configurable via `.revue.yml`. See `docs/sage-confidence-threshold.md`.
2. **Team configs are declarative YAML** — `src/AIReviewer/teams/*.yml`. `cleo_router.py` loads via `team_loader.py` at import time; TEAM_PRESETS dict is fallback only.
3. **`post_review_comment` is canonical** — `post_inline_comment` kept as compat alias. Will be removed in v2.0.
4. **`revue-io/action@v1` is a composite Action** — not a Docker/JS action. `entrypoint.sh` calls `revue review` CLI. Teams reference `uses: revue-io/action@v1` — no file copying needed.
5. **Cleo size heuristic is line-based** — `< 50 lines → team-quick`, `> 500 lines → team-full-review`. Security override always bypasses `team-quick`.
6. **Both repos must be committed** — `Projects/revue.io/` (project repo) and `workspace-bmad/` (workspace). Always commit to both.

---

## Continuation prompt

```
Read Projects/revue.io/docs/session-continuation.md for full context.

Status: E1-E5, E7 complete. 426 tests passing. E6 is next (6 stories, refs 62-67).

Start with Story [62] — Workspace onboarding UI.
Full-stack: sign-up, GitHub App OAuth, GitLab OAuth, workspace config.
First action: decide frontend/backend stack and scaffold src/web/.

Project repos:
- Main: /Users/langostin/.openclaw/workspace-bmad/Projects/revue.io/
- Workspace mirror: /Users/langostin/.openclaw/workspace-bmad/
Always commit to BOTH repos.

Taiga board: http://localhost:9000/project/revueio/kanban
```

---

## Session stats

| Metric | Value |
|--------|-------|
| Session duration | ~4.5 hours |
| Stories DoD-reviewed | 30 |
| Tech debt stories completed | 8 (E7 ✅) |
| Tests added | +115 (311 → 426) |
| Commits (revue.io repo) | 18 |
| Taiga stories fixed/linked | 18 linked, 13 closed, 5 archived |
| New files created | `team_loader.py`, 7 team YAMLs, `action.yml`, `entrypoint.sh`, `sage-confidence-threshold.md`, 4 test files |
