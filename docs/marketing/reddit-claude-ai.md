# r/ClaudeAI Post Draft

## Subreddit
r/ClaudeAI

## Title
I built a `/revue` skill for Claude Code — multi-agent code review before you commit, using your subscription not your API wallet

## Body

I've been building this since March, mostly as a tool for myself, and I'm launching it publicly today.

**The problem that pushed me to build it**

In April I got a £40.50 invoice from Anthropic — extra, on top of my Max subscription. I was testing my own tool intensively using Sonnet 4.5 through the API. About 1.5–2M tokens a day for two weeks. It added up to ~$79 in API costs before I noticed.

The thing I got wrong: I was treating "ask Claude to review my code" as a free operation because I have Max. It's not free when it's going through the API. And when a review finds something and you iterate, you're paying for the re-synthesis too.

**How `/revue` fixes this**

Run it inside your Claude Code session, before you commit. It reads your staged diff and runs six specialised agents in parallel:

- Security (injection, auth, supply-chain)
- Performance (O(n²), memory, queries)  
- Architecture (coupling, error handling)
- Code Quality (naming, duplication, testability)
- Licensing (GPL/AGPL in dependencies)
- Synthesis (deduplicates across agents, formats findings)

Because it runs inside your Claude Code session, it uses your Max subscription. No API charge.

**The CI cost angle (honest version)**

For CI pipelines there's a separate API cost, and the saving there comes from model choice, not from Revue itself. I switched to DeepSeek-V4-Pro via OpenRouter and tracked my numbers:

- April on Sonnet: ~$79 in 13 days
- May–June on DeepSeek: $27 over 22 days (same or heavier workload)
- Heaviest single day: $4.30 DeepSeek vs ~$15 equivalent Sonnet — 72% cheaper

DeepSeek is our default because it handles code review quality well at a much lower cost. But you can BYOK — any OpenAI, Anthropic, Azure, or OpenRouter model. Only your diff goes to the API.

**Platforms + pricing**

GitHub, GitLab, Bitbucket. Free tier: 25 reviews/month, no credit card.

```bash
# [CONFIRM: exact command once registry live]
claude skill install revue
```

[CONFIRM: revue.io]

Happy to answer questions about the architecture or the DeepSeek model choice — I know that one comes up.

---

**Post notes**
- Tone is right: developer talking to developers, not a product launch
- The "honest version" subheading for CI costs is intentional — r/ClaudeAI readers will probe the claims
- Post 10:00–13:00 UTC, Tuesday or Wednesday for best visibility
- First comment immediately after posting: pin install command + link
- Likely questions to prepare for: BYOK setup, false positive rate, what happens with large PRs, privacy (does code leave machine?)
- [CONFIRM install command and URL before posting]
