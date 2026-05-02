# Agentic PR Review Loop — Post-MVP Architecture

**Status:** Proposed (post-MVP)
**Decision date:** 2026-04-05
**Context:** Party mode discussion — Winston, Barry, John

---

## Overview

The current Revue pipeline is a **single-pass DAG**: diff in → agents run in parallel → Nova consolidates → comments posted → done. There is no iteration, no resolution, and no feedback loop.

This document describes the **Agentic PR Review Loop** — a post-MVP architecture that adds iterative resolution and a round-based feedback cycle. The key new capability: Revue not only *finds* problems, it *resolves* them and *verifies* the resolution.

---

## The Gap

| Capability | Current (MVP) | Post-MVP Loop |
|---|---|---|
| Multi-agent parallel review | ✅ | ✅ |
| Findings posted as inline comments | ✅ | ✅ |
| AI Resolver triages and pushes fixes | ❌ | ✅ |
| Iterative re-review after resolution | ❌ | ✅ (max 2 rounds) |
| Final clean-state assessment | ❌ | ✅ |
| Human escalation based on remaining severity | ❌ | ✅ |

---

## Target Architecture

```
PR Opened
  → AI Persona Review (parallel: Security, Architecture, Performance, Quality, API)
      → Findings with severity
  → AI Resolver
      → Triage: fix / won't fix / defer
      → Push code fixes back to PR (for "fix" decisions)
  → Round limit check (max 2 rounds)
      → No: loop back to AI Persona Review
      → Yes: Final AI Review
  → Final AI Review (clean assessment of current state)
      → Accept          (no findings remain)
      → Accept w/ notes (LOW severity only)
      → Escalate human  (MEDIUM/HIGH remain)
```

The loop runs **autonomously** — no human involvement until escalation. 5 AI personas, 1 moderator (Cleo), 1 resolver.

---

## Framework Recommendation: LangGraph

The loop introduces **stateful, conditional branching** — a `while findings_remain and round < 2` construct where agents make decisions at each branch. This is the threshold where a framework earns its place.

### Options evaluated

| Framework | Verdict |
|---|---|
| **LangGraph** (LangChain) | ✅ Recommended |
| CrewAI | ❌ Opinionated about agent roles/crews — maps poorly to Revue's named-agent model (Cleo, Nova, Zara, etc.) |
| AutoGen | ❌ Designed for conversational back-and-forth, not structured review pipelines |

### Why LangGraph

- Models the loop as a **directed graph with conditional edges** — nodes are agents/steps, edges are the branching conditions
- Handles **state persistence across rounds** natively (round counter, findings from prior pass, resolver decisions)
- Doesn't replace the existing agents — `pipeline.py` becomes the graph's backbone, existing agents remain as-is
- Tool-calling infrastructure is built-in — the Resolver calling GitHub API to push a fix, tracking what changed, passing context to the next round

---

## New Components Required

### AI Resolver

A new agent responsible for acting on findings, not just reporting them.

**Responsibilities:**
- Receive findings from the review round
- Triage each: `fix` / `won't fix` / `defer`
- For `fix` decisions: generate and push a code change back to the PR branch
- Produce a resolution summary for the next review round's context

**Placement in existing architecture:**
```
NovaConsolidator → findings → [NEW] AIResolver → resolution summary + code push
                                              → loop back to AgentRunner (if round < 2)
                                              → or → FinalReview
```

### Round State

State that must persist across loop iterations:
- `round_number` (0-indexed, max 2)
- `findings_per_round[]` — findings from each completed round
- `resolver_decisions[]` — what the Resolver decided per finding
- `pushed_fixes[]` — commits pushed by the Resolver

### Final AI Review

A lightweight synthesis pass at loop exit (round limit reached). Unlike Nova (which consolidates multi-agent conflicts), Final Review produces a **clean-state assessment**:
- What was found
- What was resolved
- What remains and why
- Recommendation: Accept / Accept with notes / Escalate

---

## Fit with Existing Architecture

The post-MVP loop **wraps** the existing pipeline rather than replacing it. The seam is already clean:

- `NovaConsolidator` returns structured `Finding[]` with severity — the Resolver consumes this directly
- `CommentService` posts findings — the Final Review is just another posting call
- `CleoRouter` and the agent pool are unchanged
- `AIClient` abstraction handles all provider differences — the Resolver uses the same interface

The main addition is **loop orchestration state** that the current `ReviewPipeline` class doesn't hold. LangGraph's `StateGraph` replaces the linear `pipeline.run()` call; everything inside remains intact.

---

## Track 2 Integration Point

**Track 2** is the conversational Nova capability from the comment-posting redesign (C8/C9, 2026-05-02). It is post-MVP and plugs into the `Consolidator` via the `SynthesisStrategy` Protocol defined in `comments/models.py`. The agentic loop architecture this document describes is the **outer** loop (AI Resolver + round-based re-review); Track 2 is the **inner** loop (Nova negotiating with contributing agents within a single consolidation pass).

### The integration point

```python
class LangGraphConversationalStrategy:
    """SynthesisStrategy implementation for Track 2.

    Replaces NovaSingleShotStrategy as the injected synthesis strategy.
    No changes to Consolidator, BodyBuilder, or Poster required.
    """
    def synthesise(self, group: list[AgentFinding]) -> ConsolidatedFinding: ...
```

The `Consolidator` is unaware of which `SynthesisStrategy` it holds. Swapping from `NovaSingleShotStrategy` to `LangGraphConversationalStrategy` is a **one-line DI change** at the call site. No further refactoring is needed when Track 2 lands — the typed pipeline (Track 1) is the prerequisite, not a blocker.

### Interaction with the outer agentic loop

Track 2 and the outer agentic loop (AI Resolver) are independent post-MVP capabilities. They compose naturally:

- Track 2 produces higher-quality `ConsolidatedFinding` items (findings are cross-checked between agents before posting)
- The AI Resolver (this document) acts on those findings — triaging, proposing fixes, and passing context to the next review round
- LangGraph is the shared framework for both; the `StateGraph` that drives the outer loop can host the inner Track 2 conversation nodes without duplication

### What to build first

Build the outer loop (this document's AI Resolver + round state) first if the goal is autonomous resolution. Build Track 2 first if the goal is higher-quality single-pass findings. Either is valid; they are independent.

### References

- `docs/architecture/comment-posting.md §Decision 4` — `SynthesisStrategy` Protocol definition
- `docs/planning/post-mvp-ideas.md` — Track 2 entry with LangGraph notes

---

## What NOT to do before MVP

- Do not add the Resolver or loop logic to the current `pipeline.py`
- Do not introduce LangGraph as a dependency yet
- Do not design the current pipeline around the loop seam — it's already clean enough

The only useful pre-work: ensure `NovaConsolidator` returns structured `Finding[]` with severity (already the case) and that `CommentService` is injectable (already the case via DI).

---

## References

- [Nova Consolidation Architecture](consolidation.md) — upstream of the Resolver
- [Comment Resolution Architecture](../architecture-comment-resolution.md) — auto-resolve posted comments when fixes land
- [Post-MVP Ideas](../post-mvp-ideas.md) — related: auto-resolve Bitbucket comments
