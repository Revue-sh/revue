# Revue

AI-powered multi-agent code review CLI for GitHub, GitLab, and Bitbucket.

## Commands

### Tests

See `docs/guides/testing.md` for commands, conventions, and AC contract testing rules.

### Pull requests

Every PR **must** use `.bitbucket/pull_request_template.md` — fill in every section, no placeholders left blank. See `docs/team/PR_TEMPLATE_GUIDE.md` for guidance. **Exception — TRIVIAL tier:** a single-sentence description suffices; do not fill the full template.

Commit and PR title format: `type(scope)[REVUE-XX]: description`

The Step-10 pre-commit review gate in `bmad-dev-story` is **tier-conditional**: TRIVIAL changes skip it entirely; MEDIUM changes run an in-session adversarial review; HIGH changes require the full human gate. Auto-merge at any tier never bypasses the Nuitka / HIGH ceremony and never sets Jira Done before merge.

### Code review tiers

Every change is classified into one of three tiers before any review work starts. Run `.claude/scripts/classify_diff.sh` to get the tier (exit 0 = TRIVIAL, 1 = MEDIUM, 2 = HIGH).

| Tier | Classifier | Review | Merge |
|------|-----------|--------|-------|
| **TRIVIAL** | Only files under `docs/` or `_bmad-output/` — no executable logic | None | Auto-merge on green CI |
| **MEDIUM** | Behavioral code changes not on the HIGH path | 3 adversarial agent layers in-session; all findings clean = gate | Auto-merge on green CI after clean review |
| **HIGH** | Any file matching a publish / security / infrastructure path | Full human Step-10 gate — no exceptions | Manual |

**Fails upward.** Ambiguous / unmatched / mixed / new-path changes → HIGH. Update the classifier explicitly to reclassify a path downward.

**Publish-path human gate (absolute — non-negotiable):** No change reaches a release tag, Nuitka build, or PyPI publish until a human has adjudicated every agent-rated High finding, regardless of tier. `main` is revertable; a published compiled wheel is not.

HIGH paths (any match → HIGH, wins over everything else):

- `packaging/` — Nuitka build scripts, wheel assembly
- `bitbucket-pipelines.yml`, `.github/workflows/`, `.gitlab-ci.yml` — all CI/CD pipelines (same blast radius)
- `fly.*.toml` — any Fly.io config file (glob, not enumerated)
- `src/web/main.py` — FastAPI entry point; registers all middleware (auth, rate-limit)
- `src/web/jwt_*.py`, `src/web/rate_limiter.py`, `src/web/routes/api_routes.py` — auth / rate-limit surface
- `src/web/database.py` — DB schema + migrations
- `revue_core/validate.py`, `revue_core/cache_paths.py`, `revue_core/security/` — licence validation
- `db/repositories/`, `db/migrations/` — raw repository layer + SQL
- `src/web/billing.py`, `src/web/stripe*.py` — Stripe wiring

MEDIUM paths (in-session adversarial review; anything else fails upward to HIGH):

- `src/` (minus HIGH-listed files above), `tests/`, `.claude/`, `scripts/`

### Scope management (intent-first)

When starting any task, establish scope upfront before writing any code:
- State the outcome and explicit boundaries ("this task does NOT include X").
- If out-of-scope work is discovered mid-task, queue it silently — do not interrupt. Present the queue after the task completes.
- Double check any identified out-of-scope work to determine if it's out-of-scope. Do this in the background asking the `bmad-agent-pm`.
- Never ask mid-task whether to create a new ticket for out-of-scope items; always defer to the post-task queue review.

### Jira ticket checks

- **ALWAYS** before creating any Jira ticket, search the existing backlog for semantically similar issues first. Present any matches above 70% similarity for confirmation. Never create a ticket without completing this check.

### Jira ticket completion — epic progress recap

Owned by `/epic-progress`. `bitbucket-merge-pr` dispatches John (`/bmad-agent-pm`) as a background sub-agent with a prompt that invokes `/epic-progress <TICKET-KEY>` and returns the result verbatim. See `.claude/skills/epic-progress/SKILL.md` for format and rules. Never hand-roll the JQL inline.

### Jira ticket states — non-negotiable rules

| When | Jira state | How |
|------|-----------|-----|
| Work starts on a story | → **In Progress** | Manual via `jira_transition.sh` |
| PR opened | → **Code Review** | Manual via `jira_transition.sh` |
| PR merged to main | → **Done** | **Automatic** — Bitbucket automation (default); see exceptions below |

Bitbucket automation moves merged tickets to Done by default, but the Free-plan cap (~100 runs/month) silently exhausts — if a ticket stays in Code Review after merge, transition it manually with `jira_transition.sh <KEY> done`. Tickets whose ACs require post-merge staging/prod validation carry the **`do-not-run-automation-after-merge`** label and stay in Code Review until validated, then move to Done manually. The agent may apply this label when the AC pattern clearly requires post-merge pipeline validation. **Never set Done before merge** — that rule is absolute.

## Coding standards

- **TDD**: write a failing test before writing implementation. Run the full test suite after each task. (MEDIUM and HIGH changes only — TRIVIAL changes carry no code logic to test.)
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
