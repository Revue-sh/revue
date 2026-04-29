# Jira Epic Description Template

Epics use Jira wiki markup in their description field (not Markdown).
Copy the block below into the Jira description when creating an epic via API or UI.

---

```
h2. Background
<why this epic exists — problem or opportunity it addresses>

h2. Goal
<the business/user outcome in one sentence>

h2. Success Criteria
* <measurable outcome 1>
* <measurable outcome 2>

h2. Platform Scope (if applicable)
* Supported: <list>
* Out of scope: <list>

h2. Out of Scope
* <explicit boundary>

h2. Dependencies
* <what must be true before this epic can complete>

h2. Stories
* <filled in as stories are created — REVUE-XXX: summary>
```

---

## Creating an epic via CLI

```bash
bash .claude/skills/jira-ticket/scripts/jira_create.sh \
  "Epic summary" \
  "" \
  "" \
  "$(cat /tmp/epic-description.txt)" \
  10113
```

Issue type IDs for the REVUE project:
- `10112` — Task (default)
- `10113` — Epic
- `10114` — Subtask
