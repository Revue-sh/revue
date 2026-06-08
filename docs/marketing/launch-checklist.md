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
| `/revue` skill published to Claude Code registry | Engineering | [ ] |
| Pricing page numbers match all channel drafts | Marketing | [ ] |
| CI integrations tested against GitHub, GitLab, and Bitbucket | Engineering | [ ] |
| DeepSeek default model operational (OpenRouter key active) | Engineering | [ ] |
| Support channel live (email / Discord / GitHub issues) | Operations | [ ] |
| Legal: BYOK terms of service reviewed | Legal | [ ] |
| Legal: data handling / diff privacy statement live on revue.io | Legal | [ ] |
| Product Hunt hunter confirmed and launch page approved | Marketing | [ ] |

---

## T-7 Days (One Week Before)

- [ ] Review all five channel drafts against brand voice guidelines
- [ ] Replace all `[CONFIRM: ...]` markers in channel drafts with real values
- [ ] Design and upload Product Hunt gallery assets to PH draft
- [ ] Complete Product Hunt maker profile
- [ ] Brief the hunter with draft description and gallery
- [ ] Warm up Twitter account (post at least 3 non-launch tweets in the week prior)
- [ ] Submit technical blog post to dev.to / revue.sh/blog (schedule for launch day)
- [ ] Draft email / waitlist notification (if needed)
- [ ] Brief the team on launch day schedule and roles
- [ ] Set up monitoring alerts: uptime, error rate, API cost spike

---

## T-1 Day (Day Before Launch)

- [ ] Go/No-Go gate review — all GREEN?
- [ ] Run load test on revue.io sign-up and onboarding
- [ ] Schedule tweets in tool (don't publish yet)
- [ ] Confirm Product Hunt launch time: 00:01 PT
- [ ] Confirm Show HN draft is saved (don't submit yet)
- [ ] Confirm r/ClaudeAI post is staged
- [ ] Confirm technical blog post is scheduled for launch day
- [ ] Message advisors and early users about PH upvotes (if applicable)

---

## Launch Day Timeline

All times are UTC. Adjust for [CONFIRM: primary team timezone].

| Time (UTC) | Action | Owner |
|-----------|--------|-------|
| 07:01 | Product Hunt goes live (00:01 PT) | Marketing |
| 07:05 | Post maker comment on PH | Marketing |
| 07:10 | Email early-access and waitlist | Marketing |
| 07:15 | Post Twitter/X thread | Marketing |
| 08:00 | Publish technical blog post | Engineering |
| 10:00 | Submit r/ClaudeAI post | Marketing |
| 11:00 | Submit Show HN (peak window) | Marketing |
| 11:05 | Post GitHub repo link on Show HN | Marketing |
| 12:00 | First check: scan PH comments, HN comments, Reddit replies | Marketing |
| 15:00 | Mid-day round: respond to all outstanding comments | Marketing |
| 18:00 | End-of-day recap: track sign-ups, PH ranking, HN points, Reddit upvotes | Marketing |

---

## Post-Launch (T+1 to T+7)

- [ ] Monitor sign-up conversion rate (free tier activation)
- [ ] Reply to all HN, PH, and Reddit comments within 24 hours
- [ ] Read and categorize product feedback for next sprint
- [ ] Share metrics with the team: sign-ups, free-to-paid conversion, API cost per review
- [ ] Flag the top three feature requests from launch channels
- [ ] Write a brief launch retrospective (what worked, what didn't, what to change)
- [ ] Plan follow-up content: changelog post, agent deep-dive, etc.

---

## Contingency

| Scenario | Response |
|----------|----------|
| revue.io down at launch time | Roll back to previous deploy; delay channels by 2 hours; post status update |
| OpenRouter / DeepSeek outage | Switch default model to Sonnet 4.5 temporarily; update pricing page note |
| Claude Code registry publish failure | `/revue` install unavailable; update r/ClaudeAI post to remove install command; add waitlist CTA |
| Product Hunt launch page rejected | Move to next available Tuesday; resubmit with amended gallery |
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
