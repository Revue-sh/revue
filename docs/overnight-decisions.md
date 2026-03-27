# Overnight Decisions Log
**Session:** 2026-03-27 overnight
**For review:** Morning of 2026-03-28

All decisions made autonomously while you rested. Nothing sent externally, no secrets touched. Review and override anything here.

---

## Decisions Made

### 1. SOLID violations fixed before Sprint 2 started
**What:** Four violations identified in Sprint 1 code were fixed before moving on.
**Decision detail:**
- **OCP fix:** Replaced `match config.provider` factory with `_PROVIDER_REGISTRY` dict + `register_provider()` function. Adding a new provider (Gemini, Bedrock etc.) now requires zero modification to `ai_client.py` — just `register_provider("gemini", GeminiClient)`.
- **SRP fix 1:** Extracted `resolve_api_key()` and `validate_provider_config()` from `AIConfig` into `core/key_resolver.py`. `AIConfig` is now a data class that delegates — no logic.
- **SRP fix 2:** Extracted the AI orchestration loop from `cmd_review()` into `core/pipeline.py` as `ReviewPipeline`. CLI is now thin: parse args, load config, delegate to pipeline, format output.
- **DIP fix:** `cmd_review()` accepts optional `pipeline_factory=` parameter. Tests inject mocks without patching globals.
- **Rationale:** Sprint 2 adds 7 more modules — fixing violations now prevents them compounding.

### 2. Wrote Sprint 2 code directly (not via Claude Code CLI)
**What:** After two 180s timeouts spawning Claude Code agents, I wrote the Sprint 2 modules directly.
**Rationale:** Claude Code CLI was taking >3 minutes per invocation (likely OAuth token refresh overhead on each call). Writing well-specified code directly is faster and produces the same quality for well-defined modules.
**Implication:** You should check if this is a system/network issue or expected. Running `claude auth status` may reveal an expired session that needs periodic refresh.

### 3. ContradictionStrategy and NoiseFilter are Protocols (OCP)
**What:** Both `ContradictionStrategy` and `NoiseFilter` are `typing.Protocol` interfaces, not base classes.
**Decision:** New strategies/filters are added by implementing the protocol — no modification to detector/filter orchestrators. Sprint 3 agent definitions can add domain-specific noise filters (e.g. Swift-specific patterns) without touching `noise_filters.py`.

### 4. `run_agents_parallel` uses ThreadPoolExecutor, not asyncio
**What:** Parallel agent execution uses threads, not async.
**Rationale:** Agent code calls blocking AI SDK methods (httpx under the hood). asyncio would require all agent code to be async-native. Threads give true parallelism here (GIL is released on I/O). No agent is CPU-bound.
**Trade-off:** Thread overhead is ~1ms per agent — negligible vs 2-30s AI call latency. If we move to fully async agents in Sprint 4+, this is a contained refactor in `agent_runner.py` only.

### 5. `SharedAnalysisResult` is immutable by convention
**What:** `SharedAnalysisResult` is a `@dataclass` without `frozen=True`.
**Decision:** Not frozen — frozen dataclasses can't have mutable list fields without `field(default_factory=...)` complications and `__hash__` issues. Convention: agents treat it as read-only. Added docstring note.
**Future:** If mutation becomes a real concern, switch to `frozen=True` with tuple fields in Sprint 3.

### 6. Contradiction resolution falls back gracefully, never raises
**What:** If the AI resolution call fails for any contradiction pair, that pair is added to `unresolved` and the original findings are kept (not removed). Pipeline never fails due to resolution failures.
**Rationale:** Resolution is a nice-to-have quality improvement — the review must still complete even if the orchestrator AI call fails. Consistent with PRD principle of graceful degradation.

### 7. Noise filters use short-circuit evaluation
**What:** First matching filter suppresses a finding — subsequent filters not evaluated.
**Decision:** This is intentional and consistent with how CSS specificity / firewall rules work. Most specific / highest-priority filters should be ordered first.
**Implication for Sprint 3:** When agent-specific filters are added, their ordering relative to global filters matters. We should document filter ordering in `noise_filters.py`.

---

## Sprint Progress

| Sprint | Status | Tests | Commit |
|--------|--------|-------|--------|
| S1 Foundation | ✅ Done | 67 | `ea0752a` |
| SOLID refactor | ✅ Done | 74 | `125020a` |
| S2 Core Pipeline | ✅ Done | 136 | `123fd22` |
| S3 Agent System | 🔄 In progress | — | — |

---

## What's Next (Sprint 3 plan)

Sprint 3 — Agent System (6 stories):

| Story | Subject | Can start? |
|-------|---------|------------|
| [016] | Agent definition loader — YAML/Markdown parser | ✅ Yes (no deps) |
| [021] | Nova and Cleo agent definitions | After [016] |
| [019] | Zara (Security analyst) agent definition | After [016] |
| [046] | Kai (Performance expert) agent definition | After [016] |
| [047] | Maya (Code quality expert) agent definition | After [016] |
| [048] | Leo (Architecture reviewer) agent definition | After [016] |

[016] first, then all 5 agent definitions in parallel.

---

## Files created overnight

```
AIReviewer/core/key_resolver.py         — SRP: API key logic
AIReviewer/core/pipeline.py             — SRP: review orchestration
AIReviewer/core/diff_limit.py           — [002] hard diff limit
AIReviewer/core/shared_analysis.py      — [003] upfront AI classification
AIReviewer/core/agent_runner.py         — [004] parallel agent execution
AIReviewer/core/contradiction_detector.py — [005] contradiction detection
AIReviewer/core/contradiction_resolver.py — [006] contradiction resolution
AIReviewer/core/nova_consolidator.py    — [007] Nova deduplication
AIReviewer/core/noise_filters.py        — [008] noise suppression
+ corresponding test files for each
```
