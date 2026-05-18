# Quickstart — GitLab CI

Add Revue AI code review to your GitLab repository in about five minutes.

---

## Prerequisites

- A GitLab repository with CI/CD enabled
- A [Revue account](https://revue.io/signup) and license key
- An API key for your chosen AI provider. Revue defaults to **OpenRouter / DeepSeek-V4-Pro** (cheapest reliable reviewer pair). Anthropic and OpenAI are supported via explicit override.

---

## Step 1 — Sign up and get your license key

1. Go to [https://revue.io/signup](https://revue.io/signup) and create an account.
2. Choose a plan (Free gives you 25 reviews/month; Indie/Pro give unlimited reviews).
3. Copy your **REVUE_LICENSE_KEY** from the dashboard.

---

## Step 2 — Add CI/CD variables to your GitLab project

Go to **Settings → CI/CD → Variables → Add variable** and add:

| Variable | Value | Masked | Protected |
|---|---|---|---|
| `REVUE_LICENSE_KEY` | Your Revue license key | ✅ Yes | No |
| `OPENROUTER_API_KEY` | Your OpenRouter API key (default) | ✅ Yes | No |

To use Anthropic or OpenAI instead, add `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` and set `provider:` and `model:` accordingly in `.revue.yml`.

> **Tip:** Set variables as "masked" to prevent them from appearing in job logs.

---

## Step 3 — Add the GitLab CI configuration

Create or update `.gitlab-ci.yml` in your repository:

```yaml
stages:
  - review

revue-ai-review:
  stage: review
  image: python:3.12-slim
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  before_script:
    - pip install revue
  script:
    - git fetch origin $CI_MERGE_REQUEST_TARGET_BRANCH_NAME
    - git diff origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME...HEAD > /tmp/mr.diff
    - revue review --diff /tmp/mr.diff --post-comments
  variables:
    REVUE_LICENSE_KEY: $REVUE_LICENSE_KEY
    OPENROUTER_API_KEY: $OPENROUTER_API_KEY
    GITLAB_URL: $CI_SERVER_URL
    GITLAB_TOKEN: $GITLAB_TOKEN
    CI_PROJECT_ID: $CI_PROJECT_ID
    CI_PROJECT_PATH: $CI_PROJECT_PATH
    CI_PROJECT_URL: $CI_PROJECT_URL
```

**If you use the Revue GitLab CI template** (available after marketplace listing), you can simplify to:

```yaml
include:
  - project: revue-io/ci-templates
    file: revue-review.yml

stages:
  - review

revue-review:
  extends: .revue-review
  variables:
    REVUE_LICENSE_KEY: $REVUE_LICENSE_KEY
    OPENROUTER_API_KEY: $OPENROUTER_API_KEY
```

---

## Step 4 — Create `.revue.yml` in your repository root

```yaml
# .revue.yml — Revue.io configuration
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

## Step 5 — Open a merge request and see the review

1. Push a branch and open a merge request.
2. The **revue-ai-review** job runs automatically as part of the MR pipeline.
3. Revue posts inline comments and a summary note on the MR.

---

## Troubleshooting

### "No Revue license key found"
- Verify `REVUE_LICENSE_KEY` exists under **Settings → CI/CD → Variables**.
- Variable names are case-sensitive. Confirm the exact spelling.
- If protected, the variable is only available on protected branches. Uncheck **Protected** to allow it on all branches.

### "License key is invalid or has been revoked"
- Log in to [https://revue.io/account](https://revue.io/account) and check your key.
- Regenerate the key in the dashboard if needed, then update the CI/CD variable.

### "You have used all of your free reviews"
- Upgrade at [https://revue.io/upgrade](https://revue.io/upgrade) for unlimited reviews.

### "Revue API is unreachable and the offline grace period has expired"
- GitLab runners must be able to reach `api.revue.io` on port 443.
- Self-managed GitLab: check your runner's egress firewall rules.

### No comments posted on the MR
- Revue uses `GITLAB_TOKEN` to post comments. Confirm the token has `api` scope.
- In GitLab SaaS, use a project access token or personal access token with `api` scope.
- Check the job log for errors from the `revue review --post-comments` command.

### Job only runs on `main` and not on MR pipelines
- Ensure you have `CI_PIPELINE_SOURCE == "merge_request_event"` in your `rules:` block.
- GitLab requires the source branch to also have a recent push for MR pipelines to trigger.

### Review is empty or skipped
- The diff may exceed `max_diff_lines` (default 2000). Increase it or break up the MR.
- All changed files may match `ignore_patterns`. Adjust the list in `.revue.yml`.
- Inspect the `git diff` output in the job log to confirm the diff file is non-empty.
