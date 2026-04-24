# Revue.io

AI-powered multi-agent code review CLI for GitHub, GitLab, and Bitbucket.

## Commands

### Tests
```bash
# Unit tests (fast, no DB)
cd src && PYTHONPATH=$(pwd) pytest revue/tests/ -q

# Single file
cd src && PYTHONPATH=$(pwd) pytest revue/tests/core/test_pipeline.py -v

# Single test
cd src && PYTHONPATH=$(pwd) pytest revue/tests/core/test_pipeline.py::test_name -v
```

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

### CI reproduction (from bitbucket-pipelines.yml)
```bash
pip install openai anthropic httpx pyyaml tomli-w pytest --quiet
cd src && PYTHONPATH=$(pwd) python3 -m pytest revue/tests/ -q
```

The `conftest.py` at repo root adds `src/` to `sys.path` automatically.

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

## Testing conventions

- Unit tests: `src/revue/tests/` — mock all external deps (repos, AI clients, VCS)
- Integration tests: `tests/` — gated with `@pytest.mark.integration`
- Mock repositories extend the real class (LSP-compliant)
- IMPORTANT: pipeline and cross-platform stories need live CI log evidence + error-path simulation. Unit tests alone are insufficient.

### AC contract testing — mandatory before Code Review

Every AC that specifies a data schema or output format **must** have a test that asserts every field in that schema — not just the fields that seem important at the time. Silently skipping fields (e.g. asserting token counts but not `agent_name`) allows data-flow bugs to pass tests and reach production.

Before transitioning a ticket to Code Review:
1. For every output schema in the ACs (JSONL, dataclass, API response), enumerate all fields.
2. Confirm a test asserts each field by name, including optional/nullable ones.
3. Confirm the end-to-end wiring is tested — not just that the writer accepts a value, but that the caller passes it.

**Why:** REVUE-162 shipped with `agent_name=None` hardcoded in `AnthropicClient.complete()`. The test checked 5 token fields but skipped `agent_name`. The artifact schema looked valid until Daniel downloaded and inspected it manually. This must be caught in tests, not in production artifact inspection.

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
| Sprint context | `docs/team/HANDOFF.md` |
