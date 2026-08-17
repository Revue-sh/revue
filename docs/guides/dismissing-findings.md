# Dismissing Findings

When Revue posts a finding you disagree with, you can dismiss it by replying directly to the comment thread. No commands, labels, or special syntax are needed — just write a plain reply explaining your reasoning.

Revue reads developer replies at the start of each review cycle and uses them to update its own behavior going forward.

---

## How to dismiss a finding

Reply to the Revue comment thread in your PR/MR with:

1. **An acknowledgment** — confirm you've read the finding.
2. **A stated reason** — explain why the code is acceptable (or why it must never appear again).

Both parts are required. A reply like "this is fine" without a reason will prompt Revue to ask for one.

**Examples of valid dismissals:**

> This is intentional — we use a raw SQL query here because the ORM doesn't support the window function we need. The query is parameterised, so there's no injection risk.

> We've decided never to use `eval()` in this codebase, even in test helpers. Please keep flagging this pattern.

---

## What Revue does next

Once a valid reply is detected, Revue:

1. **Posts an acknowledgment reply** in the thread, noting the decision.
2. **Opens a lessons PR** that updates `.revue.yml` with the pattern — either to the `allowed_patterns` list (so it's never flagged again) or to `disallowed_patterns` (so it's always flagged).
3. **Closes/resolves the thread** on the platform, marking the conversation as done.
4. **Skips the thread on future runs** — Revue will not re-post to a thread it has already acknowledged.

---

## Decision outcomes

| Outcome | What it means | What Revue does |
|---|---|---|
| **allowed_pattern** | Developer provided a valid reason why this code is acceptable | Closes thread, opens lessons PR to suppress future findings for this pattern |
| **disallowed_pattern** | Developer confirmed this pattern must always be flagged | Closes thread, opens lessons PR to enforce the pattern as a hard rule |
| **reason_missing** | Reply acknowledged the finding but did not include a reason | Replies asking for the rationale — thread stays open |
| **not_acknowledged** | Reply did not address the finding | Reaffirms the finding — thread stays open |

---

## Idempotency — no double replies

Revue will never post twice to the same thread. After it has acknowledged a dismissal, subsequent pipeline runs skip that thread automatically.

This is handled entirely through Revue's own reply detection — no hidden markers or special comment syntax are embedded in threads. All Revue needs to identify a thread it has already handled is its standard finding comment format and the inline position metadata provided by the platform.

---

## Editing the patterns file yourself

If you prefer to manage patterns directly rather than waiting for a lessons PR, you can add or remove entries in `.revue.yml` under `noise_filters`:

```yaml
noise_filters:
  allowed_patterns:
    - pattern: "Raw SQL for window function queries"
      rationale: "Parameterised queries — no injection risk; ORM limitation"
  disallowed_patterns:
    - pattern: "Use of eval() anywhere in the codebase"
      rationale: "Security policy — flagged regardless of context"
```

See [Configuration Reference](configuration.md) for the full schema.
