# Revue.io GitLab CI/CD Integration

Add automated AI code reviews to your MRs in 2 minutes.

## Quick Setup

1. Add to your `.gitlab-ci.yml`:

```yaml
include:
  - local: ci-templates/gitlab-ci/revue-review.yml

stages:
  - test

revue:
  extends: .revue-review
```

2. Add your AI provider API key as a CI/CD variable:
   - Go to **Settings → CI/CD → Variables**
   - Click **Add variable**
   - **Key**: name the variable after the provider you're using — Revue looks for the matching name:
     - `OPENROUTER_API_KEY` (default — OpenRouter, e.g. DeepSeek-V4-Pro)
     - `ANTHROPIC_API_KEY` (Claude — keys start with `sk-ant-`)
     - `OPENAI_API_KEY` (GPT models)
     - `AZURE_OPENAI_API_KEY` (Azure-hosted OpenAI)
   - **Value**: your API key
   - **Flags**: **Masked**, Protected (optional)

3. Open an MR — Revue will review it automatically.

## Configuration

Create `.revue.yml` in your repo root:

```yaml
version: "1"
ai:
  provider: openrouter
  model: deepseek/deepseek-v4-pro
review:
  max_diff_lines: 2000
  min_confidence: 70
agents:
  team: team-full-review
```

## How It Works

- The CI job runs only on `merge_request_event` pipelines — no impact on branch or tag pipelines.
- The MR diff is fetched via the GitLab API using the auto-provided `CI_JOB_TOKEN`.
- Revue analyses the diff and writes findings to `review.json`.
- `post_review.py` posts a summary note and up to 50 inline discussion threads on the MR.
- A review failure (e.g. API error) exits with code 0 so it never blocks a merge.

## Artifacts

`review.json` is stored as a job artifact for 7 days. Download it from the CI job page for offline inspection.

## Troubleshooting

- **No comments posted**: Check CI job logs. Ensure the API key variable for your provider (`OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `AZURE_OPENAI_API_KEY`) is set and unmasked for the pipeline.
- **`curl: command not found`**: The template installs `curl` and `jq` via `apt-get`. If your runner uses a non-Debian image, override `before_script` to use the appropriate package manager.
- **API rate limits**: Free tier allows 100 reviews/month. Upgrade at revue.io/pricing.
- **Permission errors**: Ensure `CI_JOB_TOKEN` has API access (enabled by default in GitLab 15.9+). For self-managed instances older than 15.9, use a project access token stored as `CI_JOB_TOKEN`.
- **Inline comments not appearing**: GitLab requires `base_sha` to match the MR diff base. Ensure `CI_MERGE_REQUEST_DIFF_BASE_SHA` is available (set automatically by GitLab in MR pipelines).
