# Prompt Cache Strategy for Multi-Agent Pipeline

**Status:** Accepted  
**Updated:** 2026-04-16

---

## Problem

Anthropic billing data for April 2–14 shows a **2.7% cache hit rate**: 13.17M tokens written to the 5-minute cache, only 349K tokens read back. Cache writes account for 62.6% of total spend ($49.38 of $78.83). The cache is nearly useless.

Two compounding root causes explain this.

### Root cause 1 — Structural: no shared prefix across agents (intra-run)

In `LoadedAgent.analyse()` ([agent_loader.py:137–146](agent_loader.py)):

```python
raw = self._client.complete(
    [{"role": "user", "content": diff + instructions}],
    system=self._def.system_prompt,   # ← unique per agent
)
```

In `AnthropicClient.complete()` ([ai_client.py:298–309](ai_client.py)), the cache breakpoint is placed on the system block:

```python
kwargs["system"] = [
    {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
]
```

Anthropic prompt caching is **prefix-based**: a cache hit requires a subsequent request to begin with the exact same prefix as the cached one. The prefix is `[system block ... cache_control marker]`.

One review run produces:

| Agent | system block | user message |
|-------|-------------|-------------|
| Zara  | `[Zara security prompt]` ← cache key | `[diff]` |
| Kai   | `[Kai performance prompt]` ← cache key | `[diff]` |
| Maya  | `[Maya code-quality prompt]` ← cache key | `[diff]` |
| Leo   | `[Leo architecture prompt]` ← cache key | `[diff]` |

Four cache writes. Zero reads. The diff is identical across all four, but because the differing system block precedes it, each agent hashes to a completely different cache prefix. **Even with infinite TTL, intra-run cache sharing is impossible under the current structure.**

### Root cause 2 — TTL: 5 minutes is shorter than the developer re-review cycle

A second `revue` run on the same PR (same agent, same diff) would hit the cache — but only within 5 minutes. In practice the workflow is: run revue → read findings → fix code → run revue again. This takes more than 5 minutes. The cache has expired every time.

The billing CSV confirms: `usage_input_tokens_cache_write_1h` = 0 across all days. The Anthropic API supports a 1-hour cache tier that is entirely unused.

---

## Decision

### D1 — Invert the prompt structure (shared diff prefix)

Move the diff into the **system block first**, before the agent-specific instructions, and place the cache breakpoint after the diff. Put agent instructions after the breakpoint (uncached).

**Why system block over multi-turn**: A multi-turn structure (diff in first user message → prefilled assistant turn → agent instructions in second user turn) preserves message-role semantics but requires a fake assistant prefill and changes the `AIClient.complete()` call signature. The system block approach achieves the same prefix-sharing with no protocol change; the semantic tradeoff is acceptable and covered by regression testing (see Consequences).

**Complete structure after D1:**

```
system = [
  {"type": "text", "text": diff_text, "cache_control": {"type": "ephemeral"}},  ← cached prefix (shared across all agents)
  {"type": "text", "text": agent_system_prompt}   ← NOT cached (unique per agent)
]
messages = [
  {"role": "user", "content": shared_context + format_instructions}  ← NOT cached (varies per run)
]
```

**Where `shared_context` goes**: `LoadedAgent.analyse()` currently prepends `shared_context` (the `SharedAnalysisResult` text) to the user prompt. Because `shared_context` is the output of a non-deterministic LLM call (temperature 0.3), it is not byte-stable between re-reviews of the same PR. It cannot be part of the cached prefix. It remains in the user message alongside `format_instructions`.

**`ai_client.py` implementation conflict**: `AnthropicClient.complete()` currently auto-appends `cache_control` to the **last** block of the system list (`ai_client.py:304–307`). If D1 passes `[diff_block, agent_instructions_block]`, the current code would mark the agent instructions — the exact opposite of D1's intent. The implementation must:
1. Remove the auto-append behavior from `AnthropicClient.complete()`.
2. Require callers (`LoadedAgent.analyse()`, `run_shared_analysis()`) to explicitly mark the diff block with `cache_control` before passing the system list.

**Remove the user-message cache breakpoint**: `_anthropic_messages_with_cache()` currently adds a second `cache_control` breakpoint to the last user message. After D1, the user message contains `shared_context + format_instructions` — short, non-deterministic, and unique per run. Caching it produces 4 distinct cache writes per review with zero reuse. Remove the `_anthropic_messages_with_cache()` call for the Anthropic path; the only breakpoint needed is the diff block in the system list.

Now every agent on the same PR shares the same cached prefix (the diff). The first agent to complete its request writes the cache; subsequent agents — on any re-review run within the TTL window — read it.

Within a single parallel run, agents 2–4 still likely miss the cache on the first review (all start simultaneously before agent 1 writes). This is acceptable: the win comes from **re-reviews**, which is the dominant pattern in iterative development.

### D2 — Switch the diff cache breakpoint to the 1-hour tier

Use the 1-hour cache tier for the diff prefix. Re-reviews within an hour (the typical fix → re-review cycle) will hit the cache for all agents.

> **Implementation note**: The exact `cache_control` type string for the 1-hour tier must be verified against the current Anthropic SDK before implementation. The billing dashboard distinguishes `cache_write_5m` from `cache_write_1h` as separate columns, confirming the API supports both. The candidate value is `"persistent"`; verify before shipping.
>
> **D2 fallback**: D1 delivers cache hits independently of the TTL tier. If the 1-hour type string requires a beta header or SDK version not yet available, ship D1 with the existing `"ephemeral"` (5-minute) tier and track D2 as a follow-up. The write-count reduction comes from D1; D2 only extends the re-review window.

### D3 — Agent instructions stay uncached

Agent system prompts (Zara, Kai, Maya, Leo) are static files, but they are small (~600–1000 tokens each) and each is only called once per run. Adding a second cache breakpoint after the diff (to cache agent instructions on top of the diff prefix) would complicate the prefix structure with minimal benefit. Keep agent instructions uncached.

---

## Out of scope

**Sequential warm-up**: Firing one agent first to warm the cache before launching the others in parallel would guarantee intra-run cache hits. This adds a serial round-trip (5–10 s latency increase) for a benefit of 3 cache reads per review. The latency cost outweighs the savings at current agent counts. Revisit if agent count exceeds 6.

**Nova, Cleo, shared_analysis**: These also call `client.complete()`. Their prompts are structurally different (Nova receives all findings, Cleo receives the diff + metadata). Apply the same diff-as-shared-prefix pattern to `shared_analysis.py` first — it runs before all agents and uses the same diff. Nova and Cleo produce unique prompts per review; focus there is secondary.

**OpenAI provider**: OpenAI prompt caching is prefix-based and automatic (no `cache_control` needed). The `cache_key=diff_hash` already passed to OpenAI clients is the correct mechanism. No change needed for OpenAI.

**Feature flag**: D1 is a structural fix to the prompt construction, not a user-visible behaviour change. No feature flag is needed. At MVP stage the only users are internal — ship it clean.

**Cache metrics observability**: How cache hit rates are measured and stored is a separate architectural decision. See `pipeline-metrics.md`.

---

## Expected impact

| Metric | Current | After D1+D2 |
|--------|---------|-------------|
| Cache writes per review | 4 (one per agent) | 1 (shared diff) + 3 uncached agent prompt writes |
| Cache reads per re-review (< 1 hr) | ~0 | 4 (all agents read shared diff) |
| Input tokens billed on re-review | ~100% | diff tokens → cache read rate (~10% cost); agent instructions → full rate |
| Expected cache hit rate | 2.7% | >60% for re-reviews of the same diff within 1 hour |

Improvement is only realised on re-reviews of the **same diff** (no code changes pushed between runs) within the TTL window. If the user commits new changes before re-running, the diff content changes and the cache misses regardless of TTL. Practical hit scenarios: re-running after a transient failure, multiple reviewers running against the same PR state, or re-checking findings without pushing. First-run cache miss rate is unchanged.

---

## Affected files

| File | Change |
|------|--------|
| `src/revue/core/agent_loader.py` | Move `diff_text` to system block (first, with explicit `cache_control`); keep `shared_context + format_instructions` in user message |
| `src/revue/core/ai_client.py` | Remove auto-append of `cache_control` to last system block in `AnthropicClient.complete()` (lines 304–307); remove `_anthropic_messages_with_cache()` call (or gate on token threshold); update docstrings |
| `src/revue/core/shared_analysis.py` | Apply same diff-prefix pattern: move diff summary to system block first, instructions in user message |

---

## Consequences

- **Review quality**: Moving the diff to the system block changes the prompt layout. This must be regression-tested to verify agent output quality is unchanged. The LLM still receives the same content — only the message role/block structure changes.
- **Cache write cost**: 1-hour tier cache writes cost more per token than 5-minute. With the shared prefix strategy, we write once per run instead of four times, so the absolute write token count decreases even if the per-token rate is higher.
- **Custom agents**: Custom agent definitions loaded from `.revue/agents/` follow the same `LoadedAgent` path — they inherit the fix automatically.

---

## Review Notes

*2026-04-15 — six gaps found and resolved in the ADR before implementation begins.*

*2026-04-16 — party mode design session (Winston, Barry, Amelia, Daniel). Additional decisions:*

- **No feature flag for D1**: D1 is a structural fix, not a user-visible behaviour change. Ships unconditionally. Added to Out of scope.
  → **Resolved**: Out of scope section updated.

- **Quality regression test is a DoD item**: Amelia to run side-by-side findings comparison on three representative diffs (small/medium/large) as part of the implementing story. Replaces the idea of a slow-rolled feature flag validation.
  → **Resolved**: Captured here; must appear as explicit AC in the Jira story.

- **Metrics observability is a separate ADR**: How cache stats are collected, stored, and visualised is out of scope for this ADR. See `pipeline-metrics.md`.
  → **Resolved**: Out of scope section updated with reference.

- **`ai_client.py` implementation conflict**: Lines 304–307 auto-append `cache_control` to the last system block. Passing `[diff_block, agent_instructions_block]` without removing this behavior would mark the wrong block.
  → **Resolved**: Added explicit callout in D1 — auto-append must be removed; callers mark the diff block explicitly.

- **`shared_context` placement**: `LoadedAgent.analyse()` prepends `shared_context` (non-deterministic LLM output) to the user prompt. Its placement after D1 was unspecified.
  → **Resolved**: `shared_context` stays in the user message — it cannot be part of the stable cached prefix.

- **User-message cache breakpoint becomes noise**: After D1, `_anthropic_messages_with_cache()` would write 4 short, unique cache entries with zero reuse.
  → **Resolved**: Documented removal of the user-message breakpoint in D1.

- **Cache hit rate estimate overstated**: ">60% for re-reviews" implies any re-review hits the cache; it only applies when the diff is unchanged between runs.
  → **Resolved**: Impact table and footnote qualified to "re-reviews of the same diff."

- **D2 fallback missing**: If the 1-hour type string requires a beta header or SDK update, D2 had no fallback path.
  → **Resolved**: Added D2 fallback — ship D1 first with `"ephemeral"`; D2 is a follow-up. Named `"persistent"` as the candidate type string to verify.

- **System block choice not justified**: The decision to use the system block over a multi-turn structure was stated but not explained.
  → **Resolved**: Added one-sentence rationale to D1 (no protocol change, no fake assistant prefill).
