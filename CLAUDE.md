# Revue

AI-powered multi-agent code review CLI for GitHub, GitLab, and Bitbucket.

## Commands

### Tests

See `docs/guides/testing.md` for commands, conventions, and AC contract testing rules.

### Pull requests

Every PR **must** use `.bitbucket/pull_request_template.md` — fill in every section, no placeholders left blank. See `docs/team/PR_TEMPLATE_GUIDE.md` for guidance.

Commit and PR title format: `type(scope)[REVUE-XX]: description`

### Scope management (intent-first)

When starting any task, establish scope upfront before writing any code:
- State the outcome and explicit boundaries ("this task does NOT include X").
- If out-of-scope work is discovered mid-task, queue it silently — do not interrupt. Present the queue after the task completes.
- Never ask mid-task whether to create a new ticket for out-of-scope items; always defer to the post-task queue review.

### Jira ticket checks

- **ALWAYS** before creating any Jira ticket, search the existing backlog for semantically similar issues first. Present any matches above 70% similarity for confirmation. Never create a ticket without completing this check.

### Jira ticket completion — epic progress recap

After Bitbucket auto-transitions a ticket to Done, dispatch `/bmad-agent-pm` **as a background sub-agent** to fetch the parent epic and produce the recap. Then render the agent's output verbatim — do not bypass John (the PM persona) by hand-rolling the JQL call yourself.

Dispatch prompt to give the PM agent:

> Fetch the parent epic of `<TICKET-KEY>` from Jira (REST v3 `/search/jql`, JQL `parent=<EPIC-KEY>`). Produce an epic progress recap in the exact format below.

Recap format the PM agent must return:

```
Epic: [<EPIC-KEY>] <Epic Name>
🟩🟩🟩🟩🟩🟩⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ <done>/<active> tickets (<pct>%)
```

Rules:

- **active** = total children − rejected − cancelled. Use `active` (not `total`) as the denominator.
- **done** = count of children with status ∈ {Done, Closed}.
- **pct** = round(done ÷ active × 100).
- **Bar width = 20 cells.** Use `🟩` (green square) for done cells and `⬜` (white square) for remaining cells — these survive markdown rendering with distinctive colour, unlike `█`/`░` which render as monochrome.
- Done cells count = `floor(done ÷ active × 20)`.
- List Rejected / Cancelled tickets in a separate row below the bar, not on the bar.

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

## IP protection — published wheels MUST be Nuitka-compiled

All three published packages (`revue_core`, `revue-ci`, `revue` skill wheel) are project IP and **must** ship to PyPI as **per-platform Nuitka-compiled wheels** — never as plain-Python source wheels. The Python source under `packaging/*/src/` is the IP asset; uploading `.py` exposes it directly.

Concretely:

- Each package owns `packaging/<pkg>/build/build_nuitka.py` + `build_wheel.py`. The tag pipeline calls these directly; it must not invoke `python -m build` or hatchling for the published artifact.
- `bitbucket-pipelines.yml` builds each package on macOS ARM64 + Linux x86_64 (minimum). Adding a platform = add another build step.
- `pyproject.toml` may keep a hatchling target so that editable dev installs (`pip install -e packaging/<pkg>/`) work from plain `.py` — but the **published** wheel always comes from the Nuitka path.
- If you see a comment, doc, or pipeline step that says "pure-Python" or "no Nuitka" for any of the three packages: it is wrong and must be corrected. There is no platform-neutral source wheel for these packages on PyPI.

This requirement predates any single Jira ticket; it is a project-wide premise.

## Key references

Run `/prime` to load the full reference table and internal flags on demand.

# Working relationship

- No sycophancy.
- Be direct, matter-of-fact, and concise.
- Be critical; challenge my reasoning.
- Don’t include timeline estimates in plans.
- Don’t add yourself as a co-author to git commits.

# Tooling

- Prefer Makefile targets (`make help`) over direct tool invocation.
- Use your Edit tool for changes; Search tool for searching.
- Use Mermaid diagrams for complex systems.