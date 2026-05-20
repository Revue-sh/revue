---
name: bitbucket-pr-review
model: haiku
description: Fetch and display Bitbucket PR status, CI pipeline results, comment analysis, and optionally pipeline step logs. Use when the user says "review the pipeline", "check CI", "review PR comments", "check PR status", "review the pipeline and PR comments for PR #NNN", or "fetch the logs for PR #NNN". If no PR number is given, resolves to the open PR for the current branch.
allowed-tools: Bash
---

Fetch PR info, CI pipeline status, comments, and optionally the full log of a named pipeline step.

## Configuration

Always `source ~/.zshenv` before any API call.

| Variable | Purpose |
|----------|---------|
| `BITBUCKET_API_TOKEN` | API token for read operations |
| `BITBUCKET_USERNAME` | Bitbucket username / email |

**Auth:** Always use basic auth: `-u "${BITBUCKET_USERNAME}:${BITBUCKET_API_TOKEN}"`. Bearer token fails on Bitbucket Cloud even for GET calls.

- Workspace/repo: `cbscd/revue` (override via `BITBUCKET_WORKSPACE` / `BITBUCKET_REPO_SLUG`)

---

## Script

```bash
SKILL_DIR="/Volumes/LexarSSD/Projects/revue.io/.claude/skills/bitbucket-pr-review"
bash "$SKILL_DIR/scripts/bb_pr_review.sh" [pr_number] [--logs [step_pattern]] [workspace/repo]
```

| Argument | Default | Description |
|----------|---------|-------------|
| `pr_number` | auto-detect from branch | PR number to inspect |
| `--logs` | omit for comments-only | Also fetch and print the named step's log |
| `step_pattern` | `Revue AI Code Review` | Substring match against step name |
| `workspace/repo` | `cbscd/revue` | Repo slug |

### Examples

```bash
SKILL_DIR="/Volumes/LexarSSD/Projects/revue.io/.claude/skills/bitbucket-pr-review"

bash "$SKILL_DIR/scripts/bb_pr_review.sh"                          # auto-detect, no logs
bash "$SKILL_DIR/scripts/bb_pr_review.sh" 82                       # PR #82, no logs
bash "$SKILL_DIR/scripts/bb_pr_review.sh" 82 --logs                # PR #82 + Revue step log
bash "$SKILL_DIR/scripts/bb_pr_review.sh" 82 --logs "Run Tests"    # PR #82 + Run Tests log
bash "$SKILL_DIR/scripts/bb_pr_review.sh" 82 --logs cbscd/revue    # explicit repo + logs
```

The script outputs up to four sections depending on flags:

```
=== PR INFO ===
PR #82: feat(routing)[REVUE-170]: ...
State : OPEN
Branch: feat/REVUE-170... → main
URL   : https://bitbucket.org/...

=== PIPELINE ===
✅ SUCCESSFUL  Pipeline - pullrequests: **  https://bitbucket.org/.../514

=== COMMENTS ===
Total: 29 top-level comment(s) (28 inline, 1 general)
Severity: 🔴 2 HIGH  🟡 13 MEDIUM  🔵 15 LOW  ℹ️ 2 INFO
...
--- Unresolved inline (no reply yet) ---
  [#787738167] cleo_router.py:357 🟡 [MEDIUM]  Substring matching creates false positives...

=== LOG: Revue AI Code Review ===     ← only when --logs is passed
+ git clone ...
[revue] Starting AI code review...
...
```

---

## Log resolution internals (for debugging)

The log API does not accept sort-by-build-number; the script works around it:

1. **Build number** — extracted from the pipeline URL in the PR statuses response (`pipelines/results/514`)
2. **Pipeline UUID** — found by paging `GET .../pipelines/?sort=-created_on&pagelen=50` and matching `build_number`
3. **Step UUID** — found by `GET .../pipelines/{uuid}/steps/` and substring-matching step name
4. **Log** — `GET .../pipelines/{uuid}/steps/{step_uuid}/log` with `-L` to follow the 307 redirect (returns plain text)

---

## Workflow

### Step 1 — Determine PR number and flags

Parse the user's message for:
- A PR number (`#82`, `PR 82`, `82`)
- Whether logs are requested ("fetch logs", "with logs", "pipeline logs")
- A specific step name if mentioned (default: `Revue AI Code Review`)

### Step 2 — Run the script

```bash
SKILL_DIR="/Volumes/LexarSSD/Projects/revue.io/.claude/skills/bitbucket-pr-review"
bash "$SKILL_DIR/scripts/bb_pr_review.sh" [args]
```

### Step 3 — Present findings

1. **CI result** — `✅ SUCCESSFUL` / `❌ FAILED` / `⏳ In progress`. Highlight failures prominently.
2. **Comment summary** — severity breakdown (HIGH/MEDIUM/LOW/INFO counts).
3. **Analysis** — for each finding assess: false positive, already fixed, actionable before merge, or defer.
4. **Unresolved inline list** — primary action items.
5. **Log analysis** (if `--logs` used) — look for `[revue]` prefixed lines which contain the actual review output; strip the pipeline setup boilerplate (git clone, cache, env variable listing) before presenting.

### Step 4 — Recommend next actions

- Items to fix before merge (with effort estimate)
- Items to defer (with suggested Jira ticket scope)
- Items that are false positives (no action needed)

---

## Fetching a single comment's full text

If a comment is truncated in the output, fetch the full content:

```bash
source ~/.zshenv
curl -s -u "${BITBUCKET_USERNAME}:${BITBUCKET_API_TOKEN}" \
  "https://api.bitbucket.org/2.0/repositories/cbscd/revue/pullrequests/${PR}/comments/${COMMENT_ID}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('content',{}).get('raw',''))"
```

---

## Replying to comments

Use the `pr-comments` skill for posting replies:

```bash
/Volumes/LexarSSD/Projects/revue.io/.claude/skills/pr-comments/scripts/bb_post_reply.sh \
  <parent_comment_id> "<body>" <pr_number>
```

Common reply bodies:
- False positive: `"False positive — <reason>.\n\n[//]: # (revue:fp)"`
- Won't fix: `"Won't fix — tracked as REVUE-NNN.\n\n[//]: # (revue:wontfix)"`
- Fixed: `"Fixed in <commit_sha>.\n\n[//]: # (revue:fixed)"`
