# REVUE-116: OpenAI Prompt Caching — observability + prompt_cache_key routing

## User Story
As a developer running Revue on a PR with an OpenAI-compatible provider, I want CI logs to
show how many tokens were served from cache on each call, and I want repeated reviews of the
same PR to be routed to the same cache server so re-reviews hit the cache reliably.

## Background

OpenAI Prompt Caching is automatic and already active — no code changes needed to enable it.
Caching activates for any prompt ≥ 1,024 tokens, with the static system prompt placed first
(which `_openai_messages()` already does correctly).

Two gaps remain after REVUE-115:

**Gap 1 — No observability:** All 4 OpenAI-compatible clients (`OpenAIClient`,
`AzureOpenAIClient`, `OpenRouterClient`, `CustomGatewayClient`) discard `resp.usage`.
`usage.prompt_tokens_details.cached_tokens` is never logged. We have no way to confirm
whether caching is active without using the OpenAI Usage dashboard.

**Gap 2 — No `prompt_cache_key`:** OpenAI routes requests to cache servers using a hash of
the first ~256 tokens of the prompt prefix. When multiple requests share the same prefix
but arrive at >15 RPM (e.g., parallel agents in CI), some overflow to additional machines
and miss the cache. Passing `prompt_cache_key` routes all requests for the same diff to the
same server, maximising cache hit rates for re-reviews.

**SDK verification (openai 2.30.0, installed):**
`chat.completions.create()` accepts both `prompt_cache_key` and `prompt_cache_retention` as
native parameters (confirmed via `inspect.signature`). `usage.prompt_tokens_details.cached_tokens`
exists on `PromptTokensDetails` (confirmed via `PromptTokensDetails.model_fields`).

**What `prompt_cache_key` does and does not solve:**
- ✅ Same agent, same diff, re-review → routed to same server → cache hit
- ✅ Prevents overflow to uncached machines when >15 RPM for the same prefix
- ❌ Does NOT share caches between Leo/Maya/Kai/Zara on a fresh review — each agent has a
  different system prompt → different prefix hash → different cache entry regardless of key

## Acceptance Criteria

1. **AC1 — Observability for all 4 OpenAI-compatible clients:** After each `complete()` call,
   `usage.prompt_tokens_details.cached_tokens` is logged at DEBUG level via the module logger
   alongside `prompt_tokens` and `completion_tokens`. Uses `getattr` with a 0 default so the
   log never raises on older SDK responses that lack `prompt_tokens_details`.

2. **AC2 — `cache_key` parameter on `AIClient` protocol:** `AIClient.complete()` protocol
   gains an optional `cache_key: str | None = None` keyword argument. All 5 implementations
   accept it. `AnthropicClient` ignores it (caching already handled by `cache_control`).
   The 4 OpenAI-compatible clients pass it as `prompt_cache_key` to `chat.completions.create()`
   when non-None.

3. **AC3 — `LoadedAgent.analyse()` passes diff hash as `cache_key`:** Computes
   `hashlib.sha256(diff_text.encode()).hexdigest()[:16]` and passes it to
   `self._client.complete(..., cache_key=diff_hash)`.

4. **AC4 — `run_shared_analysis()` passes diff hash as `cache_key`:** Same pattern using
   `diff_summary` as the input to the hash.

5. **AC5 — `_openai_messages()` docstring updated:** Docstring updated to explain that
   OpenAI prefix caching is automatic, why static system content comes first (prefix-based
   caching optimisation), and that `cache_control` stripping is defensive for any future
   callers that might add it.

6. **AC6 — No test regressions:** `cd src && PYTHONPATH=$(pwd) pytest revue/tests/ -q`
   passes with zero failures.

7. **AC7 — Unit tests added:** Tests cover the `cached_tokens` logging path and verify that
   OpenAI clients pass `prompt_cache_key` through to `chat.completions.create()` when
   `cache_key` is provided, and omit it when `None`.

## Test Cases

1. **TC1 — OpenAI client logs cached_tokens:**
   Mock `openai.OpenAI` response with `usage.prompt_tokens_details.cached_tokens=512`.
   Assert `_log.debug` is called and the log args include `512`.

2. **TC2 — prompt_cache_key forwarded when provided:**
   Mock `chat.completions.create`. Call `OpenAIClient.complete(..., cache_key="abc123")`.
   Assert `chat.completions.create` received `prompt_cache_key="abc123"`.

3. **TC3 — prompt_cache_key omitted when None:**
   Call `OpenAIClient.complete(..., cache_key=None)`.
   Assert `chat.completions.create` was NOT called with `prompt_cache_key` kwarg (or it is
   absent/None).

4. **TC4 — AnthropicClient ignores cache_key:**
   Call `AnthropicClient.complete(..., cache_key="anything")`.
   Assert `messages.create` was NOT called with `prompt_cache_key`.

5. **TC5 — LoadedAgent passes diff hash:**
   Call `LoadedAgent.analyse()` with a mock client. Assert `complete()` was called with a
   non-None `cache_key` kwarg that is a 16-character hex string.

6. **TC6 — No regression:** 677+ tests pass.

## Out of Scope

- `prompt_cache_retention="24h"` — model-specific (gpt-4.1+), requires config option;
  separate story if needed.
- Sharing cache between parallel agents on a fresh review — requires restructuring all agent
  system prompts to share a common prefix; separate story.
- OpenRouter/Azure backend support — these may silently ignore `prompt_cache_key`; the SDK
  layer passes it without error regardless.

## Dependencies

- REVUE-115 (merged or same branch) — removes per-block `cache_control` from callers; this
  story builds on that clean state.
- openai SDK 2.30.0 already installed — `prompt_cache_key` verified present in
  `chat.completions.create()` signature.

## Notes — Key File Locations

| File | Lines affected |
|---|---|
| `src/revue/core/ai_client.py` | `AIClient` protocol (~line 37), `_openai_messages()` docstring (~line 122), all 4 OpenAI `complete()` methods |
| `src/revue/core/agent_loader.py` | `LoadedAgent.analyse()` — add `hashlib` import + diff hash |
| `src/revue/core/shared_analysis.py` | `run_shared_analysis()` — add diff hash |
| `src/revue/tests/core/test_ai_client.py` | Add TC1–TC4 |
| `src/revue/tests/core/test_agent_loader.py` | Add TC5 |

## Implementation Approach

### 1. `AIClient` protocol — add `cache_key` parameter
```python
def complete(
    self,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 4096,
    temperature: float = 0.3,
    system: "str | list[dict[str, Any]] | None" = None,
    cache_key: "str | None" = None,
) -> str: ...
```

### 2. OpenAI-compatible clients — log + forward cache_key
```python
def _call() -> str:
    kwargs: dict[str, Any] = {
        "model": self._model,
        "messages": _openai_messages(messages, system),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if cache_key is not None:
        kwargs["prompt_cache_key"] = cache_key
    resp = self._client.chat.completions.create(**kwargs)
    usage = resp.usage
    if usage:
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) if details else 0
        _log.debug(
            "[openai] cached=%s prompt=%s completion=%s",
            cached, usage.prompt_tokens, usage.completion_tokens,
        )
    return resp.choices[0].message.content or ""
```

### 3. `agent_loader.py` — compute and pass diff hash
```python
import hashlib
# ...
diff_hash = hashlib.sha256(diff_text.encode()).hexdigest()[:16]
raw = self._client.complete(
    [{"role": "user", "content": prompt}],
    cache_key=diff_hash,
)
```

### 4. `shared_analysis.py` — same pattern
```python
import hashlib
# ...
diff_hash = hashlib.sha256(diff_summary.encode()).hexdigest()[:16]
raw = client.complete(
    [{"role": "user", "content": prompt}],
    cache_key=diff_hash,
)
```

## Epic
REVUE-87: Developer Experience & Transparency

## Estimate
2–3 hours (protocol change + 4 client updates + callers + tests)
