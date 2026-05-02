# Post-MVP Enhancement Ideas

## Track 2 — Conversational Nova Synthesis (LangGraphConversationalStrategy)

**Priority:** Post-MVP
**Value:** Higher-quality consolidated findings; cross-finding coherence
**Effort:** Large (8–13 points; requires LangGraph dependency)
**Design reference:** `docs/architecture/comment-posting.md §Decision 4` (SynthesisStrategy interface), `docs/architecture/agentic-review-loop.md §Track 2 Integration Point`

**Problem:**
Nova in single-shot mode (Track 1 / MVP) synthesises a group of findings in one LLM call. It cannot reason about how fixing finding A affects findings B and C — a senior teammate reviewing the same hunk would. The result is sometimes internally contradictory: two suggestions in the same group point to the same line in opposite directions.

**Proposed Solution:**
Implement `LangGraphConversationalStrategy` as a second `SynthesisStrategy`. Nova proposes a unified fix → queries each contributing agent ("does your finding still apply? does this fix introduce new issues?") → iterates to convergence or a round cap. The Consolidator is unchanged; swapping strategies is a one-line DI change at the call site.

**Framework:** LangGraph (selected 2026-04-05; see `agentic-review-loop.md §Framework Recommendation`). CrewAI was evaluated and rejected (maps poorly to Revue's named-agent model). The Anthropic Agent SDK is off the table (PRD §1 demands provider neutrality).

**Pre-requisite:** Track 1 (typed pipeline with `SynthesisStrategy` Protocol) must ship first. Conversational synthesis cannot operate on dict-shaped payloads.

**Related:**
- Outer agentic loop (AI Resolver + round-based re-review) — independent post-MVP capability; both use LangGraph and compose naturally once both ship

---

## Auto-resolve Bitbucket Comments

**Priority:** Post-MVP  
**Value:** Developer experience improvement  
**Effort:** Medium (3-5 points)

**Problem:**
Currently, Revue posts inline comments for every finding but doesn't auto-resolve them when issues are fixed in subsequent commits. This creates noise in PRs with many findings (e.g., 105 comments in PR #22).

**Proposed Solution:**
- Track finding "fingerprints" (hash of file path + line + issue text)
- On subsequent review runs, compare new findings against previous comments
- Auto-resolve Bitbucket comments for findings that no longer appear
- Keep comments open for new/changed findings

**Implementation Notes:**
- Store comment IDs mapped to finding fingerprints (in-memory or lightweight cache)
- Use Bitbucket API: `PUT /repositories/{workspace}/{repo}/pullrequests/{pr_id}/comments/{comment_id}` with `{"resolved": true}`
- Handle edge cases: line number changes (use fuzzy matching), file renames

**Benefits:**
- Cleaner PR review experience
- Developers can focus on remaining/new issues
- Automatic confirmation that fixes worked

**Related:**
- Could extend to GitHub, GitLab platforms
- Could add comment thread tracking (replies, discussions)

**Epic Candidate:** Post-MVP Quality of Life Improvements
