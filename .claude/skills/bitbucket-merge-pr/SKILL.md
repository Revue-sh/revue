---
name: bitbucket-merge-pr
model: haiku
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

### Step 3 — Sync local repo

After a successful merge, bring the local repo up to date and remove the stale branch:

```bash
git switch main
git pull origin main
git branch -d SOURCE_BRANCH 2>/dev/null || true
```

If `git branch -d` fails (branch not local), skip silently — it was already gone.

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

Dispatch `/bmad-agent-pm` (John) as a background sub-agent. Pass this exact prompt:

> Invoke the `/epic-progress <TICKET-KEY>` skill and return its output verbatim. Do not add commentary, do not re-format — just the recap text.

Print John's response verbatim after the merge confirmation lines. Format and rules live inside `/epic-progress`; do not hand-roll JQL here.

This step is post-merge bookkeeping; if it fails, surface the error but do not retry the merge.

---

## Output

Report to the user:

```
✅ Merged: <pr title>
✅ main updated (git pull)
✅ Branch <source-branch> deleted locally
✅ GitHub main synced
✅ GitLab main synced

<epic progress recap from /bmad-agent-pm>
```

If any step fails, report the exact error before stopping.
