# Revue v2.0 Launch Post — DRAFT

## Headline
**Revue Cuts Your AI Code Review Bill by 79–88%**

## Subheading
Multi-agent AI code review that catches issues before expensive re-review cycles.

## Opening
Revue is an AI-powered code reviewer that runs inside your CI pipeline — and it cuts your AI API spend by approximately 79–88% compared to using Anthropic Sonnet 4.5 alone.

The insight: most teams run a human code review *and* an AI review for every PR. If the AI catches issues that humans miss, that's valuable. But if the AI misses issues that humans catch, you're paying for the review twice — once upfront, again during re-review. Revue fixes this by running a specialized multi-agent reviewer *before* the human ever sees the PR, catching issues that would otherwise trigger expensive downstream AI re-review cycles.

## The Problem
AI code review is powerful but costly. A typical team of 5 engineers running daily code reviews on Anthropic's Sonnet 4.5 via OpenAI or Anthropic directly will spend:
- **$850–$1,200 per month** in API costs alone
- Multiplied across the year: **$10k–$14k annually** just to review code

And many teams run both:
- Automated AI review (to catch issues early)
- Human code review (to catch what AI misses)

This creates a compounding cost problem: if the AI review misses something, you pay for the review again during human review — and often again if your AI assistant has to re-synthesize the code context to fix the issue.

## The Solution
Revue runs **inside your CI pipeline** with a specialized multi-agent architecture:
- **Security Agent** — finds authentication bypasses, injection vectors, supply-chain risks
- **Performance Agent** — detects O(n²) loops, memory leaks, inefficient queries
- **Architecture Agent** — flags coupling violations, missing error handling, design pattern breaks
- **Code Quality Agent** — enforces style, naming, duplication, and testability
- **Licensing Agent** — identifies GPL, AGPL, and incompatible dependency trees
- **Synthesis Agent** — consolidates findings into actionable, deduplicated review comments

All six agents run in **parallel**. Your code never leaves your infrastructure. You bring your own API key (or use our DeepSeek default via OpenRouter for 79–88% savings).

### Pricing
- **Free**: 25 reviews/month, $0/month
- **Indie**: 100 reviews/month, $9/month (vs. $1,000–$1,400 in raw AI API costs)
- **Pro**: Unlimited reviews, $29/month (vs. $2,000–$2,800 in raw AI API costs)

## The Numbers
Revue uses **DeepSeek-V4-Pro** (via OpenRouter) as the default model. This model achieves code-review quality on par with Sonnet 4.5 while **reducing per-token costs by approximately 87%**. Combined with the operational efficiencies of running review *before* the commit, typical teams see:

| Team size | Without Revue | With Revue | Monthly savings |
|-----------|--------------|-----------|-----------------|
| 5 engineers | $850–$1,200 | $100–$250 | $600–$950 |
| 10 engineers | $1,700–$2,400 | $200–$500 | $1,200–$2,200 |
| 25 engineers | $4,250–$6,000 | $500–$1,250 | $3,000–$5,500 |

(Assumes daily PR activity; costs are for review agents only, excluding infrastructure and licensing.)

## Key Features
- **No code leaves your infrastructure** — Revue runs as a Docker sidecar in your CI runner
- **Bring your own key** — Use OpenAI, Anthropic, Azure, or our default DeepSeek (cheapest)
- **GitHub, GitLab, and Bitbucket** — Supports all three major platforms
- **Configurable noise filters** — Teach Revue about your intentional design decisions
- **Parallel agent execution** — All six agents analyze your code simultaneously

## Availability
Revue v2.0 is available starting [DATE]. Free tier users get 25 reviews/month at no cost. No credit card required.

Visit [revue.io](https://revue.io) to get started.

---

## Notes for Publication
- [ ] Confirm launch date before publication
- [ ] Confirm revue.io domain is registered and matches app branding
- [ ] Embed interactive TCO calculator (stretch goal for Phase 2.b)
- [ ] Cross-post to: Product Hunt, Hacker News, dev.to, Reddit r/devops
- [ ] Reach out to cost-focused DevTools newsletters (Changelog, The Pragmatic Engineer, etc.)
- [ ] Coordinate announcement with website launch (Phase 2.a exit)
