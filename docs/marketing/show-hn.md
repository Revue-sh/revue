# Show HN: Revue — AI code review inside your editor, cuts AI API spend by 79–88%

## Submission Title

Show HN: Revue – AI code review skill for Claude Code that cuts your AI bill by 79–88%

## Body

I've been building Revue, an AI code reviewer that runs as a `/revue-local` skill inside Claude Code (Cursor and Windsurf support coming). It catches issues before you commit — using six specialised agents running in parallel — and by default it routes to DeepSeek-V4-Pro via OpenRouter, which is ~87% cheaper per token than Anthropic Sonnet 4.5.

The positioning is cost-first because that's the real unlock. A team of five engineers doing daily code reviews with Sonnet 4.5 typically spends $850–$1,200/month in raw API costs. With Revue's DeepSeek default, that drops to $100–$250. You can BYOK (OpenAI, Anthropic, Azure, or any OpenRouter model) if you'd rather use what you're already paying for.

The architecture is six agents in parallel: Security (injection vectors, auth bypasses, supply-chain), Performance (O(n²) loops, memory, query plans), Architecture (coupling, error handling, design patterns), Code Quality (style, duplication, testability), Licensing (GPL/AGPL tree checks), and a Synthesis agent that deduplicates and formats findings into actionable review comments posted directly to your PR.

The `/revue-local` skill runs inside your existing Claude Code session — no Docker, no CI change required to start. It reads your staged diff, runs the agent panel, and returns findings in your terminal. You can also configure it in CI (GitHub Actions, GitLab CI, Bitbucket Pipelines) as a sidecar for automated PR comments.

Supported platforms: GitHub, GitLab, Bitbucket.

Pricing: Free (25 reviews/month, no credit card), Indie $9/month (100 reviews), Pro $29/month (unlimited).

Happy to discuss the multi-agent architecture, the cost methodology, or why I picked DeepSeek as the default. I'll be around to answer questions.

[CONFIRM: launch date to include in submission]
[CONFIRM: revue.io URL live and pointing to app]

## Notes

- Post on a weekday, 09:00–12:00 US Eastern (peak HN engagement window)
- Do not cross-post to HN on the same day as Product Hunt — stagger by 24–48 hours
- Anticipated discussion angles: model choice justification (DeepSeek), security posture (BYOK, no code leaves infra), multi-agent reliability, false-positive rate
- Prepare honest answers for: "why not just use the built-in Claude Code review?", "what's the false positive rate?", "how does it handle large PRs?"
