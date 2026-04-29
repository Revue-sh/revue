---
name: bitbucket-merge-pr
model: sonnet
description: Squash-merge an open Bitbucket pull request using the PR title as the exact commit message (no "Merged in..." header). Closes the source branch after merge and syncs the local repo. Use when the user says "merge the PR", "merge PR #N", "merge this PR", "squash merge", or "merge and clean up".
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

---

## Output

Report to the user:

```
✅ Merged: <pr title>
✅ main updated (git pull)
✅ Branch <source-branch> deleted locally
```

If any step fails, report the exact error before stopping.
