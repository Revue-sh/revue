# Per-Model Knobs

Revue's per-model registry (`src/revue/core/models_registry.yml`) carries one
config row per model. Each row exposes a small set of *knobs* that govern how
the dispatcher and the tool-loop wire up the underlying API call. This page is
the reference for those knobs: what they do, what values are valid, and why
they vary across models.

> The built-in registry is the source of truth for Revue-vetted models. To
> view the live, merged registry (built-ins + your `.revue.yml` overrides),
> run:
>
> ```bash
> revue list-models
> ```
>
> Add `--json` for machine-readable output or `--markdown` to regenerate the
> README table.

---

## Knob reference

### `provider`

Which API surface the dispatcher targets. The provider determines the SDK
client, the auth env-var, and the request shape.

Valid values:

- `anthropic` — the Anthropic Messages API (native Claude models).
- `openrouter` — OpenRouter's OpenAI-shaped proxy (Qwen, DeepSeek, etc.).
- `openai` — OpenAI's Chat Completions API.
- `azure` — Azure OpenAI deployment (requires `azure_endpoint` +
  `azure_deployment` in `.revue.yml`).
- `custom` — a self-hosted, OpenAI-compatible endpoint.

A model id is bound to exactly one provider; switching providers requires a
new registry entry.

### `schema_mode`

Which structured-output mechanism the model speaks. Set per provider family.

Valid values:

- `output_config` — Anthropic's GA structured-outputs feature (`output_config`
  on the Messages API). Used by every `provider: anthropic` model.
- `response_format` — the OpenAI-shaped `response_format: {type: "json_schema"
  …}` field. Used by OpenRouter, OpenAI, Azure, and most `custom` deployments.

The two mechanisms are mutually exclusive: an Anthropic model that received a
`response_format` block would 400, and vice-versa. The dispatcher reads this
knob to pick the right wrapper.

### `schema_strict`

Whether the structured-output schema is sent with strict mode on (rejects
unknown fields and unparseable tool calls).

Valid values: `true` | `false`.

The dispatcher enforces a policy: any model with `tier: supported` **must**
have `schema_strict: true`. A supported entry that drops strictness raises
`ModelRegistryError` at startup. Customer-added (`tier: unsupported`) entries
may opt out.

### `tool_choice_first_turn`

What `tool_choice` value to send on the first model turn of each agent loop.
Subsequent turns are always `auto` so the model can decide when to stop.

Valid values:

- `auto` — let the model decide whether to call a tool first turn (Anthropic
  default behaviour).
- `required` — force a tool call on the first turn. Necessary for Qwen and
  DeepSeek on OpenRouter, both of which otherwise emit a free-text reply and
  never call the structured-finding tool. See
  `scripts/smoke_openrouter_tools.py` and
  `docs/research/deepseek-v4-pro-evaluation.md` for the empirical evidence
  collected during REVUE-241 and REVUE-265.
- `none` — disable tool use entirely (reserved; not used by any built-in).

### `max_tokens_default`

The default `max_tokens` value for this model's completions. Acts as a per-row
default; the user can override it via `.revue.yml` for cost or context
reasons.

Built-in defaults:

- Sonnet 4.5: `4096` (larger output for synthesis / consolidator passes).
- Haiku 4.5: `2048` (per-agent worker cap; the typical envelope fits well
  inside).
- Qwen3 / DeepSeek-v4-pro: `2048` (OpenRouter cost ceiling).

### `tier`

Whether Revue ships this model as a vetted choice or treats it as customer
opt-in.

Valid values:

- `supported` — Revue has smoke-tested the model end-to-end. The dispatcher
  refuses to start with `schema_strict: false` on a supported row.
- `unsupported` — customer-added (or built-in but unvetted). The dispatcher
  permits any combination of knobs; the customer owns the risk.

A customer-added entry in `.revue.yml` defaults to `unsupported` unless the
entry explicitly says otherwise.

---

## Reserved knobs (not implemented yet)

These knob names are *reserved* — they appear in registry entries and in
`.revue.yml` extras but the dispatcher does not yet read them. They will land
in future stories and are documented here so customers do not collide with the
chosen names.

- `parallel_tool_calls` — whether the model may emit multiple tool calls in a
  single turn. Reserved for the parallel-tools rollout.
- `prompt_cache_key_supported` — whether the provider honours an explicit
  `prompt_cache_key`. Reserved for the cache-routing pass.
- `reasoning_mode` — provider-specific reasoning-trace toggle (e.g. Anthropic's
  extended thinking, DeepSeek's reasoner mode).
- `max_parallel_tools_per_turn` — companion to `parallel_tool_calls`; caps how
  many calls Revue will fan out in one turn.

Unknown knob keys land in `ModelConfig.extras` (read-only) so a newer registry
can ship knobs that an older binary safely ignores.

---

## How to override in `.revue.yml`

The `models:` section is a mapping of `model_id -> {knob: value}`. Per-entry
override semantics apply: only the keys you set change; the rest fall back to
the built-in row.

### Override one knob on a built-in model

```yaml
models:
  claude-haiku-4-5-20251001:
    max_tokens_default: 8192
```

Every other knob (provider, schema_mode, schema_strict, etc.) keeps its
built-in value. The override is visible in `revue list-models` with a
trailing `*` next to the changed cell.

### Add a customer model

```yaml
models:
  my-org/private-llm:
    provider: custom
    schema_mode: response_format
    schema_strict: false
    tool_choice_first_turn: auto
    max_tokens_default: 1024
    # tier defaults to "unsupported" for customer-added entries
```

The new row appears in `revue list-models` with `tier: unsupported`. Because
it is unsupported, `schema_strict: false` is allowed.

### Promote a customer model to `supported`

```yaml
models:
  my-org/private-llm:
    tier: supported
    schema_strict: true      # required when tier is supported
    # …other knobs…
```

The dispatcher will now refuse to start if `schema_strict` ever drops back to
`false` for this row — same gate as for built-in supported models.

---

## See also

- `src/revue/core/models_registry.py` — registry types and merge semantics.
- `src/revue/core/models_registry.yml` — built-in source of truth.
- `docs/research/deepseek-v4-pro-evaluation.md` — empirical smoke-test data
  that motivates `tool_choice_first_turn: required` on OpenRouter models.
