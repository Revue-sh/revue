# Frequently Asked Questions

---

## General

### What is Revue?

Revue is a multi-agent AI code reviewer with two modes:

**Claude Code skill (primary):** Install `/revue` in Claude Code and run it on your staged diff before you commit. Six specialist agents review your code inline, before it ever reaches CI.

**CI pipeline (secondary):** Add Revue to your GitHub Actions, GitLab CI, or Bitbucket Pipelines to automatically review every pull request or merge request.

### What platforms does Revue support?

- **Claude Code** (primary) — via the `/revue` skill, installed with one command
- **GitHub** — GitHub Actions
- **GitLab** — GitLab CI/CD
- **Bitbucket** — Bitbucket Pipelines

### Does Revue read my code?

No. Revue never reads your code. Your code diff goes directly to the AI provider you configure, using your own API key. Revue's servers only handle licence validation and usage counts. No source code is ever transmitted to Revue.

---

## Diff limits

### What is the diff limit?

By default, Revue stops reviewing if the diff exceeds 2,000 lines and suggests breaking the PR into smaller pieces. This keeps AI costs predictable and reviews fast.

You can raise the limit in `.revue.yml`:

```yaml
review:
  max_diff_lines: 4000
```

### What counts toward the diff limit?

Every line in the unified diff: context lines, additions, and deletions across all changed files.

### What happens when the limit is exceeded?

Revue posts a summary comment explaining that the diff is too large and suggesting a breakdown strategy. No agent reviews are run.

---

## BYOK (Bring Your Own Key)

### Do I need to provide an AI API key?

Yes. Revue uses your own AI provider key — it does not have a shared AI backend. This means:
- Your code never leaves your infrastructure
- You choose your AI provider (OpenRouter, Anthropic, OpenAI, Azure, etc.)
- You control costs directly

### Which AI providers are supported?

- **OpenRouter** — default (DeepSeek-V4-Pro, cost-optimised)
- **Anthropic** (Claude)
- **OpenAI** (GPT-4o, etc.)
- **Azure OpenAI**
- **Custom / corporate gateway** (any OpenAI-compatible endpoint)

### How do I configure my AI provider?

Set the API key as a CI secret and reference it in `.revue.yml`:

```yaml
ai:
  provider: openrouter              # default — most cost-efficient
  model: deepseek/deepseek-v4-pro
  api_key_env: OPENROUTER_API_KEY   # name of your CI secret
```

To use Anthropic instead, set `provider: anthropic`, `model: claude-sonnet-4-6`, `api_key_env: ANTHROPIC_API_KEY`.

---

## Blocking behaviour

### Can Revue block a PR from merging?

Yes. Set `fail_on_critical: true` in your CI configuration and Revue will exit with a non-zero code when critical findings are found, causing the pipeline to fail.

**GitHub Actions:**
```yaml
- uses: revue-io/action@v1
  with:
    fail_on_critical: "true"
    ai_api_key: ${{ secrets.OPENROUTER_API_KEY }}
```

**GitLab CI:**
```yaml
variables:
  REVUE_FAIL_ON_CRITICAL: "true"
```

### What severity levels does Revue use?

| Level | Description |
|---|---|
| `critical` | Must fix before merge — security vulnerabilities, data loss risks |
| `high` | Should fix — significant bugs or performance issues |
| `medium` | Consider fixing — code quality, maintainability |
| `low` | Minor suggestions — style, naming, minor improvements |

---

## Fix suggestions

### How do fix suggestions work?

After the specialist reviewers surface findings, a resolver step evaluates each one and, where it can generate a safe scoped fix, posts a platform-native code suggestion (1-click accept in GitHub/GitLab/Bitbucket).

### How do I control the confidence threshold?

Set `min_confidence` in `.revue.yml`:

```yaml
review:
  min_confidence: 70  # 0-100, default 70
```

Only suggestions above this confidence score are posted. Setting it higher (e.g. 85) means fewer but higher-quality suggestions.

### Can I disable fix suggestions?

Yes. Use `team-quick` or any team that does not include the resolver step. Alternatively, set `min_confidence: 100` to suppress all suggestions.

---

## Free tier

### How many reviews do I get on the Free tier?

25 reviews per month. A "review" is one diff processed by the review pipeline, whether run locally via `/revue` or in CI.

### What happens when I hit the limit?

Revue returns a 429 response and posts a comment on the PR explaining that the limit has been reached and linking to the upgrade page.

### Does the counter reset monthly?

Yes, on the first day of each calendar month.

---

## Troubleshooting

### The review ran but posted no comments

- Check that your CI runner has the `pull-requests: write` permission (GitHub Actions).
- Verify that the `GITHUB_TOKEN` / `CI_JOB_TOKEN` is being passed correctly.
- Check the pipeline log for errors from the `revue review` step.
- The diff may have been empty or all files matched `ignore_patterns`.

### "License key is invalid"

- Copy your key fresh from the [dashboard](/dashboard): keys are long and easy to truncate.
- Check for leading/trailing whitespace in the CI secret value.
- If the key was recently regenerated, update the secret.

### The review is very slow

- Increase `agent_timeout_seconds` to 120 in `.revue.yml`.
- Use `team-quick` for rapid feedback on small changes.

### I'm getting "diff too large" on every PR

- Increase `max_diff_lines` in `.revue.yml`.
- Add large generated files to `ignore_patterns` (e.g. `*.lock`, `migrations/*`).
