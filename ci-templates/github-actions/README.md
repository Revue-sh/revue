# Revue.io GitHub Actions Integration

Add automated AI code reviews to your PRs in 2 minutes.

## Quick Setup

1. Copy `.github/workflows/revue-review.yml` to your repo
2. Add your Anthropic API key as a GitHub secret:
   - Go to Settings → Secrets → Actions
   - Click "New repository secret"
   - Name: `ANTHROPIC_API_KEY`
   - Value: your API key (starts with `sk-ant-`)
3. Open a PR — Revue will review it automatically

## Configuration

Create `.revue.yml` in your repo root to customize:

```yaml
version: "1"
ai:
  provider: anthropic
  model: claude-sonnet-4-5-20250929
review:
  max_diff_lines: 2000
  min_confidence: 70
  ignore_patterns:
    - "*.md"
    - "*.lock"
agents:
  team: team-full-review
```

## Troubleshooting

- **No comments posted**: Check the Actions log for errors. Ensure `ANTHROPIC_API_KEY` secret is set.
- **Permission denied**: Workflow needs `pull-requests: write` permission (already in template).
- **Rate limits**: Free tier: 100 reviews/month. Upgrade at revue.io/pricing.
