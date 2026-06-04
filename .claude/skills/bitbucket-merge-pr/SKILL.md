---
name: bitbucket-merge-pr
model: sonnet
description: Squash-merge an open Bitbucket pull request using the PR title as the exact commit message (no "Merged in..." header). Closes the source branch after merge, syncs the local repo, and pushes to GitHub and GitLab mirrors. Use when the user says "merge the PR", "merge PR #N", "merge this PR", "squash merge", or "merge and clean up".
allowed-tools: Bash, Read
---

Squash-merge a Bitbucket PR using the PR title as the commit message, then clean up the local branch.

## Configuration

Always `source ~/.zshenv` before any API or script call.

| Variable | Purpose |
|----------|---------|
| `BITBUCKET_APP_PASSWORD` | App password — preferred for write (POST) calls |
| `BITBUCKET_API_TOKEN` | API token — fallback if APP_PASSWORD not set |
| `BITBUCKET_USERNAME` | Bitbucket username / email |
| `GITLAB_TOKEN` | GitLab PAT — required for protect/unprotect API calls during remote sync |

- Workspace/repo: `cbscd/revue` (override via `BITBUCKET_WORKSPACE` / `BITBUCKET_REPO_SLUG`)

## Script

```
bash .claude/skills/bitbucket-merge-pr/scripts/merge_pr.sh PR_NUMBER
```

Sources `~/.zshenv` internally. Outputs the merged PR title and the source branch name (last line).

---

## Workflow

### Step 1 — Resolve the PR number

If the user specified a PR number (e.g. "merge PR #101"), use it directly.

If no PR number was given, find the open PR for the current branch:

```bash
source ~/.zshenv
BRANCH=$(git rev-parse --abbrev-ref HEAD)
curl -s -u "${BITBUCKET_USERNAME}:${BITBUCKET_APP_PASSWORD:-${BITBUCKET_API_TOKEN}}" \
  "https://api.bitbucket.org/2.0/repositories/${BITBUCKET_WORKSPACE:-cbscd}/${BITBUCKET_REPO_SLUG:-revue}/pullrequests?state=OPEN&q=source.branch.name=%22${BRANCH}%22" \
  | jq -r '.values[0].id // empty'
```

If no open PR is found, report: "No open PR found for branch `<branch>` — create one first with /bitbucket-create-pr."

### Step 2 — Merge the PR

```bash
source ~/.zshenv
bash .claude/skills/bitbucket-merge-pr/scripts/merge_pr.sh PR_NUMBER
```

The script prints:
```
PR #N: <pr title>
Branch:  <source-branch>
Message: <pr title>

✅ Merged: <pr title>
<source-branch>
```

Capture the last line of output as `SOURCE_BRANCH` for the cleanup step.

If the script exits non-zero, print the error and stop.

### Step 2b — Stop redundant CI pipelines

The PR pipeline (`run-tests` + the AI `revue-review` step) is tied to the source
branch and keeps running — burning AI tokens — after the merge lands. Stop any
still-running pipeline for this PR:

```bash
source ~/.zshenv
bash .claude/skills/bitbucket-merge-pr/scripts/stop_pr_pipelines.sh PR_NUMBER SOURCE_BRANCH
```

The script finds `IN_PROGRESS`/`PENDING` pipelines for the PR (and its source
branch) and stops each via the Bitbucket `stopPipeline` API. It prints either
`✅ Stopped #<build> <uuid>` per pipeline or `No in-progress pipelines to stop`.

This is **best-effort cost-saving** — the merge has already landed. If it exits
non-zero, surface the message but **do not** treat it as a blocker or retry the
merge; continue to the sync steps.

### Step 3 — Sync local repo

After a successful merge, bring the local repo up to date and remove the stale branch (and any worktree attached to it):

```bash
git switch main
git pull origin main
bash .claude/skills/bitbucket-merge-pr/scripts/cleanup_branch.sh SOURCE_BRANCH
```

`cleanup_branch.sh` detects whether `SOURCE_BRANCH` is checked out in a worktree (per REVUE-349):

- Clean worktree → removes it, then deletes the branch. Prints `✅ Worktree <path> removed` and `✅ Branch <branch> deleted`.
- No worktree → just deletes the branch. Prints `✅ Branch <branch> deleted`.
- Dirty worktree → exit 2; preserves both. Surface the error and stop.
- `git branch -d` failed → exit 4; surfaces git's stderr verbatim. If stderr says "not fully merged" (the squash-merge case), additionally suggests inspecting `git diff origin/main..BRANCH --numstat` and force-deleting with `-D` if safe.

If the script exits non-zero, surface the message to the user and stop — do not force-delete without confirmation.

### Step 4 — Sync GitHub and GitLab mirrors

After local main is up to date, push to the secondary remotes:

```bash
bash .claude/skills/bitbucket-merge-pr/scripts/sync_remotes.sh
```

The script:
1. Force-pushes `main` to the `github` remote.
2. Unprotects GitLab `main` via API (required — `allow_force_push` is false by default).
3. Force-pushes `main` to the `gitlab` remote.
4. Re-protects GitLab `main` at Maintainer level (push=40, merge=40).

If the script exits non-zero, print the error. **Do not treat a remote-sync failure as a blocker** — the Bitbucket merge already landed; report the error and let the user decide whether to retry.

### Step 5 — Epic progress recap

Three sequential sub-steps:

**5a — Epic recap (foreground)**

Invoke the `/epic-progress` skill directly and print its output verbatim:

```
Skill(skill: "epic-progress", args: "<TICKET-KEY>")
```

Format and rules live inside `/epic-progress`; do not hand-roll JQL here.

**5b — Update mvp-compass.md (background EDIT only, wait for completion)**

Dispatch a background Agent to **edit** the compass (this is the token-heavy
step — backgrounding it saves context). The agent **must not commit or push** —
persistence is Step 5b-2's job (a deterministic foreground script that serialises
with concurrent merges; a backgrounded git push races them). Wait for the
completion notification before proceeding to 5b-2:

The compass is **lean and forward-looking** — "done" is a count plus a capped
"Recently shipped (last 5)" list, NOT a per-ticket archive (full history lives in
Jira). The edit must keep it that way:

```
Agent(
  description: "Update mvp-compass.md after <TICKET-KEY> merge",
  run_in_background: true,
  prompt: "Edit `docs/planning/mvp-compass.md` in the repo at /Volumes/LexarSSD/Projects/revue.io to reflect that <TICKET-KEY> has just been merged to main. Read the file first, then make ONLY these changes: (1) in the Progress line, increment the 'X / Y done' count, and decrement the strongly-should count if <TICKET-KEY> was one; (2) prepend <TICKET-KEY> (one line: key + short description) to 'Recently shipped (last 5)' and DELETE the oldest entry so the list stays at exactly five; (3) remove <TICKET-KEY> from its open bucket (Strongly-should / Pre-launch polish / Post-launch deferred / Tooling follow-up); (4) if a Parallelism same-file-collision row now has only one open ticket left, drop that row; (5) update the '0 hard launch blockers' line or a bucket's 'why' note ONLY if this merge genuinely changed it, and keep any such edit to one line. Do NOT append a per-ticket 'Done' narrative, do NOT add a Done table or changelog paragraph, and do NOT re-grow any archive — the full Done history is Jira's job; this doc stays lean. Keep every other section intact. EDIT ONLY — do NOT git add / commit / push; persistence is handled separately."
)
```

**5b-2 — Persist the compass (foreground, after 5b completes)**

Run the `commit-compass` skill's script in the **foreground** (never background —
it commits + pushes `main` and must serialise with concurrent merges). It commits
the edited compass to `main`, pushes to **Bitbucket only**, and cycles the single
reusable `compass-auto` Jira ticket (In Progress → Done after a confirmed push):

```bash
bash .claude/skills/commit-compass/scripts/commit_compass.sh "<TICKET-KEY> merged — <one-line summary>"
```

A non-zero exit means strictly **the compass did not reach origin** (its ticket
is left In Progress) — surface the error verbatim and fix the push. Do **not**
retry the PR merge (already landed). A clean exit with a ⚠️ "could not be set
Done" means the compass **did** land but its Jira ticket needs a manual
transition — no re-push.

**5c — PM commentary on MVP delivery (foreground, after 5b-2 completes)**

Once the compass is updated, invoke `bmad-agent-pm` so the commentary reflects the current MVP state:

```
Skill(skill: "bmad-agent-pm", args: "The ticket <TICKET-KEY> has just been merged and `docs/planning/mvp-compass.md` has been updated. As the Product Owner, read the updated compass and give me a brief commentary on MVP delivery: are we on track, what is now unblocked, and what is the next priority?")
```

Print the PM's response after the recap output.

This step is post-merge bookkeeping; if any sub-step fails, surface the error but do not retry the merge.

---

## Output

Report to the user. When a worktree was attached to the source branch:

```
✅ Merged: <pr title>
✅ Stopped redundant CI pipeline(s): #<build> ...   (omit if none were running)
✅ main updated (git pull)
✅ Worktree <worktree-path> removed
✅ Branch <source-branch> deleted
✅ GitHub main synced
✅ GitLab main synced

<epic progress recap (step 5a)>
<PM commentary on MVP delivery (step 5c, after mvp-compass.md update)>
```

When the source branch had no worktree:

```
✅ Merged: <pr title>
✅ Stopped redundant CI pipeline(s): #<build> ...   (omit if none were running)
✅ main updated (git pull)
✅ Branch <source-branch> deleted
✅ GitHub main synced
✅ GitLab main synced

<epic progress recap (step 5a)>
<PM commentary on MVP delivery (step 5c, after mvp-compass.md update)>
```

If any step fails, report the exact error before stopping.
