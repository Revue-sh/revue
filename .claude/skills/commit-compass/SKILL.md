---
name: commit-compass
description: Persist docs/planning/mvp-compass.md to main (origin only) after a post-merge compass update, reusing a single labelled Jira ticket. Use from bitbucket-merge-pr Step 5b-2, or whenever the compass has been edited and needs committing safely.
---

# commit-compass

Commits the MVP compass (`docs/planning/mvp-compass.md`) to `main` and pushes it
to **Bitbucket only**, cycling a single reusable Jira ticket so the backlog is
never polluted with per-update tickets. Exists because the compass was once an
untracked working doc with no revert safety, and a backgrounded agent wrongly
committed + pushed it to the wrong remote (see `feedback_compass_is_gitignored_local_only`).

## When to use

- **bitbucket-merge-pr Step 5b-2** — immediately after the background agent edits
  the compass, to persist that edit.
- Any time the compass has been changed locally and needs committing.

## Run it

```bash
bash .claude/skills/commit-compass/scripts/commit_compass.sh "<short message describing the change>"
```

Example: `... "REVUE-331 marked Done — E2E activate round-trip merged"`.

## Rules (non-negotiable)

1. **Foreground only — never background it.** The script does `git commit`/`push`
   on the shared `main` checkout and must serialise with concurrent PR merges.
   Backgrounding it reintroduces the non-fast-forward race it exists to avoid.
   (The token-heavy *compass edit* may be backgrounded; the *commit* may not.)
2. **Origin only.** Pushes `main` to Bitbucket (`origin`) and nothing else.
   GitHub/GitLab mirrors reconcile on the next real merge's `sync_remotes`.
3. **One reusable ticket, found by the `compass-auto` label.**
   - exactly one match → reuse it, transition → In Progress
   - zero matches → create it once (with the `compass-auto` label), → In Progress
   - more than one match → **fail loudly**, never guess
4. **Done only after a confirmed push.** Any failure (commit blocked, push
   rejected after retries, rebase conflict) leaves the ticket **In Progress**,
   exits non-zero, and prints a clear error naming the local commit sha. Never
   silently strand; never `--no-verify`.
5. **Moving-main safe.** On non-fast-forward, the script fetches, rebases the
   single compass commit, and retries (up to 3 attempts).

## What it does

1. Resolve the reusable ticket by label (rule 3).
2. `git add` + commit the compass as `chore(compass)[KEY]: <message>` (no
   co-author).
3. Push to `origin main` with fetch+rebase+retry on non-fast-forward.
4. Transition the ticket to Done — only after the push is confirmed.

## Preconditions

- Run from inside the repo, on `main`, with the compass actually changed
  (no-op exits 0 cleanly).
- The local protected-branch hooks must be disabled (maintainer setup) — the
  skill commits/pushes `main` directly. If a hook blocks it, the operation fails
  and the ticket is left In Progress (fail-safe). The skill never bypasses hooks
  with `--no-verify`.

## Dependencies

Uses the `jira-ticket` scripts (`jira_search.sh`, `jira_create.sh`,
`jira_transition.sh`) under `.claude/skills/jira-ticket/scripts/` and sources
`~/.zshenv` for `BITBUCKET_USERNAME` / `JIRA_API_TOKEN`.
