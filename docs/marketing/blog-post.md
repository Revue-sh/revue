# Technical Blog Post Draft

**Target**: dev.to
**Estimated read time**: 5–7 minutes
**Audience**: Developers using Claude Code or running AI in CI

---

# I built a code reviewer for myself. My API bill taught me the most important lesson.

I've been building Revue since March.

It started as a personal tool — I wanted proper multi-agent code review running inside my development workflow, not a one-shot "ask Claude to review this PR" prompt. By April I had the core working and was testing it intensively.

Then I got a £40.50 invoice from Anthropic. On top of my £90 Max subscription.

I'd been hammering Sonnet 4.5 through the API — 1.5 to 2 million input tokens a day for two weeks straight — and hadn't noticed the cost accumulating until it hit. About $79 in raw API costs in 13 days.

The irony was hard to ignore. I was building a tool to make AI-assisted development more efficient, and I was doing it as inefficiently as possible.

## What I got wrong about AI review costs

The expensive habit isn't using AI for code review. It's conflating two different things:

**API tokens** — you pay per token, billed separately from any subscription.

**Subscription tokens** — already paid for. Your Max plan, your Cursor seat, whatever you're on. Using these for review costs nothing extra.

When developers ask their AI assistant to "review this before I push," they're often spending API tokens on something their subscription already covers — and if the review finds issues and they iterate, they pay again for the re-synthesis.

The fix is straightforward: move the review inside your session, before you commit.

## How Revue works

`/revue` is a Claude Code skill. You run it against your staged diff before committing — inside your existing Claude Code session. Subscription tokens. No separate API charge.

Six specialised agents run in parallel:

- **Security** — injection vectors, auth bypasses, supply-chain risks
- **Performance** — O(n²) loops, memory leaks, inefficient queries
- **Architecture** — coupling violations, missing error handling
- **Code Quality** — naming, duplication, testability
- **Licensing** — GPL/AGPL compatibility in dependencies
- **Synthesis** — deduplicates findings across agents, formats as actionable comments

Each agent gets a narrow brief. Security reasoning is different from performance reasoning — running them together in one prompt creates context pressure and dilutes both. Running them in parallel means the full review takes roughly as long as the slowest single agent.

## The CI cost question

For CI pipelines, `/revue` runs as a sidecar in GitHub Actions, GitLab CI, or Bitbucket Pipelines. Here the cost does come from your API wallet — and model choice matters.

I switched from Sonnet 4.5 to DeepSeek-V4-Pro via OpenRouter and tracked the numbers across my own workload.

April (Sonnet, 13 days): ~$79 API costs.
May–June (DeepSeek, 22 days of equal or heavier work): $27 total.

The most recent heavy day — a full implementation sprint — cost $4.30 on DeepSeek. Same token volume on Sonnet: ~$15. 72% cheaper.

That's not a Revue feature. It's a model recommendation. DeepSeek-V4-Pro handles code review quality on par with Sonnet 4.5 at a fraction of the cost — so we default to it, and we're honest that the saving comes from the model choice, not from anything clever in the architecture.

You can BYOK: OpenAI, Anthropic, Azure, or any OpenRouter model. Your diff is the only thing that leaves your machine.

## Who this is for

If you're doing AI-assisted development and finding your API bill growing, the `/revue` local workflow is the most direct fix — you're already paying for the subscription.

If you're running AI review in CI and using GPT-4 or Sonnet class models, switching to DeepSeek will cut costs significantly. Revue makes it easy to configure, but the saving is the model switch.

## Try it

Revue is live. Free tier: 25 reviews/month, no credit card.

```bash
# [CONFIRM: exact install command once registry is live]
claude skill install revue
```

https://revue.sh

I've been the primary user since March. Happy to answer questions about the architecture, the false positive rate, or how the synthesis layer handles conflicting findings between agents.

---

**Post notes**
- Use the real numbers — don't round or inflate them
- The £40.50 invoice screenshot can be embedded if dev.to supports it (it does)
- Tag: `claudecode`, `ai`, `devtools`, `costreduction`
- The "this is a model recommendation, not a Revue feature" line is intentional — technical readers respect that honesty
- [CONFIRM install command and URL before publishing]
