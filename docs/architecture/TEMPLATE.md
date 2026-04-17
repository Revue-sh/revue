# [Decision title — noun phrase, e.g. "Streaming Output for Large Diffs"]

**Status:** Proposed
**Updated:** YYYY-MM-DD

---

## Problem

What is broken, missing, or sub-optimal? Include evidence (metrics, error logs, billing data) where available. State the two or three root causes — not just symptoms.

---

## Decision

### D1 — [Short label]

Describe the change. Include a before/after code or structure example if it helps.

> **Implementation note**: Any API contracts, SDK version requirements, or flags that must be verified before shipping.

### D2 — [Short label] *(if applicable)*

...

### D3 — [Short label] *(if applicable)*

...

---

## Out of scope

List related approaches that were explicitly considered and rejected, with a one-sentence reason for each. This prevents revisiting closed questions.

---

## Expected impact

| Metric | Current | After |
|--------|---------|-------|
| [Metric 1] | [value] | [value] |
| [Metric 2] | [value] | [value] |

Qualify any estimates: what conditions must hold for the improvement to materialise?

---

## Affected files

| File | Change |
|------|--------|
| `path/to/file.py` | What changes and why |

---

## Consequences

- **[Concern 1]**: What could go wrong or needs attention.
- **[Concern 2]**: Backward compatibility, cost, quality, or testing implications.

---

## Review Notes

*Populated during the Proposed phase. Add your name, date, and comment. Remove resolved items before moving to Accepted.*

<!--
Example:
- **2026-04-15 (Alice)**: D2 assumes the 1-hour cache tier is available — verify the type string before shipping.
  → **Resolved**: Confirmed as "persistent"; implementation note updated.
-->
