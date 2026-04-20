# Architecture Decision Records

This directory contains ADRs (Architecture Decision Records) for Revue. Each ADR captures a significant design decision: the problem it solves, the chosen approach, and the trade-offs accepted.

---

## When to write an ADR

Write an ADR when a decision:
- Affects multiple files or layers (CLI, Service, Repository, Infrastructure)
- Has non-obvious trade-offs worth preserving for future contributors
- Supersedes or contradicts an existing decision

Small, self-evident changes (renaming a variable, adding a config key) do not need an ADR.

---

## Status lifecycle

| Status | Meaning |
|--------|---------|
| **Proposed** | Open for comment. No implementation work should start until this moves to Accepted. |
| **Accepted** | Decision locked. The implementing Jira story should exist and be linked in the ADR. |
| **Implemented** | Code is merged. ADR is a historical record — do not edit the decision body. |
| **Superseded** | Replaced by a newer ADR. Link to the superseding document. |

A **Proposed** ADR is the RFC phase. Add feedback to its **Review Notes** section before it moves to Accepted.

---

## How to review a Proposed ADR

1. Read the Problem and Decision sections.
2. Add your comments to the **Review Notes** section at the bottom of the ADR file (include your name/date).
3. If the decision is blocking a story, raise it on the relevant Jira epic.
4. Once all open notes are resolved, update Status to **Accepted**.

---

## ADR index

| File | Status | Summary |
|------|--------|---------|
| [consolidation.md](consolidation.md) | Implemented | Nova batch synthesis — consolidating multi-agent findings into per-location PR comments in a single LLM call |
| [agentic-review-loop.md](agentic-review-loop.md) | Proposed (post-MVP) | Iterative agentic review loop with resolution and verification rounds |
| [prompt-cache-strategy.md](prompt-cache-strategy.md) | Accepted | Fix 2.7% Anthropic cache hit rate by moving the diff to a shared system block prefix |
| [pipeline-metrics.md](pipeline-metrics.md) | Proposed | Per-run JSONL metrics artifacts for cache observability and future dashboard; `MetricsCollector` Protocol injection |
| [system-context-injection.md](system-context-injection.md) | Proposed | Inject architecture docs and adjacent file contracts into agent prompts so agents can detect system-assumption errors, not just diff-level defects |
| [critical-path-escalation.md](critical-path-escalation.md) | Proposed | Declarative `critical_paths` and `escalation` config in `.revue.yml`; structured escalation comments when sensitive areas are touched or severity thresholds are exceeded |

---

## Starting a new ADR

Copy [TEMPLATE.md](TEMPLATE.md), rename it to describe the decision (e.g. `streaming-output.md`), and fill in every section. Leave **Review Notes** empty — it gets populated during the Proposed phase.
