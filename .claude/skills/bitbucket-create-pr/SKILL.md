---
name: bitbucket-create-pr
model: haiku
description: Create a Bitbucket pull request for the current branch. Reads the PR template, fills every section from branch/Jira/git context, submits via the Bitbucket API, and transitions the Jira ticket to Code Review. Use when the user says "create a PR", "open a PR", "raise a PR", "submit a pull request", or after finishing work on a branch that has a linked Jira ticket.
allowed-tools: Bash, Read, Write
---

Create a Bitbucket pull request for the current branch with a fully-filled template.

## Configuration

Always `source ~/.zshenv` before any API or script call.

| Variable | Purpose |
|----------|---------|
| `BITBUCKET_API_TOKEN` | Atlassian API token — used for ALL calls (reads and writes) |
| `BITBUCKET_USERNAME` | Bitbucket username / email |
| `JIRA_API_TOKEN` | Jira API token (for fetching ticket details) |

- Workspace/repo: `cbscd/revue` (override via `BITBUCKET_WORKSPACE` / `BITBUCKET_REPO_SLUG`)
- **Auth note:** Atlassian **deprecated App Passwords** — `BITBUCKET_APP_PASSWORD` no longer authenticates (401). Use Basic auth via `-u "${BITBUCKET_USERNAME}:${BITBUCKET_API_TOKEN}"` for every call, reads and writes. Bearer tokens also 401 for writes.
- PR template: `.bitbucket/pull_request_template.md` — fill **every** section, no placeholder left blank

## Script

```
.claude/skills/bitbucket-create-pr/scripts/create_pr.sh TICKET "PR title" description_file [destination]
```

Sources `~/.zshenv` internally. Authenticates with `BITBUCKET_API_TOKEN` (App Passwords are deprecated).

---

## Workflow

### Step 1 — Gather context

```bash
# Current branch
git rev-parse --abbrev-ref HEAD

# Commits on this branch not in main (scan for Jira ticket key)
git log main..HEAD --oneline

# Files changed
git diff --stat main..HEAD
```

Extract the Jira ticket key from the branch name. Pattern: `type/REVUE-NNN-description` → `REVUE-NNN`.  
If the ticket is ambiguous, use the most recent commit that contains `[REVUE-NNN]`.

### Step 2 — Fetch Jira ticket details

```bash
bash .claude/skills/jira-ticket/scripts/jira_fetch.sh REVUE-NNN
```

Extract: summary (for PR title), acceptance criteria (for the AC section), background/user story (for the Summary section).

### Step 3 — Build the PR title

Format: `type(scope)[REVUE-NNN]: description`

Match the type and scope from the most recent commit message on the branch. The description should be a concise version of the Jira summary (50 chars or less after the ticket key).

### Step 4 — Fill the PR template

Read the template:

```bash
cat .bitbucket/pull_request_template.md
```

Fill every section with real content. Rules from `CLAUDE.md`:

- **No placeholders left blank** — every section must have actual content
- **Acceptance Criteria**: copy from Jira ticket, check all as `[x]` (work is done by the time you open the PR)
- **Testing / Unit tests**: include the exact pytest command and result — run tests if the result is not already known
- **Testing / Manual testing**: describe what was manually verified, or "Not applicable — tooling/config change only"
- **Testing / E2E tests**: describe coverage or "Not applicable"
- **Code Review Checklist**: check relevant items, leave unchecked only those that genuinely don't apply (add a note explaining why)
- **Impact / Business** and **Impact / Technical**: fill both — never leave blank
- **Breaking Changes**: "None" if none
- **Documentation**: list what was updated, or "Not required — tooling change only"
- **Dependencies / Blocks, Blocked by, Related**: fill all three or "None"
- **Deployment Notes**: "Standard deployment" if nothing special, otherwise explain

Write the filled template to a temp file:

```bash
TICKET="REVUE-NNN"
cat > /tmp/pr-description-${TICKET}.md << 'TEMPLATE'
<full filled template content here>
TEMPLATE
```

### Step 5 — Create the PR

```bash
source ~/.zshenv
bash .claude/skills/bitbucket-create-pr/scripts/create_pr.sh \
  REVUE-NNN \
  "type(scope)[REVUE-NNN]: description" \
  /tmp/pr-description-REVUE-NNN.md \
  main
```

The script outputs: `PR #<id>: https://bitbucket.org/cbscd/revue/pull-requests/<id>`

If the script fails with an auth error, check that `BITBUCKET_API_TOKEN` is set (App Passwords are deprecated and will 401):

```bash
source ~/.zshenv && echo "API_TOKEN set: ${BITBUCKET_API_TOKEN:+yes}"
```

### Step 6 — Transition Jira to Code Review

```bash
bash .claude/skills/jira-ticket/scripts/jira_transition.sh REVUE-NNN code-review
```

Expected output: `REVUE-NNN → code-review (HTTP 204)`

**Never** transition to `done` — Bitbucket automation handles that on merge.

---

## Output

Report to the user:

```
✅ PR #<id> opened: <url>
✅ REVUE-NNN → Code Review
```

If any step fails, report the exact error before stopping.
