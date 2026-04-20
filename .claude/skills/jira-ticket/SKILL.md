---
name: jira-ticket
model: haiku
description: Fetch, list, search, create, and transition Jira tickets for the REVUE project. Use when the user asks to read, create, search, or transition a Jira ticket. Invoked as /jira-ticket [KEY|search query|transition KEY status|create ...].
allowed-tools: Bash
---

Access Jira tickets for the revue.io project via the Atlassian REST API.

## Configuration

- Base URL: `https://urukia.atlassian.net`
- Auth: Basic auth — `BITBUCKET_USERNAME` + `JIRA_API_TOKEN` env vars (in `~/.zshenv`)
- Project key: `REVUE`
- Epic field: `parent` (next-gen project — not `customfield_10014`)
- **Create endpoint: `POST /rest/api/2/issue`** ← v2 only; v3 returns 404 on POST

## Scripts

All operations have a reusable script in `./scripts/`. Always run scripts from the
skill base directory so relative paths resolve correctly. Scripts source `~/.zshenv`
themselves — no need to source it beforehand.

| Script | Usage |
|--------|-------|
| `jira_fetch.sh` | Fetch one or more tickets by key |
| `jira_search.sh` | Search by JQL |
| `jira_transition.sh` | Transition a ticket to done / in-progress / todo |
| `jira_set_epic.sh` | Assign one or more tickets to an epic |
| `jira_create.sh` | Create a new Task ticket |

## Common status IDs

| Status | ID |
|--------|----|
| To Do | 10109 |
| In Progress | 10110 |
| Code Review | 10111 |
| Done | 10112 |

## Transition IDs (used by jira_transition.sh)

| Status | Transition ID |
|--------|--------------|
| todo | 11 |
| in-progress | 21 |
| code-review | 2 |
| done | 31 |
| rejected | 3 |

**NEVER** call `done` manually — Bitbucket automation transitions to Done on merge.

## Common epic IDs

| Epic | Jira ID |
|------|---------|
| REVUE-87 (E8 — Review Intelligence & Knowledge Base) | 10937 |

## Instructions

Parse the user's argument to determine intent and call the appropriate script.

### 1. Fetch a specific ticket — `/jira-ticket REVUE-117`

```bash
bash .claude/skills/jira-ticket/scripts/jira_fetch.sh REVUE-117
```

For multiple tickets:

```bash
bash .claude/skills/jira-ticket/scripts/jira_fetch.sh REVUE-113 REVUE-114
```

### 2. List / search tickets — `/jira-ticket list` or `/jira-ticket search <terms>`

```bash
# All open tickets
bash .claude/skills/jira-ticket/scripts/jira_search.sh "project=REVUE AND status in ('To Do','In Progress') ORDER BY updated DESC"

# Tickets without an epic
bash .claude/skills/jira-ticket/scripts/jira_search.sh "project=REVUE AND \"Epic Link\" is EMPTY AND issuetype != Epic ORDER BY updated DESC" 50

# Tickets under a specific epic
bash .claude/skills/jira-ticket/scripts/jira_search.sh "project=REVUE AND \"Epic Link\" = REVUE-87 ORDER BY status ASC"

# Keyword search
bash .claude/skills/jira-ticket/scripts/jira_search.sh "project=REVUE AND text ~ \"rate limit\""
```

Common JQL patterns:
- `status="To Do"` — backlog
- `status="In Progress"` — active
- `issuetype = Epic` — all epics
- `priority = High` — high priority items

### 3. Transition a ticket — `/jira-ticket transition REVUE-117 done`

```bash
bash .claude/skills/jira-ticket/scripts/jira_transition.sh REVUE-117 done
# Valid statuses: done, in-progress, todo
```

### 4. Assign to epic — `/jira-ticket set-epic REVUE-87 REVUE-119 REVUE-120`

```bash
bash .claude/skills/jira-ticket/scripts/jira_set_epic.sh REVUE-87 REVUE-119 REVUE-120
# Accepts epic key (REVUE-87) or numeric Jira ID (10937)
```

### 5. Create a ticket — `/jira-ticket create story: "summary"`

```bash
# Minimal
bash .claude/skills/jira-ticket/scripts/jira_create.sh "Summary text" [EPIC_KEY] [label]

# With description body (Jira wiki markup — h2. headings, *bold*, etc.)
bash .claude/skills/jira-ticket/scripts/jira_create.sh "Summary text" EPIC_KEY "" "h2. User Story\n\n..."

# Or pipe description from a heredoc (omit $4)
bash .claude/skills/jira-ticket/scripts/jira_create.sh "Summary text" EPIC_KEY "" <<'EOF'
h2. User Story

As a ...
EOF
```

Description format: Jira wiki markup (`h2.`, `*bold*`, `_italic_`, `\n` for newlines).
Use `""` as a placeholder for label when you only want to set the description.
Description template: read the `docs/story-dod-checklist.md`

### 6. No argument — list all open tickets

```bash
bash .claude/skills/jira-ticket/scripts/jira_search.sh "project=REVUE AND status in ('To Do','In Progress') ORDER BY updated DESC" 30
```
