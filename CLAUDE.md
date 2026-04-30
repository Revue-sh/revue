# Revue.io

AI-powered multi-agent code review CLI for GitHub, GitLab, and Bitbucket.

## Commands

### Tests

See `docs/guides/testing.md` for commands, conventions, and AC contract testing rules.

### Pull requests

Every PR **must** use `.bitbucket/pull_request_template.md` — fill in every section, no placeholders left blank. See `docs/team/PR_TEMPLATE_GUIDE.md` for guidance.

Commit and PR title format: `type(scope)[REVUE-XX]: description`

### Jira ticket states — non-negotiable rules

| When | Jira state | How |
|------|-----------|-----|
| Work starts on a story | → **In Progress** | Manual via `jira_transition.sh` |
| PR opened | → **Code Review** | Manual via `jira_transition.sh` |
| PR merged to main | → **Done** | **Automatic** — Bitbucket automation; never do this manually |

**NEVER** call the Jira transition API to set Done. The Bitbucket→Jira automation handles it on merge. Calling it manually before merge is always wrong.

## Coding standards

- **TDD**: write a failing test before writing implementation. Run the full test suite after each task.
- **SOLID**: actively apply all five principles. Never defer violations as post-MVP — flag immediately.
- Every new function/method must have corresponding unit tests before it is considered complete.
- Prefer small, focused commits: one logical change per commit.

## Architecture rules

Read `ARCHITECTURE.md` before any structural change. Non-negotiable:

- **Layered**: CLI → Service → Repository → Infrastructure. No layer skipping.
- **No raw SQL** outside `db/repositories/`
- **Constructor injection** — never instantiate dependencies inside service methods
- **Domain models** (`core/models.py`) separate from DB schemas — no tuples/dicts in business logic
- MVP uses local `.revue/` JSON persistence. `JsonReviewRepository` and `PostgresReviewRepository` share the same interface — swapping is a one-line constructor change.

## Internal flags

| Flag | Default | Purpose |
|------|---------|---------|
| `REVUE_METRICS_ENABLED` | off | Enables `JsonlMetricsCollector`; writes per-run token usage to `.revue/metrics.jsonl`. Never document on any public surface — see `docs/architecture/pipeline-metrics.md` ADR D6. |

## Key references

| Area | Where to look |
|------|--------------|
| Architecture diagram + agent roles | `docs/planning/prd.md` §4.3 (Cleo → Zara/Kai/Maya/Leo → Nova → Comments) |
| Nova consolidation pipeline | `docs/architecture/consolidation.md` |
| Post-MVP agentic loop | `docs/architecture/agentic-review-loop.md` |
| Pipeline code | `src/revue/core/pipeline.py`, `core/cleo_router.py` |
| Comment posting / threading | `src/revue/comments/service.py`, `comments/platform_adapter.py` |
| VCS integration | `src/revue/core/vcs_adapter.py` |
| Domain types | `src/revue/core/models.py` |
| AI provider | `src/revue/core/ai_client.py` |
| Config schema | `docs/guides/revue-yml-reference.md` |
| Testing commands + conventions | `docs/guides/testing.md` |
| Sprint context | `docs/team/HANDOFF.md` |
