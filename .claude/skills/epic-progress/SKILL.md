---
name: epic-progress
model: haiku
description: Render an epic progress recap (green-bar tally of Done/Active children) for a Jira epic. Accepts a ticket key or epic key — resolves to parent epic automatically. Use when the user says "epic progress", "epic recap", or when a Jira ticket completes.
allowed-tools: Bash, Read
---

Render a green-bar epic progress recap for a Jira epic.

## Invocation

```bash
bash .claude/skills/epic-progress/scripts/recap.sh <KEY>
```

`<KEY>` is any Jira issue key — an Epic, or any ticket whose parent is an Epic. The script resolves to the parent epic automatically.

## Output

```
Epic: [<EPIC-KEY>] <Epic Name>
[███░░░░░░░░░░░░░░░░░] <done>/<active> tickets (<pct>%)
```

If any children are Rejected or Cancelled, a second line lists them:

```
Excluded: REVUE-311, REVUE-312 (rejected/cancelled)
```

Print the recap verbatim — no preamble, no commentary, no extra formatting.

## Rules

- `done` = children with status ∈ {Done, Closed}
- `active` = total children − Rejected − Cancelled (the denominator)
- `pct` = `round(done / active * 100)`
- Bar: 20 cells. Filled = `floor(done / active * 20)` 🟩, rest ⬜.

## Errors

Exit 1 with `error: <KEY> has no parent epic` if the input is neither an Epic nor a ticket with a parent Epic.

## Dependencies

Reuses `.claude/skills/jira-ticket/scripts/jira_fetch.sh` and `.claude/skills/jira-ticket/scripts/jira_search.sh`. Does not hand-roll JQL or REST calls.
