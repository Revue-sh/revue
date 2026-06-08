# LinkedIn Launch Post

**Format**: Personal founder story
**Attach**: Invoice screenshot (Apr 30 £40.50 overage)
**Post timing**: 08:00–10:00 local, Tuesday or Wednesday

---

In April, Anthropic sent me an unexpected invoice for £40.50.

I'm already on the Max subscription — that's £90/month. This was on top of it. Pure API overage.

I was building an AI code review tool and testing it heavily with Sonnet 4.5. About 1.5–2 million tokens a day for two weeks straight. I didn't notice until the bill landed.

The irony: I was building a tool to make AI-assisted development smarter, and I was doing it in the most expensive way possible.

That bill forced me to actually think about where AI costs come from in a development workflow.

Here's what I worked out:

The problem isn't that AI code review is expensive. It's *when* you run it and *which wallet* it comes from. When you ask your AI assistant to review a PR mid-session, you're spending API tokens on something your Max subscription already covers. And if it finds something and you iterate — you pay again for the re-review.

So I changed the approach.

Revue runs as `/revue` directly inside your Claude Code session — before you commit. You review your staged diff against the work in progress, inside the session you're already in. Max subscription. No separate API charge.

For CI pipelines, I switched the default model to DeepSeek-V4-Pro via OpenRouter and tracked the numbers properly.

April (Sonnet 4.5, 13 days of intensive work): ~$79 in API costs.
May–June (DeepSeek, 22 days of equal or heavier work): $27 total.

The heaviest recent day — a full implementation sprint — cost $4.30 on DeepSeek. The same token volume on Sonnet would have been ~$15. 72% cheaper, not because of anything clever, just because I stopped using an expensive model where a cheaper one works just as well.

Today I'm launching Revue publicly.

It's a multi-agent code reviewer: six specialised agents run in parallel against your diff — security, performance, architecture, code quality, licensing, and a synthesis layer that deduplicates and formats the findings. Works with GitHub, GitLab, and Bitbucket. BYOK — configure any model you trust, or use our DeepSeek default.

I've been building this since March. Free tier is 25 reviews/month, no credit card.

https://revue.sh

If you're doing AI-assisted development and your API costs are growing, I'd genuinely like to know how this holds up on your codebase.

---

**Post notes**
- The invoice screenshot is the strongest element — attach it
- Do not edit the £40.50 number or soften the "I made a mistake" framing — that's what makes it credible
- First comment: pin install command + link once confirmed
- Keep line breaks exactly as written — LinkedIn collapses long paragraphs on mobile
