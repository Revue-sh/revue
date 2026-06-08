# Nova Consolidation Architecture

**Status:** Implemented (per-group synthesis); Batch synthesis designed  
**Epic:** REVUE-87 (Developer Experience & Transparency)  
**Stories:** REVUE-95 (Orchestrator Transparency), REVUE-109 (Usage Tracking DI)  
**PR:** #39  
**Updated:** 2026-04-03

---

## Overview

Nova is the consolidation layer in the Revue review pipeline. It sits between the agent findings (produced by Maya, Zara, Kai, Leo, etc.) and the comment-posting step. Its job: reduce N agent findings into the smallest useful set of PR comments, one per code location.

This document covers the **Nova Batch Synthesis** design — the architecture for resolving multi-agent conflicts across an entire PR in a single LLM call.

---

## Problem

When multiple agents review the same PR, they frequently flag the same code location independently. Without consolidation, a developer sees N separate comments on the same line — one from the security agent, one from the architecture agent, one from the performance agent. This is noise, not signal. Worse, the findings often overlap or even contradict each other, and the developer has no unified recommendation.

**Example:** Two agents flag `src/revue/core/usage_tracker.py:18`:
- **Leo** (architecture): DIP violation — `TRACK_URL` at module level
- **Zara** (security): SSRF risk — unvalidated user-controlled URL

Without synthesis, the developer sees two comments and must mentally merge them. With synthesis, they see one: "Module-level `TRACK_URL` creates DIP violation and SSRF exposure. Accept `track_url` as optional param (DI). Validate HTTPS before use."

The synthesis is responsible for producing one coherent fix, not summarising each agent
in sequence. Attribution remains explicit so the author can see which review domains
contributed, and severity/confidence remain deterministic. This carries forward the
useful UX rationale from the superseded April 2026 `nova-synthesis-mode` proposal without
restoring its obsolete `cli.py` rendering or legacy model shapes.

---

## Design Decision

Nova collects ALL multi-agent conflict groups across the entire PR and resolves them in a **single LLM call** — not per-line.

This is a deliberate architectural choice. Per-line synthesis (one LLM call per conflict group) is simpler to implement but O(N) in cost and latency where N is the number of conflicting locations. Batch synthesis is O(1) per PR — one prompt, one response, one parse.

---

## Architecture

### Pipeline Flow

```
Agent findings (N)
  -> coordinate grouping by (file, line)
  -> singletons [groups of 1] -> pass through, zero LLM cost
  -> conflict groups [groups of 2+] -> batch TOML prompt
                                     -> ONE LLM call (Nova)
                                     -> JSON response array
                                     -> match by file:line key
  -> final findings = singletons + synthesised conflicts
  -> existing strategy dedup (secondary, content-based)
  -> post: one comment per (file, line)
```

### Detailed Steps

1. **Coordinate grouping.** All findings are bucketed by `(file_path, line_number)`. This is the primary deduplication key — it is exact, deterministic, and zero-cost.

2. **Singleton pass-through.** Groups containing exactly one finding are returned unchanged. No LLM call, no modification, no token cost. This is the common case — most lines are flagged by only one agent.

3. **Batch synthesis.** All conflict groups (2+ findings on the same line) are collected into a single TOML prompt. Nova makes one LLM call to synthesise all of them at once.

4. **Response matching.** The JSON response array is matched back to groups by `file` + `line` key (not array index — see design decisions below).

5. **Deterministic severity/confidence.** The LLM synthesises prose only. Severity is `max(group)` by severity order. Confidence is `max(group)`. These are never LLM-determined.

6. **Secondary dedup.** The merged set (singletons + synthesised) passes through pluggable deduplication strategies (`SimilarIssueStrategy`, etc.) for content-based near-duplicate removal.

7. **Final sort.** Critical > major > minor > suggestion, then by confidence descending.

---

## Prompt Design

### Input Format: TOML

The batch prompt encodes all conflict groups as TOML:

```toml
[[group]]
file = "src/revue/core/usage_tracker.py"
line = 18
[[group.finding]]
agent = "Leo"
severity = "MEDIUM"
issue = "DIP violation — TRACK_URL at module level"
suggestion = "Apply DI pattern: inject track_url param"
[[group.finding]]
agent = "Zara"
severity = "MEDIUM"
issue = "SSRF risk — unvalidated user-controlled URL"
suggestion = "Add HTTPS-only validation before use"
```

### Output Format: JSON

The response is a JSON array, one object per group, keyed by `file` + `line`:

```json
[
  {
    "file": "src/revue/core/usage_tracker.py",
    "line": 18,
    "issue": "Module-level TRACK_URL creates DIP violation and SSRF exposure",
    "suggestion": "Accept track_url as optional param (DI). Validate HTTPS before use. Eliminates module coupling and SSRF vector in one change."
  }
]
```

### Why TOML Input / JSON Output

| Choice | Reason |
|--------|--------|
| TOML input | Structured, clearly delimited groups. Human-readable in prompt context. Nested `[[group.finding]]` maps naturally to the data shape. Lower token count than JSON for the same data. |
| JSON output | Standard LLM output format. Parseable programmatically with `json.loads()`. LLMs produce valid JSON more reliably than valid TOML. |

The asymmetry is intentional: optimise input for the LLM to *read* (TOML is clearer), optimise output for the pipeline to *parse* (JSON is safer).

---

## Key Design Decisions

### Severity and Confidence Are Deterministic

The LLM synthesises prose (issue description, suggestion text). It never determines severity or confidence. These are computed deterministically:

- **Severity:** highest in the group by `_SEVERITY_ORDER` (critical > major > minor > suggestion > info)
- **Confidence:** `max()` across the group

**Why:** LLMs are unreliable at consistent severity classification. A synthesis that downgrades severity because the prose sounds less urgent would be a regression. Deterministic severity means the consolidation layer is auditable.

### Matching by File+Line Key, Not Array Index

The JSON response includes `file` and `line` fields. The pipeline matches synthesised results back to groups using this composite key, not by array position.

**Why:** LLMs may reorder array elements, skip groups, or insert extra entries. Positional matching is fragile. Key-based matching is resilient to reordering and allows graceful fallback for missing groups.

### Concatenation Fallback

If the LLM call fails (network error, malformed JSON, missing groups in response), the fallback is per-group concatenation with agent attribution:

```
*Leo:* DIP violation — TRACK_URL at module level
*Zara:* SSRF risk — unvalidated user-controlled URL
```

This is worse than synthesis but strictly better than posting N separate comments. The developer still sees one comment per line with all findings attributed.

**Fallback triggers:**
- LLM call raises an exception
- Response fails JSON parse
- Parsed response missing `issue` or `suggestion` fields
- Response group cannot be matched to input group by file+line key

### Batch Size Limit

`MAX_BATCH_SIZE = 50` groups per LLM call. PRs with more than 50 conflict groups are split into chunks, each producing a separate LLM call.

**Why 50:** Balances prompt size against LLM context limits. 50 groups with 2-3 findings each is roughly 3,000-5,000 tokens of input — well within context for all supported providers. Going higher risks truncation or degraded synthesis quality.

**Cost with chunking:** O(N/50) LLM calls. A PR with 200 conflict groups makes 4 calls. In practice, most PRs have fewer than 10 conflict groups, so this is almost always O(1).

---

## Cost Profile

| Scenario | Before (per-group) | After (batch) |
|----------|-------------------|---------------|
| PR with 1 conflict group | 1 LLM call | 1 LLM call |
| PR with 10 conflict groups | 10 LLM calls | 1 LLM call |
| PR with 100 conflict groups | 100 LLM calls | 2 LLM calls |
| PR with 0 conflict groups (all singletons) | 0 LLM calls | 0 LLM calls |

The common case (0-5 conflict groups) drops from 0-5 calls to 0-1 calls. The pathological case (large PRs with many conflicts) drops from O(N) to O(N/50).

---

## Tradeoffs

### Batch vs. Per-Group Synthesis

| | Batch (chosen) | Per-group |
|---|---|---|
| **LLM cost** | O(1) per PR | O(N) where N = conflict groups |
| **Latency** | One round-trip | N sequential round-trips (or parallel with rate-limit risk) |
| **Prompt complexity** | Higher — TOML encoding, array response parsing | Lower — single JSON object in/out |
| **Failure blast radius** | One failure loses all syntheses (mitigated by fallback) | One failure loses one synthesis |
| **Cross-group context** | LLM sees all conflicts, may produce more coherent PR-level advice | Each group synthesised in isolation |

The failure blast radius is the main downside. A single malformed response falls back to concatenation for *all* groups, not just one. This is acceptable because:
1. The fallback (attributed concatenation) is still better than N separate comments
2. JSON parse failures are rare with `temperature=0.2`
3. Key-based matching allows partial recovery — groups that *do* appear in the response are synthesised, only missing groups fall back

### TOML vs. Markdown for Prompt Input

Markdown was considered (agents already produce markdown-flavoured output). TOML was chosen because:
- Markdown groups would need custom delimiters (`---`, `## Group N`) that the LLM might echo back
- TOML `[[group]]` arrays have unambiguous boundaries
- TOML is ~15% fewer tokens than equivalent markdown for structured data

---

## Implementation

### Location

| File | Functions |
|------|-----------|
| `src/revue/core/dedup_consolidator.py` | `_batch_synthesise()`, `_build_batch_prompt()`, `_parse_batch_response()` |
| `src/revue/core/dedup_consolidator.py` | `_merge_group()` (per-group fallback), `_build_synthesis_prompt()` (per-group prompt) |
| `src/revue/core/dedup_consolidator.py` | `consolidate()` — public entry point, orchestrates grouping + synthesis + secondary dedup |
| `src/revue/core/pipeline.py` | Passes `ai_client` to `consolidate()` via dependency injection |

### Dependency Injection

The `consolidate()` function accepts an optional `ai_client: AIClient | None` parameter. When `None`, synthesis is skipped entirely (concatenation fallback for all groups). This keeps the consolidator testable without mocking LLM calls — tests that don't need synthesis simply omit `ai_client`.

The pipeline passes its existing `AIClient` instance through, so no new client is created for consolidation.

### Pluggable Deduplication (OCP)

Secondary deduplication strategies implement the `DeduplicationStrategy` protocol:

```python
class DeduplicationStrategy(Protocol):
    def are_duplicates(self, a: AIReview, b: AIReview) -> bool: ...
```

Built-in strategies:
- `SameFileLineStrategy` — exact file + line + severity match (redundant after coordinate grouping, but kept for safety on the merged set)
- `SimilarIssueStrategy` — same file, nearby line (within 3 lines), 60%+ word overlap in issue text

New strategies can be added without modifying `consolidate()`.

---

## References

- [REVUE-109](https://revue-io.atlassian.net/) — Usage tracking DI (motivated the `ai_client` injection pattern)
- [REVUE-95](docs/stories/REVUE-95.md) — Orchestrator transparency (upstream of consolidation)
- [Comment Resolution Architecture](docs/architecture-comment-resolution.md) — Downstream: how posted comments are tracked and auto-resolved
- PR #39 — Implementation pull request
