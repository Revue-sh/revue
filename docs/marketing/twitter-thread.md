# Twitter/X Thread Draft

## Thread

---

**Tweet 1 — Hook**

We've been running AI code review on every PR for 6 months.

Our AI API bill: down 83%.
Review quality: up.

Here's how. 🧵

---

**Tweet 2 — The problem**

Most teams using AI code review pay for the same review 2–3 times.

AI writes code → misses edge case → human catches it → AI re-synthesises full context to fix → re-review.

Each cycle compounds cost. The bottleneck isn't the code — it's the loop.

---

**Tweet 3 — The /revue insight**

The fix: catch issues *before* the commit, not after.

/revue is a Claude Code skill that runs a full review against your staged diff, right inside your editor session — before CI, before the PR, before any human sees it.

---

**Tweet 4 — Multi-agent architecture**

It's not "ask Claude to review this."

Six agents run in parallel:

🔐 Security — injection, auth, supply chain
⚡ Performance — O(n²), memory, queries
🏗 Architecture — coupling, error handling
✨ Quality — style, duplication, testability
📄 Licensing — GPL/AGPL detection
🔀 Synthesis — deduplicates, formats findings

---

**Tweet 5 — The cost breakdown**

Default model: DeepSeek-V4-Pro via OpenRouter.

~87% cheaper per token than Anthropic Sonnet 4.5.
Code-review quality: on par.

5-engineer team, daily reviews:
• Without Revue (Sonnet 4.5): $850–$1,200/month
• With Revue (DeepSeek default): $100–$250/month

---

**Tweet 6 — BYOK**

Don't want to switch models? Don't have to.

Revue is BYOK — OpenAI, Anthropic, Azure, or any OpenRouter model. Your code never leaves your machine; only the diff goes to the API you configure.

DeepSeek is just the default because it's the cheapest good model for review.

---

**Tweet 7 — Platforms**

Works with GitHub, GitLab, and Bitbucket.

Runs as:
• `/revue` inside Claude Code
• CI sidecar in GitHub Actions / GitLab CI / Bitbucket Pipelines

---

**Tweet 8 — Pricing**

Pricing is designed so the savings are obvious even on the free tier:

Free: 25 reviews/month, $0, no credit card
Indie: 100 reviews/month, $9/month
Pro: Unlimited, $29/month

At $9/month you're saving $600–$1,000/month if you're currently running Sonnet 4.5.

---

**Tweet 9 — CTA**

Revue is live at https://revue.sh.

Install the Claude Code skill:
`claude skill install revue`

[CONFIRM: exact install command]

Free tier, no CC. Try it before your next PR.

---

**Tweet 10 — Reply bait**

Ask me about:
— why six agents and not one
— how the synthesis layer handles conflicting findings
— what the false positive rate looks like across different codebases

Ask below 👇

---

## Thread Notes

- Post on [CONFIRM: launch day], 09:00–11:00 US Eastern
- Thread should read as a narrative, not a list of features
- Retweet tweet 1 from personal accounts + team accounts immediately after posting
- Pin tweet 1 reply with the GitHub repo link [CONFIRM: repo URL] for technical credibility
- Do not post on the same day as Show HN — stagger by 24–48 hours
- Monitor for replies about DeepSeek trust/privacy concerns — have a canned response ready (BYOK, diff-only API calls, no storage)
