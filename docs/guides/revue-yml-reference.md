# `.revue.yml` Configuration Reference

Complete reference for all keys in the `.revue.yml` configuration file.

Place `.revue.yml` in the root of your repository. If the file is absent, Revue falls back to environment variables only.

---

## Top-level keys

| Key | Type | Required | Description |
|---|---|---|---|
| `version` | string | **Yes** | Config schema version. Must be `"1"`. |
| `language` | string | No | Repository's primary coding language (e.g. `python`, `swift`, `go`). Used to prime reviewer agents — they treat this as their default expertise lens but still review every file in the diff. When omitted, Revue infers the language from the diff. |
| `ai` | object | No | AI provider settings. |
| `review` | object | No | Review behaviour settings. |
| `consolidation` | object | No | Finding consolidation settings (grouping and synthesis bounds). |
| `rating` | object | No | Star-rating formula weights. |
| `noise_filters` | object | No | Noise filter settings. |
| `agents` | object | No | Agent team and custom agent settings. |
| `output` | object | No | Output format settings. |

---

## `version`

```yaml
version: "1"
```

The schema version. This field is required. Only `"1"` is currently supported. Revue will refuse to run if this field is missing or set to an unsupported value.

---

## `ai`

Controls which AI provider and model Revue uses.

| Key | Type | Default | Description |
|---|---|---|---|
| `provider` | string | `openrouter` | AI provider. One of: `openrouter`, `anthropic`, `openai`, `azure`, `custom`. |
| `model` | string | `deepseek/deepseek-v4-pro` | Model identifier passed to the provider API. Drives the reviewer agents (Maya, Zara, Kai, Leo). |
| `synthesis_model` | string | — | Optional override for the **reasoning tier**: both Nova (synthesis) and Vex (verification) run on this model. Defaults to `model` when unset. Reviewer agents are not affected. |
| `api_key_env` | string | — | Name of the environment variable that holds your API key (BYOK). Example: `OPENROUTER_API_KEY`. |
| `base_url` | string | — | Override the provider's base URL. Useful for corporate AI gateways. |
| `temperature` | float | `0.3` | Sampling temperature (0.0–2.0). Lower = more deterministic output. |
| `max_tokens` | int | `50000` | Maximum tokens the AI may generate per call. |
| `azure` | object | — | Azure-specific settings (required when `provider: azure`). |

### `ai.azure`

Required only when `provider: azure`.

| Key | Type | Default | Description |
|---|---|---|---|
| `endpoint` | string | — | Azure OpenAI endpoint URL. Example: `https://myorg.openai.azure.com`. |
| `deployment` | string | — | Azure deployment name. |
| `api_version` | string | `2024-02-01` | Azure API version string. |

### Examples

```yaml
# OpenRouter / DeepSeek (default — cheapest reliable reviewer pair)
ai:
  provider: openrouter
  model: deepseek/deepseek-v4-pro
  api_key_env: OPENROUTER_API_KEY

# Anthropic (opt-in — highest signal, highest cost)
ai:
  provider: anthropic
  model: claude-sonnet-4-5-20250929
  api_key_env: ANTHROPIC_API_KEY

# OpenAI
ai:
  provider: openai
  model: gpt-4o
  api_key_env: OPENAI_API_KEY

# Azure OpenAI
ai:
  provider: azure
  model: gpt-4o
  api_key_env: AZURE_OPENAI_KEY
  azure:
    endpoint: https://myorg.openai.azure.com
    deployment: my-gpt4o-deployment
    api_version: "2024-02-01"

# Custom / corporate AI gateway
ai:
  provider: custom
  model: claude-sonnet-4-5-20250929
  api_key_env: GATEWAY_API_KEY
  base_url: https://ai-gateway.mycompany.internal/v1
```

---

## `review`

Controls how Revue processes the diff and filters files.

| Key | Type | Default | Description |
|---|---|---|---|
| `max_diff_lines` | int | `2000` | Hard line limit for the entire diff. If exceeded, Revue stops and suggests breaking the PR into smaller pieces. Range: 1–10000. |
| `min_confidence` | int | `70` | Suppress findings with AI confidence below this percentage (0–100). |
| `agent_timeout_seconds` | int | `90` | Per-agent wall-clock timeout in seconds (1–600). Raise to `120` on slow or VPN-constrained networks. |
| `ignore_patterns` | list[string] | `[]` | Glob patterns for files to skip during review. Applied to `file_path`. |
| `reviewer_tool_use` | bool | `true` | Enable lazy full-file reads for reviewer agents (Maya, Leo, Kai, Zara). When enabled, agents can call the `read_file` tool to verify claims against the full file context before flagging findings (REVUE-241). Improves accuracy of prose-only findings at the cost of slightly higher token usage. Set to `false` to disable and save tokens. |
| `max_parallel_agents` | int | `1` | Maximum number of agents to run in parallel. Range: 1–8. Setting to 1 runs agents sequentially (safe default). Increase to reduce wall-clock time at higher API cost. |

### Review configuration examples

```yaml
# Basic configuration
review:
  max_diff_lines: 3000
  min_confidence: 65
  agent_timeout_seconds: 90

# With ignore patterns and feature flags
review:
  max_diff_lines: 3000
  min_confidence: 65
  agent_timeout_seconds: 90
  max_parallel_agents: 2  # Run 2 reviewers in parallel to reduce wall-clock time
  reviewer_tool_use: true  # Enable full-file reads for reviewers (default)
  ignore_patterns:
    - "*.md"
    - "*.lock"
    - "*.min.js"
    - "*.min.css"
    - "package-lock.json"
    - "yarn.lock"
    - "node_modules/*"
    - "vendor/*"
    - "third_party/*"
    - "**/__snapshots__/**"
    - "test_*"
    - "*_test.*"
```

---

## `consolidation`

Controls how Revue groups and synthesises multiple findings into single comments (REVUE-210, Decision 2–3).

Pass A (deterministic) clusters findings by proximity. Pass B (single-shot Nova) synthesises clusters into coherent comments. A verification pass (Vex) then checks Nova's output against the diff. Nova and Vex share the **reasoning tier** model — set `ai.synthesis_model` to control both.

| Key | Type | Default | Description |
|---|---|---|---|
| `proximity_lines` | int | `3` | Maximum line distance (N) for grouping findings in the same file. Findings farther apart become separate comments. |
| `max_group_size` | int | `3` | Maximum findings per group (K). Groups exceeding this limit are split into smaller groups or singletons. |

### Behaviour

For each file's findings (sorted by line number):

1. **Grouping**: Cluster findings where `line_distance ≤ N` AND `group_size ≤ K`.
2. **Synthesis**: Pass each group to Nova (LLM) to produce a single unified comment.
3. **Fallback**: If Nova fails, deterministically concatenate findings with attribution headers.

### Examples

```yaml
# Conservative bounds (default)
consolidation:
  proximity_lines: 3
  max_group_size: 3

# Aggressive grouping (merge distant findings into fewer comments)
consolidation:
  proximity_lines: 10
  max_group_size: 5

# Minimal grouping (post each finding as its own comment)
# max_group_size: 1 means groups cap at 1 finding — every finding becomes a singleton
consolidation:
  proximity_lines: 0
  max_group_size: 1
```

---

## `rating`

Controls how findings are translated into the 1–5 star score shown in the PR summary comment.

The score starts at **5.0** and a weighted penalty is subtracted for each finding. The result is clamped between `floor` and `5.0`.

```
score = 5.0 − (high × w_high + medium × w_medium + low × w_low + info × w_info)
score = max(floor, min(5.0, score))
```

### `rating.weights`

| Key | Type | Default | Description |
|---|---|---|---|
| `high` | float | `1.5` | Penalty subtracted per HIGH finding. |
| `medium` | float | `0.3` | Penalty subtracted per MEDIUM finding. |
| `low` | float | `0.05` | Penalty subtracted per LOW finding. |
| `info` | float | `0.0` | Penalty subtracted per INFO finding (none by default). |
| `floor` | float | `1.0` | Minimum possible score, regardless of finding count. Set to `0.0` to allow a score of zero. |

### Examples

```yaml
# Default (balanced — typical projects)
rating:
  weights:
    high:   1.5
    medium: 0.3
    low:    0.05
    info:   0.0
  floor: 1.0

# Strict team — medium findings penalised as heavily as high
rating:
  weights:
    high:   2.0
    medium: 1.0
    low:    0.2
    info:   0.0
  floor: 0.0

# Lenient team — only high findings meaningfully affect the score
rating:
  weights:
    high:   1.0
    medium: 0.1
    low:    0.0
    info:   0.0
  floor: 2.0
```

> **Note:** `revue init` pre-fills this section with the default weights. You can adjust the values without restarting — they are read fresh on each run.

---

## `noise_filters`

Controls the post-consolidation noise suppression layer that removes false positives.

| Key | Type | Default | Description |
|---|---|---|---|
| `disable` | list[string] | `[]` | Names of noise filters to disable. See filter names below. |
| `low_confidence_threshold` | float | `0.5` | Suppress findings whose normalised confidence score is below this value (0.0–1.0). |

### Available filter names

| Filter name | What it suppresses |
|---|---|
| `swift-di` | Swift dependency injection boilerplate false positives |
| `linter-suppression` | Findings on lines with explicit linter suppression comments |
| `low-confidence` | Findings below the `low_confidence_threshold` |
| `duplicate` | Duplicate findings across agents |

### `allowed_patterns` and `disallowed_patterns`

Define patterns that represent intentional design decisions (allowed) or patterns that should always be flagged (disallowed). These are injected into agent system prompts as natural-language guidance before every review.

| Key | Type | Default | Description |
|---|---|---|---|
| `allowed_patterns` | list[object] | `[]` | Patterns the agent should **not** flag. Each entry has `pattern` (string) and `rationale` (string). |
| `disallowed_patterns` | list[object] | `[]` | Patterns the agent should **always** flag. Same structure as above. |

Each entry requires two fields and supports one optional field:

| Field | Type | Required | Description |
|---|---|---|---|
| `pattern` | string | **Yes** | Natural-language description of the code pattern. |
| `rationale` | string | **Yes** | Why this pattern is allowed or disallowed. |
| `applies_to` | list[string] | No | Agent names that receive this pattern. Omit (or leave empty) to inject into **all** agents. |

#### `applies_to` — agent-scoped injection

By default every pattern is injected into every agent's system prompt. Use `applies_to` to target only the agents that are relevant, reducing prompt size and preventing unrelated agents from being confused by project-specific context.

Built-in agent names:

| Name | Role |
|---|---|
| `zara` | Security |
| `kai` | Performance |
| `leo` | Architecture |
| `maya` | Code quality |

Custom agents (from `custom_agents_dir`) use whatever `name` is declared in their definition file. `applies_to` matching is case-insensitive.

```yaml
noise_filters:
  disable:
    - "swift-di"
  low_confidence_threshold: 0.6
  allowed_patterns:
    # Global — injected into all agents
    - pattern: "TODO finding where 'TODO'/'FIXME' doesn't literally appear in the line"
      rationale: "Guard against model hallucinating TODOs from the word 'to' in prose"

    # Scoped — only architecture and code-quality agents need this context
    - pattern: "N+1 query in comment resolution is acceptable for current dataset size"
      rationale: "Fewer than 50 comments per PR; batch-fetch deferred to post-MVP"
      applies_to: ["kai", "leo"]

    # Scoped — code-quality agent only
    - pattern: "Bare except in _inject_pr_context"
      rationale: "Intentional catch-all; PR context injection must not crash the review loop"
      applies_to: ["maya"]

  disallowed_patterns:
    - pattern: "TODO comments in production code"
      rationale: "TODOs should be tracked as Jira tickets"
```

Patterns are injected under `## Allowed Patterns — Do Not Flag` and `## Disallowed Patterns — Always Flag` headings in each agent's system prompt. Agents with no matching patterns receive no injection at all.

---

## `agents`

Controls which review team runs and where custom agents are loaded from.

| Key | Type | Default | Description |
|---|---|---|---|
| `team` | string | `team-full-review` | Name of the agent team to run. See built-in teams below. |
| `custom_agents_dir` | string | `""` | Path to a directory of custom agent `.md` / `.yaml` definition files. Relative to the repository root. |

### Built-in teams

| Team name | Description |
|---|---|
| `team-full-review` | All specialists: code quality, security, performance, architecture |
| `team-quick` | Code quality only (Maya) — fast, low cost |
| `team-security-focus` | Security-first: Zara (security) + Maya (code quality) |
| `team-performance` | Kai (performance) + Maya (code quality) |
| `team-swift-ios` | Swift/iOS-specific agents + Zara |
| `team-kotlin-android` | Kotlin/Android-specific agents + Zara |
| `team-python` | Python-focused agents |
| `team-typescript` | TypeScript-focused agents |

```yaml
agents:
  team: team-security-focus
  custom_agents_dir: ".revue/agents"
```

### Custom agents

Place agent definition files in the directory specified by `custom_agents_dir`. Each file must be a Markdown (`.md`) or YAML (`.yaml`) agent definition following the Revue agent schema.

---

## `output`

Controls how Revue formats and writes its review output.

| Key | Type | Default | Description |
|---|---|---|---|
| `format` | string | `markdown` | Output format. One of: `markdown`, `json`, `text`. |
| `file` | string | `""` | Write output to this file path instead of stdout. Relative to the repository root. |

```yaml
output:
  format: json
  file: revue-output.json
```

---

## Complete example

```yaml
# .revue.yml — Revue configuration
version: "1"

ai:
  provider: openrouter
  model: deepseek/deepseek-v4-pro
  api_key_env: OPENROUTER_API_KEY
  temperature: 0.3
  max_tokens: 50000

review:
  max_diff_lines: 2000
  min_confidence: 70
  agent_timeout_seconds: 90
  ignore_patterns:
    - "*.md"
    - "*.lock"
    - "package-lock.json"
    - "*.min.js"
    - "node_modules/*"
    - "vendor/*"

rating:
  weights:
    high:   1.5   # penalty per HIGH finding
    medium: 0.3   # penalty per MEDIUM finding
    low:    0.05  # penalty per LOW finding
    info:   0.0   # INFO findings do not affect the score
  floor: 1.0      # minimum possible score

noise_filters:
  disable: []
  low_confidence_threshold: 0.5
  allowed_patterns:
    - pattern: "N+1 query in comment resolution"
      rationale: "Fewer than 50 comments per PR; batch-fetch deferred to post-MVP"
      applies_to: ["kai", "leo"]
  disallowed_patterns:
    - pattern: "TODO comments in production code"
      rationale: "TODOs should be tracked as Jira tickets"

agents:
  team: team-full-review
  custom_agents_dir: ""

output:
  format: markdown
  file: ""
```

---

## API key resolution

Revue resolves the API key at startup using the following priority order (first match wins):

1. `api_key` — value set directly in code (not supported via `.revue.yml`; internal use only).
2. `api_key_env` — name of the environment variable to read. Use this when your CI secret has a non-standard name.
3. Provider default env var — Revue looks up the standard env var for your `provider` automatically if `api_key_env` is omitted.

### Provider default env vars

| Provider | Default env var |
|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `azure` | `AZURE_OPENAI_API_KEY` |
| `openrouter` | `OPENROUTER_API_KEY` |
| `custom` | `REVUE_API_KEY` |

**`api_key_env` is optional for standard setups.** If you name your CI secret after the provider default (e.g. `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`), you can omit `api_key_env` entirely and Revue will find the key automatically.

```yaml
# api_key_env omitted — Revue auto-resolves OPENAI_API_KEY from the environment
ai:
  provider: openai
  model: gpt-4o-mini
```

Use `api_key_env` only when your CI secret name differs from the provider default:

```yaml
# Custom CI secret name
ai:
  provider: openai
  model: gpt-4o-mini
  api_key_env: MY_CORP_AI_KEY   # reads $MY_CORP_AI_KEY instead of $OPENAI_API_KEY
```

> **Multi-provider key support (post-MVP):** When Revue adds fallback provider support (REVUE-147, REVUE-148), each provider in the chain will have its own `api_key_env` field. Until then, `api_key_env` applies to the single configured provider.

---

## Environment variable precedence

Revue merges configuration in this order (later sources win):

1. Built-in defaults
2. `.revue.yml` values
3. Environment variables

These environment variables always override `.revue.yml`:

| Environment variable | Overrides |
|---|---|
| `REVUE_LICENSE_KEY` | License key |
| `REVUE_PROVIDER` | `ai.provider` |
| `REVUE_MODEL` | `ai.model` |
| `REVUE_API_KEY_ENV` | `ai.api_key_env` |
| `REVUE_BASE_URL` | `ai.base_url` |
