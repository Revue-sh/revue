# REVUE-115: Fix Anthropic Prompt Caching — top-level cache_control + observability

## User Story
As a developer running Revue on a PR with Anthropic, I want prompt caching to reliably activate
so that re-reviews consume cached tokens (0.1× TPM rate) instead of cold tokens — eliminating
the rate-limit pressure that previously caused 429 errors — and I want CI logs to confirm
whether caching is active so I never have to check a usage CSV to debug it again.

## Background

Commit `448a77e` added `cache_control: {"type": "ephemeral"}` per-block to `agent_loader.py`
and `shared_analysis.py`. Two problems were introduced:

**Problem 1 — Dead system breakpoint (wrong placement)**
Agent system prompts are ~400–600 tokens. Anthropic's minimum cacheable prefix is **1,024
tokens** (Sonnet 4.6) / 4,096 tokens (Haiku 4.5, Opus 4.6). The `cache_control` on the system
block is silently ignored — no error, no cache write, no `cache_write_5m` in the CSV.

**Problem 2 — Wrong layer (callers own what the client should own)**
The Anthropic-specific per-block structure was placed in `agent_loader.py` and
`shared_analysis.py`. This violates the layered architecture: caching is a transport concern
owned by `AnthropicClient`, not by callers. It also created divergent Anthropic/OpenAI code
paths in `shared_analysis.py` that should not exist.

**Problem 3 — Zero observability**
`AnthropicClient.complete()` discarded `resp.usage`. Cache write/read token counts were never
logged, making it impossible to confirm whether caching was active without downloading a
usage CSV.

**The correct approach (verified against SDK 0.87.0):**
`messages.create()` accepts `cache_control` as a **top-level parameter** (confirmed in
`inspect.signature`). The SDK auto-determines the last cacheable content block. This is the
approach recommended by the official Anthropic prompt caching cookbook:

```python
response = client.messages.create(
    model=MODEL_NAME,
    max_tokens=300,
    cache_control={"type": "ephemeral"},  # top-level — SDK handles breakpoint placement
    messages=[...]
)
```

This gives Anthropic Prompt Caching to every call made through `AnthropicClient` without
callers needing to know about it. Cache hits: 0.1× TPM rate, immediately solving the
4 agents × 8k tokens = 32k TPM > 30k limit problem for re-reviews.

**Note on parallel caching between agents (out of scope):**
Leo, Maya, Kai, and Zara have different system prompts → different cache key prefixes → no
shared cache hit between parallel agents on a single fresh review. Caching benefits re-reviews
(same diff, second run) where the full request is identical. A shared-base-prompt architecture
that enables parallel caching is a separate story if needed.

## Acceptance Criteria

1. **AC1 — Top-level cache_control in AnthropicClient:** `AnthropicClient.complete()` in
   `ai_client.py` passes `cache_control={"type": "ephemeral"}` as a top-level kwarg to
   `self._client.messages.create()`.

2. **AC2 — Callers cleaned up:** `agent_loader.py` `LoadedAgent.analyse()` no longer has an
   Anthropic-specific branch. System prompt and diff content are passed as plain strings/dicts
   with no `cache_control` keys anywhere in the call. The Anthropic and non-Anthropic paths
   are unified.

3. **AC3 — shared_analysis.py Anthropic branch removed:** `run_shared_analysis()` no longer
   has an `isinstance(client, AnthropicClient)` branch. One code path serves all providers.

4. **AC4 — Cache observability in logs:** `AnthropicClient.complete()` logs the cache token
   counts from `resp.usage` — specifically `cache_creation_input_tokens` and
   `cache_read_input_tokens` — at `DEBUG` level via the `logging` module, so CI logs show
   whether caching is writing or reading on each call.

5. **AC5 — No test regressions:** `cd src && PYTHONPATH=$(pwd) pytest revue/tests/ -q` passes
   with zero failures.

6. **AC6 — Updated unit tests:** Existing mock-based tests for `AnthropicClient` and
   `LoadedAgent` are updated to reflect the new call structure (no per-block `cache_control`
   in callers; `cache_control` in `messages.create()` kwargs).

## Test Cases

1. **TC1 — AnthropicClient passes top-level cache_control:**
   Mock `anthropic.Anthropic` and assert that the `messages.create()` call receives
   `cache_control={"type": "ephemeral"}` as a kwarg. Assert it is NOT present inside any
   content block of `system` or `messages`.

2. **TC2 — LoadedAgent.analyse() unified path:**
   Create a `LoadedAgent` backed by a mock `AnthropicClient`. Call `analyse()`. Assert the
   `system` kwarg passed to `complete()` is a plain string or a list of dicts with no
   `cache_control` key at any level. Assert `messages` content blocks likewise have no
   `cache_control` keys.

3. **TC3 — shared_analysis no Anthropic branch:**
   Call `run_shared_analysis()` with an `AnthropicClient` mock and with an `OpenAIClient`
   mock. Assert both code paths produce the same message structure (same call to
   `client.complete()` — no provider-specific divergence).

4. **TC4 — Cache logging does not raise:**
   Call `AnthropicClient.complete()` with a mock that returns a `Usage` object with
   `cache_creation_input_tokens=100` and `cache_read_input_tokens=0`. Assert no exception
   is raised and the debug log contains the expected values.

5. **TC5 — No regression:** Full suite passes (`673` tests expected green).

## Out of Scope

- Parallel caching between Leo/Maya/Kai/Zara on a single fresh review (requires shared base
  prompt architecture — separate story).
- Extended TTL (`"ttl": "1h"`) — 5-minute window covers typical re-review scenarios.
- OpenAI/Azure/OpenRouter changes — automatic prefix caching requires no code changes; placing
  static system content before variable user content (already done in `_openai_messages()`) is
  sufficient.
- Changing agent system prompt lengths to pad past the 1,024 token threshold.

## Dependencies

- Commit `448a77e` — context only. This story partially reverts and supersedes the caching
  approach in that commit. REVUE-110 does NOT need to be merged first; this story rides the
  same branch (`feat/REVUE-110-duplicate-comments-fix`).
- `anthropic` SDK ≥ 0.87.0 — **already installed** (confirmed via `pip show anthropic`).
  `messages.create()` top-level `cache_control` parameter verified present in 0.87.0.

## Notes — Key File Locations

| File | Lines affected |
|---|---|
| `src/revue/core/ai_client.py` | `AnthropicClient.complete()` — lines 208–221, add `cache_control` kwarg + usage logging |
| `src/revue/core/agent_loader.py` | `LoadedAgent.analyse()` — lines 104–141, remove Anthropic branch entirely |
| `src/revue/core/shared_analysis.py` | `run_shared_analysis()` — lines 240–266, remove Anthropic branch entirely |
| `src/revue/tests/core/` | Update relevant test mocks for `AnthropicClient.complete()` call shape |

## Implementation Approach

### 1. `src/revue/core/ai_client.py` — `AnthropicClient.complete()`

```python
def complete(self, messages, *, max_tokens=4096, temperature=0.3, system=None) -> str:
    def _call() -> str:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "cache_control": {"type": "ephemeral"},  # top-level; SDK ≥ 0.87.0
        }
        if system is not None:
            kwargs["system"] = system
        resp = self._client.messages.create(**kwargs)
        # Observability: log cache activity so CI logs confirm caching state
        usage = resp.usage
        _log.debug(
            "[anthropic] cache_creation=%s cache_read=%s input=%s output=%s",
            getattr(usage, "cache_creation_input_tokens", 0),
            getattr(usage, "cache_read_input_tokens", 0),
            usage.input_tokens,
            usage.output_tokens,
        )
        return resp.content[0].text
    return _with_retry(_call, max_attempts=self._max_attempts)
```

### 2. `src/revue/core/agent_loader.py` — `LoadedAgent.analyse()`

Remove the `if isinstance(self._client, AnthropicClient):` branch entirely.
The unified path becomes:

```python
prompt = (
    f"{self._def.system_prompt}\n\n"
    f"{shared_context}"
    f"Review the following diff:\n\n{diff_text}\n\n"
    f"{_INSTRUCTIONS}"
)
raw = self._client.complete([{"role": "user", "content": prompt}])
```

Remove the `from .ai_client import AnthropicClient` import if it becomes unused.

### 3. `src/revue/core/shared_analysis.py` — `run_shared_analysis()`

Remove the `if isinstance(client, AnthropicClient):` branch. The `from .ai_client import
AnthropicClient` import inside the try block is removed. One code path:

```python
prompt = SHARED_ANALYSIS_PROMPT.format(diff_summary=diff_summary)
if resolved_provider not in _JSON_FORMAT_PROVIDERS:
    prompt += _ANTHROPIC_JSON_SUFFIX
raw = client.complete([{"role": "user", "content": prompt}])
```

## Epic
REVUE-87: Developer Experience & Transparency

## Estimate
2–3 hours (code changes ~30 min, test updates ~60–90 min, verification ~30 min)
