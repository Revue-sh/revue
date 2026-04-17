# Pipeline Run Metrics

**Status:** Proposed  
**Updated:** 2026-04-16

---

## Problem

The only way to verify that the prompt caching changes in `prompt-cache-strategy.md` are working is to read the Anthropic billing dashboard CSV — the same manual process that revealed the original 2.7% hit rate after 12 days. There is no in-run signal, no per-PR breakdown, and no programmatic query path.

Two compounding needs:

1. **Immediate**: validate that D1+D2 from `prompt-cache-strategy.md` improve cache hit rates — requires per-run token data queryable across runs.
2. **Future**: build a dashboard to visualise cost and cache effectiveness over time, fed by a persistent store of per-run artifacts.

The current codebase has no metrics layer. Adding ad-hoc logging to individual call sites would scatter the concern and make it impossible to swap the sink later.

---

## Decision

### D1 — `MetricsCollector` Protocol + Null Object injection

Introduce a `MetricsCollector` Protocol as the single abstraction all callers depend on. Concrete implementations are injected at construction time; callers never check for `None` or branch on provider.

```python
# src/revue/core/metrics.py

class MetricsCollector(Protocol):
    def record(self, event: MetricsEvent) -> None: ...
    def flush(self, run_id: str) -> None: ...

class NullMetricsCollector:
    """Default no-op — used when metrics are disabled or in tests."""
    def record(self, event: MetricsEvent) -> None: pass
    def flush(self, run_id: str) -> None: pass
```

`MetricsEvent` is a typed dataclass:

```python
@dataclass
class MetricsEvent:
    event_type: str          # "agent_call" — extensible for future event types
    timestamp: str           # ISO 8601
    agent_name: str | None
    provider: str
    model: str
    cache_creation_tokens: int
    cache_read_tokens: int
    input_tokens: int
    output_tokens: int
```

The `event_type` tag makes the schema forward-compatible: future events (`"comment_posted"`, `"nova_consolidation"`) slot in without breaking existing consumers.

**Test double** — tests that assert on metrics output pass a `CapturingMetricsCollector` directly:

```python
class CapturingMetricsCollector:
    def __init__(self): self.events: list[MetricsEvent] = []
    def record(self, event): self.events.append(event)
    def flush(self, run_id): pass
```

No mocking framework needed. LSP-compliant with `NullMetricsCollector`.

### D2 — Single construction point, shared instance

The collector is instantiated once in `ReviewPipeline.__init__()` (or its factory) and injected into every component that needs it:

```python
def _build_metrics_collector() -> MetricsCollector:
    if os.getenv("REVUE_METRICS_ENABLED"):
        return JsonlMetricsCollector()
    return NullMetricsCollector()
```

The same instance is passed to `AnthropicClient` (calls `record()` after each `complete()`) and held by the pipeline (calls `flush()` at the end of the run). No component creates its own collector.

`AnthropicClient` holds the collector as a constructor parameter (`metrics: MetricsCollector = NullMetricsCollector()`). The `AIClient` protocol signature is **unchanged** — this is an Anthropic-specific concern.

### D3 — JSONL artifact written at end of run

`JsonlMetricsCollector` accumulates events in memory during the run. When `flush()` is called at pipeline completion, it appends one JSON object to `.revue/metrics.jsonl`:

```json
{
  "run_id": "a3f2c1b4",
  "timestamp": "2026-04-16T09:14:00Z",
  "pr": "cbscd/revue#55",
  "provider": "anthropic",
  "model": "claude-sonnet-4-6",
  "agents": [
    {"name": "zara",  "cache_creation_tokens": 4231, "cache_read_tokens": 0,    "input_tokens": 4231, "output_tokens": 312},
    {"name": "kai",   "cache_creation_tokens": 0,    "cache_read_tokens": 4231, "input_tokens": 4231, "output_tokens": 287},
    {"name": "maya",  "cache_creation_tokens": 0,    "cache_read_tokens": 4231, "input_tokens": 4231, "output_tokens": 341},
    {"name": "leo",   "cache_creation_tokens": 0,    "cache_read_tokens": 4231, "input_tokens": 4231, "output_tokens": 298}
  ],
  "totals": {
    "cache_creation_tokens": 4231,
    "cache_read_tokens": 12693,
    "input_tokens": 16924,
    "output_tokens": 1238
  }
}
```

The artifact is written **after** the run completes — never before. Partial runs (failures mid-pipeline) do not produce an artifact. The JSONL format (one JSON object per line) allows `jq` queries across all runs without loading the full file.

### D4 — `--verbose` prints run totals to stdout

When `--verbose` is passed, the pipeline prints a summary of the `totals` block after `flush()`:

```
[revue] cache  write: 4,231 tokens  read: 12,693 tokens  (75% cache hit rate this run)
```

This is the only user-visible surface for metrics. It reads from the same in-memory accumulator as the JSONL writer — no second data path.

### D5 — Aggregation is a separate service (deferred)

The aggregation process (reading JSONL artifacts → DB) and any dashboard are explicitly out of scope for this ADR. Revue writes artifacts; it never reads them back or knows a DB exists. The aggregation service is a future standalone process, likely a separate repo. The schema in D3 is stable enough to build on — do not change it once artifacts exist in the field.

### D6 — Feature flag: `REVUE_METRICS_ENABLED` (internally undocumented)

Collection is off by default. Enable with:

```bash
export REVUE_METRICS_ENABLED=1
```

**Must not appear in any public-facing surface:**

| Surface | Status |
|---------|--------|
| `README.md` | Never document |
| `docs/revue-yml-reference.md` | Never document |
| `--help` / `--version` output | Never document |
| Example `.revue.yml` files | Never document |
| Website / marketing copy | Never document |
| Release notes / changelog | Never document |

**May appear in:**

| Surface | Status |
|---------|--------|
| This ADR | ✓ |
| `CLAUDE.md` under "Internal flags" | ✓ |
| Code comments (`# Internal only — see pipeline-metrics ADR`) | ✓ |

**Promotion path**: when the aggregation service and dashboard are production-ready, deprecate the env var and add a documented `metrics_enabled:` key to `.revue.yml`. Remove this note at that point.

---

## Out of scope

**OpenAI and other providers**: `AnthropicClient` is the only client with cache token fields in the API response. Other providers record zero values in the cache fields. Full multi-provider metrics is a follow-up.

**Aggregation service and DB**: Separate process, separate decision. Design it after one sprint of real JSONL data exists to validate the schema.

**Dashboard**: Follows the aggregation service. Not designed here.

**Nova, Cleo, shared_analysis events**: Only `agent_call` events from `AnthropicClient` are recorded in the MVP. Other pipeline stages (Nova consolidation, Cleo routing, shared analysis) are added in a follow-up story once the Protocol is in place.

---

## Expected impact

| Metric | Current | After D1–D6 |
|--------|---------|-------------|
| Cache hit visibility | Anthropic dashboard only | Per-run JSONL + `--verbose` |
| Time to detect cache regression | Days (next billing CSV) | Next run |
| Effort to query hit rate across N runs | Manual CSV | `jq` on `.revue/metrics.jsonl` |

---

## Affected files

| File | Change |
|------|--------|
| `src/revue/core/metrics.py` | New — `MetricsCollector` Protocol, `NullMetricsCollector`, `MetricsEvent`, `CapturingMetricsCollector` |
| `src/revue/infrastructure/metrics_writer.py` | New — `JsonlMetricsCollector` |
| `src/revue/core/ai_client.py` | Inject `MetricsCollector` into `AnthropicClient.__init__()`; call `record()` after each `complete()` |
| `src/revue/core/pipeline.py` | Inject `MetricsCollector`; call `flush()` at run completion; print `--verbose` summary |
| `CLAUDE.md` | Add "Internal flags" section documenting `REVUE_METRICS_ENABLED` |

---

## Consequences

- **`AIClient` protocol**: REVUE-155 (prerequisite for this story) changes `complete()` to return `CompletionResult(text, usage)`. The metrics implementation should read `cache_creation_tokens` and `cache_read_tokens` from `result.usage` rather than `resp.usage` directly. Implement REVUE-155 before REVUE-154.
- **No test changes for existing tests**: All existing tests implicitly use `NullMetricsCollector` via the default. Only new metrics-specific tests use `CapturingMetricsCollector`.
- **Schema stability**: Once `.revue/metrics.jsonl` exists in the field, the schema in D3 must not change without a migration. The `event_type` tag is the extension point.
- **Partial run artifacts**: Runs that fail before `flush()` is called produce no artifact. This is intentional — incomplete runs should not pollute the dataset.

---

## Review Notes

*Populated during the Proposed phase.*
