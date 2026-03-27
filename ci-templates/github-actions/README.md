# Revue.io — GitHub Actions Integration

Add automated multi-agent AI code reviews to every PR in 2 minutes.

## Quick Setup

1. Add your AI provider API key as a GitHub secret:
   - **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY`, etc.)

2. Create `.github/workflows/revue-review.yml` in your repo:

```yaml
name: Revue.io Code Review

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write

jobs:
  revue:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: revue-io/action@v1
        with:
          ai_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
```

3. Open a PR — Revue posts inline comments automatically.

---

## All Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `ai_api_key` | ✅ Yes | — | Your AI provider API key (BYOK — never stored by Revue) |
| `ai_provider` | No | `anthropic` | `anthropic` \| `openai` \| `azure` \| `openrouter` \| `custom` |
| `ai_model` | No | `claude-sonnet-4-5-20250929` | Model name for the chosen provider |
| `ai_base_url` | No | — | Custom base URL for Azure or self-hosted gateways |
| `revue_token` | No | — | Revue.io workspace token (from app.revue.io/settings/tokens) for analytics |
| `mode` | No | `multi-agent` | `multi-agent` \| `single-agent` |
| `config_path` | No | `.revue.yml` | Path to your Revue config file |
| `min_confidence` | No | `70` | Minimum Sage fix-suggestion confidence (0–100) |
| `fail_on_critical` | No | `false` | Exit non-zero if critical findings found (blocks merge) |
| `python_version` | No | `3.12` | Python version for the Revue runner |

## Outputs

| Output | Description |
|---|---|
| `findings_count` | Total findings posted |
| `critical_count` | Critical-severity findings |
| `review_url` | Link to full report on app.revue.io (requires `revue_token`) |

---

## Examples

### OpenAI backend

```yaml
- uses: revue-io/action@v1
  with:
    ai_api_key: ${{ secrets.OPENAI_API_KEY }}
    ai_provider: openai
    ai_model: gpt-4o
```

### Azure OpenAI

```yaml
- uses: revue-io/action@v1
  with:
    ai_api_key: ${{ secrets.AZURE_OPENAI_API_KEY }}
    ai_provider: azure
    ai_model: gpt-4o
    ai_base_url: https://your-resource.openai.azure.com
```

### Block merges on critical findings

```yaml
- uses: revue-io/action@v1
  with:
    ai_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
    fail_on_critical: true
```

### Use outputs in downstream steps

```yaml
- id: revue
  uses: revue-io/action@v1
  with:
    ai_api_key: ${{ secrets.ANTHROPIC_API_KEY }}

- name: Print summary
  run: echo "Revue found ${{ steps.revue.outputs.findings_count }} issues"
```

---

## Configuration file (`.revue.yml`)

Place in your repo root for project-level settings:

```yaml
version: "1"
ai:
  provider: anthropic
  model: claude-sonnet-4-5-20250929
  api_key_env: ANTHROPIC_API_KEY

review:
  max_diff_lines: 2000
  min_confidence: 70
  agent_timeout_seconds: 90
  ignore_patterns:
    - "*.md"
    - "*.lock"
    - "package-lock.json"

noise_filters:
  disable: []
  low_confidence_threshold: 0.5

agents:
  team: auto
```

---

## Versioning

| Tag | What it points to |
|---|---|
| `revue-io/action@v1` | Latest stable v1.x — auto-receives patch/minor updates |
| `revue-io/action@v1.0.0` | Pinned to exact release |
| `revue-io/action@main` | Bleeding edge — not recommended for production |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| No comments posted | Check Actions log. Verify `ANTHROPIC_API_KEY` secret is set and valid. |
| `permission denied` on `pull-requests` | Add `permissions: pull-requests: write` to your workflow. |
| Review times out | Raise `agent_timeout_seconds` in `.revue.yml` (default 90s). |
| Too many false positives | Raise `min_confidence` in `.revue.yml` to 80 or 90. |
| Large PR skipped | Raise `max_diff_lines` in `.revue.yml` (default 2000). |

---

## Migration from workflow template

If you previously used the copy-paste workflow template (pre-v1), replace your `revue-review.yml` with:

```yaml
- uses: revue-io/action@v1
  with:
    ai_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
```

The action handles diff fetching, Python setup, and comment posting automatically. Remove any manual `pip install`, `revue review`, or `actions/github-script` steps from your workflow.
