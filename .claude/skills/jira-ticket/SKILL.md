---
name: jira-ticket
model: haiku
description: Fetch, list, search, create, and transition Jira tickets for the REVUE project. Use when the user asks to read, create, search, or transition a Jira ticket. Invoked as /jira-ticket [KEY|search query|transition KEY status|create ...].
allowed-tools: Bash
---

Access Jira tickets for the revue.io project via the Atlassian REST API.

## Configuration

- Base URL: `https://urukia.atlassian.net`
- Auth: Basic auth — email `BITBUCKET_USERNAME` +  `JIRA_API_TOKEN` env vars (in `~/.zshenv`)
- Project key: `REVUE`
- Search endpoint: `POST /rest/api/3/search/jql`
- Issue endpoint: `GET /rest/api/3/issue/{key}`
- Transition endpoint: `POST /rest/api/3/issue/{key}/transitions`
- **Create endpoint: `POST /rest/api/2/issue`** ← v2 only; v3 returns 404 on POST

Always `source ~/.zshenv` before any curl call.

## Common status IDs

| Status | ID |
|--------|----|
| To Do | 10109 |
| In Progress | 10110 |
| Done | 10111 |

## Instructions

Parse the user's argument to determine intent:

### 1. Fetch a specific ticket (e.g. `/jira-ticket REVUE-117`)

```bash
source ~/.zshenv && curl -s \
  -u "${BITBUCKET_USERNAME}:${JIRA_API_TOKEN}" \
  "https://urukia.atlassian.net/rest/api/3/issue/REVUE-117" | python3 -c "
import json, sys

def extract_text(node):
    if isinstance(node, str): return node
    text = ''
    if 'text' in node: text += node['text']
    for c in node.get('content', []): text += extract_text(c)
    return text

d = json.load(sys.stdin)
f = d.get('fields', {})
print('Key:    ', d.get('key'))
print('Summary:', f.get('summary'))
print('Status: ', f.get('status', {}).get('name'))
print('Type:   ', f.get('issuetype', {}).get('name'))
print('Priority:', f.get('priority', {}).get('name', 'none'))
desc = f.get('description')
if desc: print('\nDescription:\n' + extract_text(desc))
acs = f.get('customfield_10016') or f.get('customfield_10014')
if acs: print('\nACs:\n' + extract_text(acs))
"
```

Output a clean summary to the user: key, summary, status, type, description, and acceptance criteria if present.

### 2. List/search tickets (e.g. `/jira-ticket list sprint` or `/jira-ticket search won't fix`)

```bash
source ~/.zshenv && curl -s \
  -u "${BITBUCKET_USERNAME}:${JIRA_API_TOKEN}" \
  -X POST "https://urukia.atlassian.net/rest/api/3/search/jql" \
  -H "Content-Type: application/json" \
  -d '{"jql":"project=REVUE ORDER BY updated DESC","fields":["key","summary","status","issuetype","priority"],"maxResults":20}' | python3 -c "
import json, sys
d = json.load(sys.stdin)
issues = d.get('issues', [])
for i in issues:
    f = i['fields']
    status = f.get('status', {}).get('name', '?')
    itype = f.get('issuetype', {}).get('name', '?')
    print(f'{i[\"key\"]:12} [{status:12}] [{itype:8}] {f[\"summary\"]}')
print(f'\n{len(issues)} issue(s) returned.')
"
```

Adjust the JQL for the user's search intent. Common patterns:
- `project=REVUE AND status="To Do"` — open tickets
- `project=REVUE AND status="In Progress"` — active work
- `project=REVUE AND sprint in openSprints()` — current sprint
- `project=REVUE AND text ~ "rate limit"` — keyword search

### 3. Transition a ticket (e.g. `/jira-ticket transition REVUE-117 done`)

First fetch available transitions:

```bash
source ~/.zshenv && curl -s \
  -u "${BITBUCKET_USERNAME}:${JIRA_API_TOKEN}" \
  "https://urukia.atlassian.net/rest/api/3/issue/REVUE-117/transitions" | python3 -c "
import json, sys
for t in json.load(sys.stdin).get('transitions', []):
    print(f'  {t[\"id\"]:4} → {t[\"to\"][\"name\"]}')"
```

Then apply the transition using the ID:

```bash
source ~/.zshenv && curl -s -o /dev/null -w "%{http_code}" \
  -u "${BITBUCKET_USERNAME}:${JIRA_API_TOKEN}" \
  -X POST "https://urukia.atlassian.net/rest/api/3/issue/REVUE-117/transitions" \
  -H "Content-Type: application/json" \
  -d '{"transition":{"id":"10111"}}'
```

A `204` response means success. Confirm to the user.

### 4. Create a ticket (e.g. `/jira-ticket create story: "summary" — description`)

**Use v2 — v3 returns 404 on POST.** Valid issue type IDs for REVUE: `10112` (Task), `10113` (Epic).

```bash
source ~/.zshenv && curl -s -X POST \
  -u "${JIRA_EMAIL}:${JIRA_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "fields": {
      "project": {"key": "REVUE"},
      "issuetype": {"id": "10112"},
      "summary": "Your summary here",
      "description": "Plain text description",
      "labels": ["tech-debt"]
    }
  }' \
  "https://urukia.atlassian.net/rest/api/2/issue" | python3 -c "
import json, sys
d = json.load(sys.stdin)
if 'key' in d:
    print(f'Created: {d[\"key\"]}')
    print(f'URL: https://urukia.atlassian.net/browse/{d[\"key\"]}')
else:
    print('ERROR:', d)
"
```

Notes:
- `description` is plain text in v2 (not Atlassian Document Format)
- `labels` is optional — use `["tech-debt"]` for debt items
- Omit `labels` if not needed

### 5. No argument

List all open REVUE tickets (To Do + In Progress), grouped by status, most recently updated first.
