# Quickstart — GitHub Actions

Add Revue AI code review to your GitHub repository in about five minutes.

---

## Prerequisites

- A GitHub repository with Actions enabled
- A [Revue account](https://revue.sh/signup) and license key
- An API key for your chosen AI provider (Anthropic or OpenAI)

---

## Step 1 — Sign up and get your license key

1. Go to [https://revue.sh/signup](https://revue.sh/signup) and create an account.
2. Choose a plan (Free gives you 25 reviews/month; Indie/Pro give unlimited reviews).
3. Copy your **REVUE_LICENSE_KEY** from the dashboard.

---

## Step 2 — Add secrets to your GitHub repository

Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret name | Value |
|---|---|
| `REVUE_LICENSE_KEY` | Your Revue license key |
| `OPENROUTER_API_KEY` | Your OpenRouter API key (default). For Anthropic/OpenAI, use `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` respectively |

---

## Step 3 — Add the workflow file

Create `.github/workflows/revue.yml` in your repository:

```yaml
name: Revue AI Code Review

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
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install Revue
        run: pip install revue

      - name: Run Revue review
        env:
          REVUE_LICENSE_KEY: ${{ secrets.REVUE_LICENSE_KEY }}
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPOSITORY: ${{ github.repository }}
          GITHUB_PR_NUMBER: ${{ github.event.pull_request.number }}
        run: |
          git diff origin/${{ github.base_ref }}...HEAD > /tmp/pr.diff
          revue review --diff /tmp/pr.diff --post-comments
```

**Using Anthropic or OpenAI instead?** Replace `OPENROUTER_API_KEY` with `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, and set `provider: anthropic` or `provider: openai` and the corresponding model in your `.revue.yml`.

---

## Step 4 — Create `.revue.yml` in your repository root

```yaml
# .revue.yml — Revue configuration
version: "1"

ai:
  provider: openrouter             # openrouter | anthropic | openai | azure | custom
  model: deepseek/deepseek-v4-pro  # default (cost-optimised). See docs/configuration/per-model-knobs.md
  api_key_env: OPENROUTER_API_KEY  # env var name for your AI provider key (BYOK)

review:
  max_diff_lines: 2000             # skip the review if the diff is larger than this
  min_confidence: 70               # only surface findings above this confidence score
  agent_timeout_seconds: 90        # per-agent timeout; raise to 120 on slow networks
  ignore_patterns:
    - "*.md"
    - "*.lock"
    - "package-lock.json"
    - "*.min.js"
    - "node_modules/*"

agents:
  team: team-full-review           # team-full-review | team-quick | team-security-focus | …

output:
  format: markdown                 # markdown | json | text
```

---

## Step 5 — Open a pull request and see the review

1. Push a branch and open a pull request.
2. The **Revue AI Code Review** check runs automatically.
3. Revue posts inline comments and a summary comment on the PR.

---

## Troubleshooting

### "No Revue license key found"
- Check that `REVUE_LICENSE_KEY` is set in **Settings → Secrets**.
- Make sure the secret name matches exactly (case-sensitive).

### "License key is invalid or has been revoked"
- Log in to [https://revue.sh/account](https://revue.sh/account) and verify your key.
- Free-tier keys are valid for 30 days; renew them in the dashboard.

### "You have used all of your free reviews"
- Upgrade at [https://revue.sh/upgrade](https://revue.sh/upgrade) for unlimited reviews.

### "Revue API is unreachable and the offline grace period has expired"
- GitHub Actions runners must be able to reach `api.revue.sh` on port 443.
- Check if your org has IP allow-listing or egress restrictions.

### "API is unreachable" on first run
- Your runner may not have internet access. Check your Actions network settings.
- Self-hosted runners: ensure outbound HTTPS to `api.revue.sh` is allowed.

### No comments posted on the PR
- Confirm the workflow has `pull-requests: write` permission.
- Check the `GITHUB_TOKEN` is available (it is by default in standard repos).
- Inspect the Actions log for errors from the `revue review` step.

### Review is empty or skipped
- The diff may exceed `max_diff_lines` (default 2000). Increase it or break up the PR.
- All changed files may match `ignore_patterns`. Adjust the list in `.revue.yml`.
