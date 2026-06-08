# r/ClaudeAI Post Draft

## Subreddit

r/ClaudeAI

## Title

I built a `/revue` skill for Claude Code that does a full AI code review before every commit — and it uses DeepSeek by default so it doesn't eat your Anthropic budget

## Body

If you're using Claude Code for day-to-day development, you've probably noticed that asking Claude to "review this PR" burns a surprising amount of context and tokens. I ran the numbers for my team and we were spending more on re-review cycles than on the original implementation — Claude would write code, miss something, a human would catch it, Claude would re-synthesise the full context to fix it. The cost compounds.

So I built Revue as a Claude Code skill — it runs as `/revue` directly inside your Claude Code session.

**What it does**

When you run `/revue` before a commit, it spins up six specialised agents in parallel against your staged diff:

- **Security** — injection vectors, auth bypasses, dependency risks
- **Performance** — O(n²) loops, memory leaks, inefficient queries
- **Architecture** — coupling violations, missing error handling, design pattern breaks
- **Code Quality** — style, naming, duplication, testability
- **Licensing** — GPL/AGPL incompatibility detection
- **Synthesis** — deduplicates findings and formats them as review comments

The key thing: it runs *before you commit*, not after. That's when it's cheapest to fix something.

**The cost angle**

By default, Revue routes to **DeepSeek-V4-Pro via OpenRouter** — about 87% cheaper per token than Anthropic Sonnet 4.5. You can BYOK if you'd rather use Anthropic, OpenAI, or Azure. No code leaves your machine; Revue reads your local diff and sends only that to the model API you configure.

For context: a five-person team doing daily reviews with Sonnet 4.5 typically spends $850–$1,200/month. With Revue's DeepSeek default, that drops to roughly $100–$250/month.

**How to install**

```bash
# Install the Claude Code skill
claude skill install revue
```

Then invoke it from any Claude Code session with `/revue`.

[CONFIRM: exact install command once registry listing is live]

**Platforms supported**

GitHub, GitLab, and Bitbucket. `/revue` runs inside Claude Code.

**Pricing**

Free tier: 25 reviews/month, no credit card required.
Indie: $9/month, 100 reviews.
Pro: $29/month, unlimited.

[CONFIRM: revue.io URL]

---

Happy to answer questions about the architecture, the model choice, or how the synthesis layer handles conflicting findings across agents.

## Post Notes

- Tone: conversational, first-person, genuine — not a product announcement
- Lead with the `/revue` angle specifically because this is r/ClaudeAI — the community uses Claude Code daily
- Anticipate questions about: BYOK config, false positive rate, large PR handling, privacy (does code leave machine?)
- Upvote window: post 10:00–13:00 UTC on a Tuesday or Wednesday
- Pin a maker comment immediately after posting with: install link, GitHub repo link [CONFIRM], and a brief FAQ
