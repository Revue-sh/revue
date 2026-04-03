# `.revue.yml` Configuration Reference

Complete reference for all keys in the `.revue.yml` configuration file.

Place `.revue.yml` in the root of your repository. If the file is absent, Revue falls back to environment variables only.

---

## Top-level keys

| Key | Type | Required | Description |
|---|---|---|---|
| `version` | string | **Yes** | Config schema version. Must be `"1"`. |
| `ai` | object | No | AI provider settings. |
| `review` | object | No | Review behaviour settings. |
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
| `provider` | string | `anthropic` | AI provider. One of: `anthropic`, `openai`, `azure`, `openrouter`, `custom`. |
| `model` | string | `claude-sonnet-4-5-20250929` | Model identifier passed to the provider API. |
| `api_key_env` | string | — | Name of the environment variable that holds your API key (BYOK). Example: `ANTHROPIC_API_KEY`. |
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
# Anthropic (default)
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

### Ignore pattern examples

```yaml
review:
  max_diff_lines: 3000
  min_confidence: 65
  agent_timeout_seconds: 90
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

Each entry requires:
- **`pattern`** (string, required): A natural-language description of the code pattern.
- **`rationale`** (string, required): Why this pattern is allowed or disallowed.

```yaml
noise_filters:
  disable:
    - "swift-di"
  low_confidence_threshold: 0.6
  allowed_patterns:
    - pattern: "_def attribute access on LoadedAgent"
      rationale: "Internal implementation detail, no public API"
    - pattern: "Bare except in _inject_pr_context"
      rationale: "Intentional catch-all, PR context injection must not crash the review loop"
  disallowed_patterns:
    - pattern: "TODO comments in production code"
      rationale: "TODOs should be tracked as Jira tickets"
```

Allowed patterns are injected under a `## Allowed Patterns — Do Not Flag` heading in each agent's system prompt. Disallowed patterns appear under `## Disallowed Patterns — Always Flag`. When both lists are empty, no injection occurs.

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
# .revue.yml — Revue.io configuration
version: "1"

ai:
  provider: anthropic
  model: claude-sonnet-4-5-20250929
  api_key_env: ANTHROPIC_API_KEY
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

noise_filters:
  disable: []
  low_confidence_threshold: 0.5

agents:
  team: team-full-review
  custom_agents_dir: ""

output:
  format: markdown
  file: ""
```

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
