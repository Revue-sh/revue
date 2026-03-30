# Quickstart — Bitbucket Pipelines

Add Revue AI code review to your Bitbucket repository in about five minutes.

---

## Prerequisites

- A Bitbucket Cloud repository with Pipelines enabled
- A [Revue account](https://revue-io.fly.dev/signup) and license key
- An API key for your chosen AI provider (Anthropic or OpenAI)
- A Bitbucket API token (Settings → Personal Bitbucket settings → API tokens)

---

## Step 1 — Sign up and get your license key

1. Go to [https://revue-io.fly.dev/signup](https://revue-io.fly.dev/signup) and create an account.
2. Copy your **REVUE_LICENSE_KEY** from the dashboard.

---

## Step 2 — Enable Pipelines

In your Bitbucket repository: **Repository settings → Pipelines → Settings → Enable Pipelines**.

---

## Step 3 — Add repository variables

Go to **Repository settings → Repository variables** and add (mark secrets as Secured):

| Variable | Value | Secured |
|---|---|---|
| `BITBUCKET_API_TOKEN` | Your Bitbucket API token | ✅ |
| `AI_API_KEY` | Your AI provider API key | ✅ |
| `REVUE_LICENSE_KEY` | Your Revue license key | ✅ |
| `AI_PROVIDER` | `anthropic` (or `openai`) | |
| `AI_MODEL` | `claude-sonnet-4-5-20250929` | |

---

## Step 4 — Add `bitbucket-pipelines.yml`

Create or update `bitbucket-pipelines.yml` in your repository root:

```yaml
image: python:3.12-slim

pipelines:
  pull-requests:
    "**":
      - step:
          name: "Revue.io AI Code Review"
          script:
            - pip install revue-io --quiet
            - |
              AUTH=$(echo -n "${BITBUCKET_USERNAME}:${BITBUCKET_API_TOKEN}" | base64)
              curl -s \
                -H "Authorization: Basic ${AUTH}" \
                -H "Accept: text/plain" \
                "https://api.bitbucket.org/2.0/repositories/${BITBUCKET_WORKSPACE}/${BITBUCKET_REPO_SLUG}/pullrequests/${BITBUCKET_PR_ID}/diff" \
                -o /tmp/revue_pr.diff
            - |
              AI_API_KEY="${AI_API_KEY}" revue review \
                --diff /tmp/revue_pr.diff \
                --platform bitbucket \
                --pr-id "${BITBUCKET_PR_ID}" \
                --workspace "${BITBUCKET_WORKSPACE}" \
                --repo-slug "${BITBUCKET_REPO_SLUG}" \
                --bb-username "${BITBUCKET_USERNAME}" \
                --bb-token "${BITBUCKET_API_TOKEN}" \
                --provider "${AI_PROVIDER:-anthropic}" \
                --model "${AI_MODEL:-claude-sonnet-4-5-20250929}" \
                --mode multi-agent \
                --config .revue.yml \
                || echo "Review completed"
```

---

## Step 5 — Add `.revue.yml` to your repository

```yaml
# .revue.yml
version: "1"

ai:
  provider: anthropic
  model: claude-sonnet-4-5-20250929
  api_key_env: AI_API_KEY

review:
  max_diff_lines: 2000
  min_confidence: 70
  ignore_patterns:
    - "*.md"
    - "*.lock"
    - "node_modules/*"

agents:
  team: team-full-review
```

---

## Step 6 — Open a pull request

1. Push a branch and open a pull request.
2. The **Revue.io AI Code Review** step runs automatically.
3. Revue posts a summary comment on the PR and sets a commit status (pass/fail).
