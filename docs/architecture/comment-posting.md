# Comment Posting Architecture

**Status:** Accepted (Track 1 — typed pipeline)
**Decision date:** 2026-05-02
**Context:** Party mode design session — Daniel, Winston, John, Paige, Bob
**Supersedes:** ad-hoc `_build_merged_comment_body` in `cli.py` (REVUE-172 accretion); `dedup_consolidator.py` hunk-grouping bolt-on

---

## Scope

This document covers **every comment shape Revue can produce**: single (singleton finding), Nova-synthesised (multiple agents on the same issue), proximity-grouped (multiple distinct findings in the same diff hunk), prose-only, and with-suggestion (platform-native one-click code replacement).

The full matrix is deliberate. Scoping narrowly and extending later is precisely how the current accretion problem arose. This document pins the contract once, so future work argues only about behaviour within the contract, not about the shape of the contract itself.

---

## Framing: Two Levels of Pain

Revue's comment-posting layer has two distinct problems that are often conflated but require different responses.

**L1 — Code-design pain:** `cli.py` has grown to 1,000+ lines with two independent "merge" mechanisms bolted on at different times (see Background). There are no typed contracts between stages; attribution drops on grouped comments; the two merge paths can produce contradictory output. Solved by the SOLID refactor described in this document. No framework required.

**L2 — Orchestration pain:** Nova in single-shot mode cannot reason about how fixing finding A affects findings B and C. A senior teammate reviewing the same code would. This is a genuinely different problem — it requires multi-turn agent conversation, not cleaner data types. Solved by Track 2 (post-MVP conversational Nova). Framework required (LangGraph). Not solved here.

Both are real. They differ in scope, sequencing, and whether they block the current dogfood. This document addresses L1 entirely and creates the integration point for L2 (via `SynthesisStrategy`).

---

## Background: Two Accretion Mechanisms

The current code has two independent "merge" paths that were built at different times for different purposes:

| Mechanism | Location | Attribution today |
|-----------|----------|-------------------|
| Nova synthesis (same-issue multi-agent) | `core/dedup_consolidator.py` → `consolidate()` | ✅ Preserved via `synthesised_from` |
| AC10 hunk grouping (distinct findings, same hunk) | `cli.py` `_build_merged_comment_body` | ❌ Dropped since REVUE-172 |

These are not the same operation. Nova synthesis resolves *conflict* (multiple agents, same issue); hunk grouping resolves *proximity* (different issues, nearby lines). Treating them as the same kind of operation is the root cause of the attribution drop and the missing-suggestion bug.

**Observed regressions (MR !22, `feat/REVUE-202-line-resolver`):**

1. Multi-finding grouped comments: no agent attribution
2. Multi-finding grouped comments: no `code_replacement` block, even when underlying findings carry one
3. No proximity bound on hunk grouping: a new file (one big hunk) collapsed 8 findings spread over 130 lines into a single comment
4. Anchor refinement (Fix 2 of REVUE-202) moves the anchor to wherever the chosen suggestion's snippet matches — visually displacing prose findings from their actual lines
5. Some agent outputs are no-op (`code_replacement` equals existing lines minus diff sigils) — separate concern tracked via agent-prompt tightening

---

## Target Architecture

```
Agents → list[AgentFinding]
   ↓
Consolidator
   ├── Pass A (deterministic): GroupingStrategy → list[SynthesisGroup]
   └── Pass B (LLM, single batch): SynthesisStrategy → list[ConsolidatedFinding]
         ↓
   FindingPostProcessor chain → list[ConsolidatedFinding] (filtered/transformed)
   ↓
BodyBuilder (per-platform; pure function, kind-switched on finding type)
   ↓
Poster (resolves position, posts via VCSAdapter, deduplicates against existing)
```

**Module map:**

| Module | Responsibility |
|--------|---------------|
| `comments/models.py` | Typed contracts: `AgentFinding`, `SynthesisGroup`, `ConsolidatedFinding` |
| `comments/consolidator.py` | Orchestrates Pass A + Pass B + post-processor chain |
| `comments/body_builder.py` | Pure rendering: typed finding → platform comment body |
| `comments/poster.py` | I/O only: position resolution + `VCSAdapter` call + dedup against existing |

`cli.py` becomes orchestration only (CLI argument parsing + pipeline wiring).

---

## Decision 1 — Single Consolidator replaces two accretion mechanisms

### Context

`dedup_consolidator.py` and `cli.py:_build_merged_comment_body` both perform "merge" operations but on different axes (same-issue vs proximity) with different contracts (Nova JSON vs raw dicts). Having two paths makes it impossible to enforce attribution as a typed invariant — each path can independently drop it.

### Decision

Retire both paths into a single `Consolidator` module with two explicit, sequenced passes:

- **Pass A (deterministic):** `GroupingStrategy` clusters `AgentFinding` items into `SynthesisGroup` lists. No LLM. Output: structured groups ready for synthesis.
- **Pass B (LLM, single batched call):** `SynthesisStrategy` receives all non-singleton groups in one batch. Produces `ConsolidatedFinding` with required attribution, anchor, and (where applicable) unified `code_replacement`. Singletons pass through unchanged.

`attribution` is a **required field** of `ConsolidatedFinding`. The regressions in MR !22 become structurally impossible — the pipeline cannot produce a comment without attribution because the typed contract won't allow it.

### Consequences

- `dedup_consolidator.py` logic migrates into `NovaSingleShotStrategy` (the first `SynthesisStrategy` implementation). Existing unit tests migrate with it.
- `_build_merged_comment_body` in `cli.py` is deleted. Its proximity-grouping logic migrates to `ProximityAndCountGroupingStrategy`.
- `cli.py` loses two large private methods. A follow-up cleanup PR (PR 5) removes any dead code that remains.
- `ConsolidatedFinding` is the typed handshake between `Consolidator` and `BodyBuilder`. Passing raw dicts across this boundary is prohibited.

---

## Decision 2 — Pass A grouping rule: Option C (proximity AND count, both bounded)

### Context

The original AC10 grouping rule was unbounded: findings in the same hunk were grouped regardless of how far apart they were or how many there were. MR !22 showed the failure mode: a new file (one large hunk) collapsed 8 findings spread over 130 lines into a single comment, making none of them actionable.

### Decision

A `SynthesisGroup` requires **both** conditions:

- `line_distance ≤ N` (default **N = 3**)
- `group_size ≤ K` (default **K = 3**)

Findings that exceed either threshold become separate singleton groups and are posted as individual comments.

Defaults are conservative starting values. They will be tuned post-launch using telemetry (`unanchored_finding_count`, per-group size distribution). The *shape* of the rule is locked; the numbers are not.

Both thresholds are configurable via `.revue.yml`:

```yaml
consolidation:
  proximity_lines: 3   # N — max line distance for grouping
  max_group_size: 3    # K — max findings per group
```

**What this directly fixes:** MR !22 `line_resolver.py:18` 8-finding collapse. Findings at distances 47/87/94/114/129 stay as separate comments. The AC10 case (lines 12 and 14, distance 2) still groups correctly.

### Consequences

- The grouping rule is encoded as a `GroupingStrategy` Protocol so future strategies (semantic, LLM-assisted) plug in without touching `Consolidator`. `ProximityAndCountGroupingStrategy(n=3, k=3)` is the MVP implementation.
- `.revue.yml` schema reference (`docs/guides/revue-yml-reference.md`) must be updated with the `consolidation:` stanza.
- Unit tests parameterise both N and K to confirm boundary behaviour.

---

## Decision 3 — Pass B synthesis: Nova single-shot option β

### Context

Three options were considered for Pass B:

- **α (deterministic concatenation):** Concatenate finding prose with attribution headers. No LLM. Cheap but produces numbered-list comments, not coherent paragraphs. Cannot produce a unified `code_replacement` covering the whole group.
- **β (single-shot Nova, current choice):** Nova receives all non-singleton groups in one batched prompt. Produces synthesised prose + one unified `code_replacement` per group (where applicable). Reuses the existing Nova batch call from `consolidation.md` with an expanded prompt.
- **γ (conversational Nova, post-MVP):** Nova proposes a fix, queries contributing agents iteratively, converges. Requires LangGraph. Out of scope for MVP.

### Decision

Implement option **β**. Reasons:

1. **C1 alignment.** A "senior teammate" review is one coherent paragraph, not a numbered list. β produces that; α cannot.
2. **One-click apply uniformity.** β yields one unified `code_replacement` per group; α can only surface one finding's replacement at random, or none. PRD §3.2's MVP goal of platform-native one-click suggestions requires β.
3. **No extra LLM cost.** Nova already batches all groups into one call (see `consolidation.md`). Pass B reuses that call with an expanded prompt — no additional API request.
4. **Determinism risk is bounded.** If Nova fails (network, JSON parse error, missing groups), each affected group falls back to deterministic concatenation with attribution (option α). The fallback is already implemented and exercised.

### Consequences

- `NovaSingleShotStrategy` wraps the existing `consolidate()` logic from `dedup_consolidator.py`. The Nova prompt is extended to handle proximity groups as well as same-line conflict groups.
- The fallback path (α-equivalent concatenation) is retained as the error handler inside `NovaSingleShotStrategy`, not as a separate strategy. Callers always receive `ConsolidatedFinding`; they cannot observe which path was taken.
- Future option γ slots in as a new `SynthesisStrategy` implementation (see Decision 4).

---

## Decision 4 — Three pluggable strategy interfaces (GroupingStrategy, SynthesisStrategy, FindingPostProcessor)

### Context

The Consolidator has three distinct extension axes: *how* findings are clustered (Pass A), *how* clusters are synthesised (Pass B), and *what validation/transformation* is applied to the output (post-processing). These axes are independently variable — e.g., a future semantic grouper can pair with the current single-shot synthesiser, or the current proximity grouper can pair with a future conversational synthesiser. Hard-coding any of these would require `Consolidator` changes every time a new combination is tried.

### Decision

All three extension surfaces use the Strategy/Protocol pattern:

```python
class GroupingStrategy(Protocol):
    """Pass A: cluster raw agent findings into Synthesis Groups."""
    def group(self, findings: list[AgentFinding]) -> list[list[AgentFinding]]: ...

class SynthesisStrategy(Protocol):
    """Pass B: synthesise a Synthesis Group into a ConsolidatedFinding."""
    def synthesise(self, group: list[AgentFinding]) -> ConsolidatedFinding: ...

class FindingPostProcessor(Protocol):
    """Transform or validate a ConsolidatedFinding.

    Return None to drop the finding entirely;
    return the (possibly modified) finding to keep it.
    """
    def process(self, finding: ConsolidatedFinding) -> ConsolidatedFinding | None: ...

class Consolidator:
    def __init__(
        self,
        grouping: GroupingStrategy,
        synthesis: SynthesisStrategy,
        post_processors: list[FindingPostProcessor] = (),
    ): ...
```

**Today's injection:**
```python
Consolidator(
    grouping=ProximityAndCountGroupingStrategy(n=3, k=3),
    synthesis=NovaSingleShotStrategy(nova_client),
    post_processors=[NoOpSuggestionDropper(), UnanchoredFindingExtractor(summary_sink)],
)
```

**Future extension matrix** (no Consolidator changes needed):

| Grouping → \ Synthesis ↓ | Single-shot Nova (today) | Conversational Nova (Track 2) |
|---|---|---|
| Proximity + count (today) | MVP shipping shape | Track 2 default |
| Semantic / LLM-assisted (future) | Future opt-in | Full agentic vision |

**Track 2 is post-MVP.** The `SynthesisStrategy` Protocol is the integration point. `LangGraphConversationalStrategy` will implement it without any further Consolidator refactoring. See `agentic-review-loop.md §Track 2 Integration Point` for the plug-in shape.

### Consequences

- All three Protocols live in `comments/models.py` alongside the data types.
- `Consolidator.__init__` uses constructor injection (consistent with project-wide DI pattern; CLAUDE.md §Architecture rules).
- Each strategy is independently unit-testable with a stub partner.
- Adding a new validator (e.g. `HallucinationDetector`, `SeverityNormaliser`) never touches `Consolidator` code — it is appended to the `post_processors` list at the call site.

---

## Decision 5 — No-op suggestion detection runs in the Consolidator

### Context

Agent outputs occasionally produce a `code_replacement` that is the snippet stripped of leading `+`/`-`/space diff sigils — i.e. applying the suggestion changes nothing. Posting this as a platform-native suggestion misleads the developer (the "apply suggestion" button does nothing visible) and wastes the platform's suggestion quota.

### Decision

A `FindingPostProcessor` called `NoOpSuggestionDropper` runs in the post-processor chain. When a finding's `code_replacement`, after stripping diff sigils from each line, equals the finding's `snippet`, the processor sets `code_replacement = None`. The comment is then rendered as prose-only. The finding itself is preserved — the *suggestion* is dropped, not the finding.

This detection is **belt-and-braces**: it runs alongside future agent-prompt tightening (a separate ticket; see agent-output-contract.md once drafted) which will prevent no-op suggestions from reaching the Consolidator in the first place. The Consolidator check remains regardless — defence in depth.

### Consequences

- `NoOpSuggestionDropper` is the first item in the default `post_processors` list.
- Agent-prompt tightening is a separate concern and is not in Track 1's scope.
- Metric `noop_suggestion_dropped_count` is added to the per-run metrics output (`.revue/metrics.jsonl`, consistent with `pipeline-metrics.md` ADR D6 and the `REVUE_METRICS_ENABLED` flag).

---

## Decision 6 — Unanchored findings demote to the PR-level summary comment

### Context

A finding with neither `snippet` nor `code_replacement` has no verifiable anchor evidence. Posting it as an inline comment anchored at the agent-reported line misleads the developer — the line number is the agent's guess, not a verified match. The reader cannot distinguish "Revue verified this location" from "this is an approximate hint."

### Decision

An `UnanchoredFindingExtractor` `FindingPostProcessor` returns `None` from `process()` (removing the finding from the inline stream) and accumulates the dropped findings into a `summary_sink`. The `BodyBuilder` reads the sink when building the PR-level summary comment and appends:

```
### Findings without anchor evidence

*Maya · Code Quality* — In `cli.py` (~line 1053): <issue prose>
*Maya · Code Quality* — In `cli.py` (~line 1073): <issue prose>
```

The `(~line N)` notation makes clear the line number is the agent's estimate, not a verified anchor.

This is **transitional behaviour**. Once agent prompts are tightened to require `snippet`, this section empties naturally. The metric `unanchored_finding_count` tracks progress; a sustained count of zero signals the transitional section can be removed.

### Consequences

- `UnanchoredFindingExtractor` must run *after* `NoOpSuggestionDropper` in the post-processor chain (a finding with only a no-op `code_replacement` and no `snippet` is unanchored after the dropper runs).
- `summary_sink` is injected into `UnanchoredFindingExtractor` at the call site; `BodyBuilder` holds a reference to the same sink.
- `unanchored_finding_count` added to per-run metrics (same flags as Decision 5).

---

## Out of Scope

| Item | Reason |
|------|--------|
| Track 2 conversational Nova (`LangGraphConversationalStrategy`) | Post-MVP. Interface (`SynthesisStrategy`) is defined here; implementation is not. See `agentic-review-loop.md §Track 2 Integration Point`. |
| Agent-prompt tightening to prevent no-op suggestions upstream | Separate ticket. Decision 5 provides the safety net. |
| Semantic / LLM-assisted grouping strategy | Future opt-in via `GroupingStrategy` interface. |
| Hallucination detection, severity normalisation, content scrubbing | Future `FindingPostProcessor` implementations. |
| LangGraph as a dependency | Not added until Track 2. |
| Per-file diff scoping changes | Handled in REVUE-202; separate from this refactor. |

---

## Affected Files

| File | Change |
|------|--------|
| `src/revue/comments/models.py` | New — `AgentFinding`, `SynthesisGroup`, `ConsolidatedFinding` dataclasses; `GroupingStrategy`, `SynthesisStrategy`, `FindingPostProcessor` Protocols |
| `src/revue/comments/consolidator.py` | New — `Consolidator`, `ProximityAndCountGroupingStrategy`, `NovaSingleShotStrategy`, `NoOpSuggestionDropper`, `UnanchoredFindingExtractor` |
| `src/revue/comments/body_builder.py` | New — pure rendering; per-platform kind-switching; reads `summary_sink` |
| `src/revue/comments/poster.py` | New — position resolution, `VCSAdapter` call, dedup against existing comments |
| `src/revue/core/dedup_consolidator.py` | Logic migrates to `NovaSingleShotStrategy`; file deleted or kept as thin shim during migration |
| `src/revue/cli.py` | `_build_merged_comment_body` deleted; posting/rendering calls replaced with `BodyBuilder`/`Poster` calls; target ~400–500 lines (orchestration only) |
| `docs/guides/revue-yml-reference.md` | Add `consolidation:` stanza (`proximity_lines`, `max_group_size`) |

---

## References

- [Positioning Architecture](positioning.md) — full data flow diagram from diff ingestion through agents to platform API call; per-platform PositionAdapter design; AC7 gap
- [Nova Consolidation Architecture](consolidation.md) — implementation detail of `NovaSingleShotStrategy`; Nova's TOML/JSON batch prompt format
- [Configurable Comment Vocabulary](configurable-comment-labels.md) — proposed display-only overrides for the deterministic `Action` / `Suggest` / `Note` labels
- [Agentic Review Loop](agentic-review-loop.md) — Track 2 plug-in shape; `LangGraphConversationalStrategy` integration point
- [Comment Posting Refactor Plan](../planning/comment-posting-refactor-plan.md) — PR sequence and Jira ticket structure for Track 1 delivery
- [Pipeline Metrics ADR](pipeline-metrics.md) — `REVUE_METRICS_ENABLED` flag; `JsonlMetricsCollector` format

### Reconciled UX rationale

The earlier UX proposal used a separate `code_suggestion` field and formatter in
`cli.py`. The implemented contract is `code_replacement` on typed findings, rendered by
`BodyBuilder` through the platform formatter registry. The product rationale remains:
GitHub and GitLab should expose a native one-click suggestion when a complete replacement
is available, while Bitbucket must degrade to readable code without broken suggestion
syntax. A missing or rejected replacement always leaves the prose finding intact.
