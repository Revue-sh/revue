# Show HN: Revue — AI code review inside your editor, cuts AI API spend by 79–88%

## Submission Title

Show HN: Revue – AI code review skill for Claude Code that cuts your AI bill by 79–88%

## Body

AI coding tools have created a paradox: engineers are shipping code faster than ever, but code review hasn't scaled with them. DORA 2024 found that AI adoption correlates with larger batch sizes and lower delivery stability — more PRs, higher defect density, same number of reviewers. The quality gate has become the bottleneck.

I've been building Revue, an AI code reviewer that runs as a `/revue` skill inside Claude Code. It catches issues before you commit using six agents in parallel. Because it runs inside your existing Claude session, it uses your subscription — no separate API charge.

For CI pipelines, Revue runs as a separate pipeline step and does bill against your API wallet — that's where model choice matters. The default is DeepSeek-V4-Pro via OpenRouter: 87% cheaper per token than Sonnet 4.5. A five-engineer team running CI reviews on Sonnet 4.5 spends roughly $850–$1,200/month; on DeepSeek, $100–$250. You can BYOK — OpenAI, Anthropic, Azure, or any OpenRouter model.

The six agents are: Security (injection vectors, auth bypasses, supply-chain), Performance (O(n²) loops, memory, query plans), Architecture (coupling, error handling, design patterns), Code Quality (style, duplication, testability), Licensing (GPL/AGPL checks), and a Synthesis agent that merges and deduplicates findings and prints them to the terminal.

The `/revue` skill runs inside Claude Code without Docker or CI changes. It reads your staged diff, runs the agents, and prints findings to the terminal. Your Claude session can see the output, validate each finding, and apply fixes on the spot — before you ever open a PR.

You can also add a pipeline step in your CI (GitHub Actions, GitLab CI, Bitbucket Pipelines) for automated PR reviews.

Supported platforms: GitHub, GitLab, Bitbucket. Free tier, no credit card required.

Install:

    claude skill install revue

https://revue.sh

Try it on your next PR and let me know what you find — especially where it gets it wrong.

[CONFIRM: launch date to include in submission]

## Notes

- Post on a weekday, 09:00–12:00 US Eastern (peak HN engagement window)
- Do not cross-post to HN on the same day as Product Hunt — stagger by 24–48 hours
- Anticipated discussion angles: model choice justification (DeepSeek), security posture (BYOK, no code leaves infra), multi-agent reliability, false-positive rate
- Prepare honest answers for: "why not just use the built-in Claude Code review?", "what's the false positive rate?", "how does it handle large PRs?"
