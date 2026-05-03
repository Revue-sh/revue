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

Read `ARCHITECTURE.md` before any structural change. Non-negotiable: layered CLI → Service → Repository → Infrastructure (no skipping), no raw SQL outside `db/repositories/`, constructor injection, domain models in `core/models.py`, `JsonReviewRepository` and `PostgresReviewRepository` share one interface.

## Key references

Run `/prime` to load the full reference table and internal flags on demand.
