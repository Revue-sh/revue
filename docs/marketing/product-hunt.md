# Product Hunt Launch Draft

## Tagline (max 60 characters)

AI code review that slashes your AI API bill by 79–88%

*(57 chars)*

## Short Description (260-char pitch)

Revue is a multi-agent AI code reviewer that runs inside Claude Code before every commit. Six agents catch security, performance, and architecture issues in parallel. Default model: DeepSeek-V4-Pro — 87% cheaper than Sonnet 4.5.

*(240 chars)*

## Gallery Captions

1. **The /revue skill** — Run `/revue` inside Claude Code on any staged diff. Six agents run in parallel; findings appear in your terminal before you push.
2. **Six-agent architecture** — Security, Performance, Architecture, Code Quality, Licensing, and Synthesis agents run simultaneously. No single-model bottleneck.
3. **Cost comparison** — DeepSeek-V4-Pro vs Anthropic Sonnet 4.5: ~87% per-token savings with equivalent code-review quality.
4. **Platform integrations** — GitHub, GitLab, and Bitbucket. CI sidecar mode for automated PR comments in any pipeline.
5. **Pricing** — Free (25/month, no CC), Indie ($9/month, 100 reviews), Pro ($29/month, unlimited).

## Longer Description

Revue is an AI code reviewer for teams already paying for AI development tools who don't want a separate bill just to review their code.

**How it works**

Install the `/revue` Claude Code skill. Before every commit, run it against your staged diff. Revue dispatches six agents in parallel — Security, Performance, Architecture, Code Quality, Licensing, and Synthesis — and returns findings directly in your editor session, with duplicates merged.

No Docker. No CI setup required to get started. Just a skill install.

**The cost story**

Revue routes to DeepSeek-V4-Pro via OpenRouter by default — 87% cheaper per token than Anthropic Sonnet 4.5 with equivalent code-review performance.

A five-engineer team running daily reviews on Sonnet 4.5 spends roughly $850–$1,200/month. With Revue, that drops to $100–$250/month.

You can bring your own key (OpenAI, Anthropic, Azure, any OpenRouter model). Your code stays on your machine; only the diff is sent to the API you configure.

**Platform support**

GitHub, GitLab, and Bitbucket. Runs as a local Claude Code skill or as a CI sidecar (GitHub Actions, GitLab CI, Bitbucket Pipelines).

**Pricing**

- Free: 25 reviews/month — no credit card required
- Indie: $9/month — 100 reviews
- Pro: $29/month — unlimited reviews

**First comment (maker comment)**

Hi PH 👋 — I'm [CONFIRM: maker name], and I built Revue after watching our AI API costs compound every sprint.

The insight: most teams pay for the same code review twice. AI writes code and misses something. A human catches it. Then AI reruns on the full context to fix it. That loop is expensive. Running review before the commit stops it from happening.

Questions about the architecture, DeepSeek choice, BYOK setup, or how the synthesis layer handles conflicting findings are welcome.

https://revue.sh

## Launch Logistics

- **Category**: Developer Tools
- **Topics**: Artificial Intelligence, Developer Tools, Productivity
- **Hunter**: [CONFIRM: hunter name and PH profile]
- **Launch day**: [CONFIRM: launch date — avoid Monday; Tuesday or Wednesday preferred]
- **Launch time**: 00:01 PT (PH resets daily at midnight PT — first-mover advantage)
- **Maker profile**: [CONFIRM: PH maker account created and linked]
- **Gallery assets needed**: 5 screenshots or screen recordings [CONFIRM: designed]
- **Promo video**: Optional but recommended — 60-second walkthrough of `/revue` in action [CONFIRM]

## Pre-launch Checklist

- [ ] PH account created and verified
- [ ] Maker profile completed
- [ ] Hunter confirmed and briefed
- [ ] Gallery assets designed and uploaded
- [ ] revue.sh live and load-tested before launch
- [ ] Pricing page matches launch post numbers
- [ ] Free trial sign-up flow tested end-to-end
- [ ] Support email / Discord ready to receive inbound
