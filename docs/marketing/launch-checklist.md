# Launch Day Checklist — Revue

**Launch date:** [CONFIRM: launch date]
**Launch coordinator:** [CONFIRM: name]

---

## Go / No-Go Gates

All gates must be GREEN before any channel goes live. A single RED gate blocks the full launch.

| Gate | Owner | Status |
|------|-------|--------|
| revue.io is live and SSL-valid | Engineering | [ ] |
| Free tier sign-up flow tested end-to-end (no CC required) | Engineering | [ ] |
| `/revue-local` skill published to Claude Code registry | Engineering | [ ] |
| Pricing page numbers match all channel drafts | Marketing | [ ] |
| CI integrations tested against GitHub, GitLab, and Bitbucket | Engineering | [ ] |
| DeepSeek default model operational (OpenRouter key active) | Engineering | [ ] |
| Support channel live (email / Discord / GitHub issues) | Operations | [ ] |
| Legal: BYOK terms of service reviewed | Legal | [ ] |
| Legal: data handling / diff privacy statement live on revue.io | Legal | [ ] |
| Product Hunt hunter confirmed and launch page approved | Marketing | [ ] |

---

## T-7 Days (One Week Before)

- [ ] Final review of all five channel drafts against brand voice checklist
- [ ] All `[CONFIRM: ...]` markers in channel drafts resolved and replaced with real values
- [ ] Product Hunt gallery assets designed and uploaded to PH draft
- [ ] Product Hunt maker profile completed
- [ ] Hunter briefed with draft description and gallery
- [ ] Twitter account warmed up (at least 3 non-launch tweets in the week prior)
- [ ] Technical blog post submitted to dev.to / revue.sh/blog (schedule for launch day)
- [ ] Email list / waitlist notification drafted (if applicable)
- [ ] Internal team briefed on launch day schedule and their roles
- [ ] Monitoring alerts configured: uptime, error rate, API cost spike

---

## T-1 Day (Day Before Launch)

- [ ] Go/No-Go gate review — all GREEN?
- [ ] Load test revue.io sign-up and onboarding flow
- [ ] Stage tweets in scheduling tool (do not publish yet)
- [ ] Confirm Product Hunt launch time: 00:01 PT
- [ ] Confirm Show HN submission draft saved (do not submit yet)
- [ ] Confirm r/ClaudeAI post staged
- [ ] Confirm technical blog post scheduled for launch day
- [ ] Briefing call / message with any advisors / early users asked to upvote PH

---

## Launch Day Timeline

All times are UTC. Adjust for [CONFIRM: primary team timezone].

| Time (UTC) | Action | Owner |
|-----------|--------|-------|
| 07:01 | Product Hunt goes live (00:01 PT) | Marketing |
| 07:05 | Maker comment posted on PH | Marketing |
| 07:10 | Email early-access / waitlist list | Marketing |
| 07:15 | Twitter/X thread published | Marketing |
| 08:00 | Technical blog post published | Engineering |
| 10:00 | r/ClaudeAI post submitted | Marketing |
| 11:00 | Show HN submitted (peak HN window) | Marketing |
| 11:05 | Show HN — initial comment with GitHub repo link | Marketing |
| 12:00 | First engagement check: PH comments, HN comments, Reddit replies | Marketing |
| 15:00 | Mid-day engagement round: respond to all outstanding comments | Marketing |
| 18:00 | End-of-day recap: sign-ups, PH ranking, HN points, Reddit upvotes | Marketing |

---

## Post-Launch (T+1 to T+7)

- [ ] Monitor sign-up conversion rate (free tier activation)
- [ ] Respond to all HN, PH, and Reddit comments within 24 hours
- [ ] Collect and triage product feedback for next sprint
- [ ] Share metrics internally: sign-ups, free-to-paid conversion, API cost per review
- [ ] Identify top three feature requests surfaced by launch channels
- [ ] Write brief launch retrospective (what landed, what didn't, what to do differently)
- [ ] Plan follow-up content: changelog post, "how we built the synthesis agent" deep-dive, etc.

---

## Contingency

| Scenario | Response |
|----------|----------|
| revue.io down at launch time | Roll back to previous deploy; delay channels by 2 hours; post status update |
| OpenRouter / DeepSeek outage | Switch default model to Sonnet 4.5 temporarily; update pricing page note |
| Claude Code registry publish failure | `/revue-local` install unavailable; update r/ClaudeAI post to remove install command; add waitlist CTA |
| Product Hunt launch page rejected | Move to next available Tuesday; re-submit with amended gallery |
| Negative HN thread (privacy / security concerns) | Prepared response: BYOK architecture, diff-only API calls, no code storage, open-source diff sanitiser |

---

## Channel Summary

| Channel | File | Target publish window |
|---------|------|-----------------------|
| Product Hunt | `product-hunt.md` | Launch day 00:01 PT |
| Twitter/X thread | `twitter-thread.md` | Launch day 09:00–11:00 US ET |
| r/ClaudeAI | `reddit-claude-ai.md` | Launch day 10:00–13:00 UTC |
| Show HN | `show-hn.md` | Launch day 11:00–12:00 US ET |
| Technical blog | `blog-post.md` | Launch day 08:00 UTC |
