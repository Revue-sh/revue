# Revue — Enterprise Sales Playbook
**Version:** 1.0
**Date:** March 2026
**Status:** Draft

---

## Overview

Revue uses a three-tier Enterprise structure with escalating human involvement based on deal size:

| Sub-tier | Seats | Process | Human involvement |
|----------|-------|---------|-------------------|
| **Enterprise Starter** | 1–10 | Fully self-serve | None |
| **Enterprise Growth** | 11–50 | Self-serve + light review | ~5 min/lead |
| **Enterprise Plus** | 51+ | High-touch sales | Full sales cycle |

---

## Enterprise Starter (1–10 seats) — Fully Automated

**Flow:**
1. User clicks "Enterprise Starter" on pricing page
2. Form: company name, work email, GitHub/GitLab org URL, team size (1–10)
3. **Auto-verification:**
   - Email domain is corporate (not Gmail/Outlook/Yahoo)
   - GitHub/GitLab org exists, has >5 repos, >10 commits/month
4. **Auto-issue:**
   - License key sent instantly via email
   - 30-day trial, then auto-bill $59/month
5. Welcome email with quickstart guide

**Cost to operate:** ~$0 (email automation only)

---

## Enterprise Growth (11–50 seats) — Light Touch

**Flow:**
1. User clicks "Enterprise Growth" on pricing page
2. Form: company, work email, org URL, team size, use case dropdown
3. **Auto-verification** (same as Starter)
4. **Slack alert to sales:** "New Enterprise Growth signup: Acme Corp, 25 seats, Startup"
5. Sales reviews within 4 hours (approve 99% of cases)
6. If approved: auto-issue 30-day trial key
7. Welcome email includes optional "setup call" link (Calendly)

**Cost to operate:** ~$50/month (sales time: ~5 min/lead, ~10 leads/month)

---

## Enterprise Plus (51+ seats) — High-Touch Sales

### Step 1: Pre-Qualification (Smart Chatbot)

**Tool:** Intercom (~$99/month) or custom OpenAI chatbot (~$50/month at volume)

**Chatbot script:**
```
Bot: "Hi! I see you're interested in Revue for a large team. A few quick questions to make sure we find the right fit for you."

Q1: "What's your biggest code review challenge today?"
    → [Options: Review bottleneck / Quality inconsistency / Security gaps / Something else]

Q2: "How many PRs does your team merge per week?"
    → [Options: <10 / 10–50 / 50–200 / 200+]

Q3: "What's your timeline for a decision?"
    → [Options: Next 30 days / Next 60 days / Just exploring]

Q4: "What's your approximate budget range?"
    → [Options: $2K–5K/yr / $5K–15K/yr / $15K–50K/yr / $50K+/yr]
```

**Routing logic:**
- **Qualified (high-intent):** Timeline <60 days + budget >$5K → Auto-schedule Calendly call, send pre-call email
- **Unqualified (tire-kicker):** Just exploring + budget <$2K → Offer Enterprise Growth trial instead
- **Unclear:** Add to nurture sequence (monthly check-in email)

**Pre-call email includes:**
- 3-minute demo video
- 1 relevant case study (match their industry if possible)
- Calendly link to book 45-min slot

---

### Step 2: Discovery & Demo Call (45 min)

#### Pre-Call Prep (5 min before)
- Review chatbot answers
- Look up company on LinkedIn: industry, funding, team size, tech stack
- Check their GitHub/GitLab org: languages, PR volume, repo activity
- Identify likely pain points based on industry (fintech → security, startup → velocity)

---

#### Introduction (2 min)
> "Hi [Name], thanks for taking the time. I'm [Your Name] from Revue. Before we dive in — you mentioned [pain point from chatbot]. Tell me a bit more about what's driving that."

*Listen actively. Don't pitch yet.*

---

#### Discovery (15 min)

**Current process:**
> "Walk me through what happens after a developer opens a PR today."

*Listen for: manual reviews, bottlenecks, senior dev overload, inconsistent quality*

**Pain points:**
> "What's the biggest frustration with your current review process?"

*Common: slow reviews, junior devs need guidance, security/compliance gaps, AI-generated code quality*

**Tech stack:**
> "What languages and frameworks are you primarily using? Any monorepos?"

*Confirms Revue supports their stack; flags custom agent needs*

**Decision process:**
> "Who else is involved in this decision? Engineering manager, CTO, security team?"

*Identifies stakeholders; flags if another call is needed*

**Timeline:**
> "What's your timeline? Are you looking to start in the next 30 days?"

*Qualifies urgency; helps prioritise pipeline*

---

#### Demo (15 min)

> "Let me show you how Revue works on a real PR. Do you have a public repo I can use, or should I use a demo?"

**If they share a repo:** Use it. This is the best possible demo — shows Revue working on their actual code.

**If not:** Use a demo repo with a similar tech stack to theirs.

**Walk through:**
1. PR opened → Revue auto-triggered (show CI log)
2. Cleo analyses diff, routes to relevant agents
3. Zara (Security) flags a potential injection risk
4. Kai (Performance) spots an N+1 query
5. Maya (Quality) notes a SOLID violation
6. Nova consolidates and prioritises all findings
7. Sage proposes a 1-click fix for the security issue
8. Show the resulting PR comments (inline + summary)

**Key points to land:**
- "This runs in ~90 seconds, before any human reviewer looks at it"
- "Your team customises the rules — e.g., enforce your internal API standards in `.revue.yml`"
- "Your code never leaves your CI environment — not our servers, not our logs"
- "They use their own OpenAI or Anthropic key, so they control the data policy"

---

#### Pricing (10 min)

> "Based on what you've shared — a team of [X] — here's how Enterprise Plus works:"

| Option | Seats | Price | Includes |
|--------|-------|-------|---------|
| Enterprise Growth | 11–50 | $149/mo ($1,249/yr) | All 6 agents, email support, SLA |
| Enterprise Plus (Standard) | 51–100 | Custom | + Dedicated support, Docker image |
| Enterprise Plus (Volume) | 101–250 | Custom | + Hardware-bound license |
| Enterprise Plus (Custom) | 251+ | Custom | + On-premise, custom integrations (Post-MVP) |

> "For your team of [X], I'd suggest [Option]. How does that compare to your budget?"

*If too high:*
> "Would it make sense to start with Enterprise Growth and expand as the team grows?"

*If in range:*
> "Great. We'd start with a 30-day pilot with 10 seats — full features, no commitment. You'd see real results on real PRs before deciding."

---

#### Handling Objections

**"Too expensive"**
> "Let's look at the ROI. If Revue catches one critical bug per sprint, what's your typical cost to fix that post-deployment? Most teams say $5K–50K per incident. We're talking about $1,800/year."

**"We already use [Competitor X]"**
> "Great — what do you like about it? Revue's main difference is multi-agent specialisation: instead of one AI trying to do everything, we run a Security agent, a Performance agent, an Architecture agent in parallel. It catches different things. Does [Competitor X] catch architectural issues or suggest 1-click fixes?"

**"We need to see ROI first"**
> "Totally fair. Let's do a 30-day pilot with 10 seats. Full features, no commitment. We can even define success metrics together before we start — what would make this a clear win for your team?"

**"Need approval from CTO/Security/Procurement"**
> "No problem. Want me to join a 20-minute call with them? I can answer technical or security questions directly — often faster than email chains."

**"Data security / code leaving our premises"**
> "This is actually where Revue is fundamentally different. Your code never touches our servers. The orchestrator runs inside your CI runner. The only thing our API sees is a license validation request — no code, no diffs, no review content. Your diff goes directly from your CI to your own OpenAI/Anthropic key."

---

#### Close (5 min)

> "Based on what we've discussed, does this make sense for your team?"

**If YES:**
> "Excellent. Here's what happens next:
> 1. I'll send over a contract and a 30-day trial license (10 seats, full features) today
> 2. We have a 30-min setup call if you'd like help configuring `.revue.yml`
> 3. After 30 days we review results together and expand to your full team
> Does that work?"

**If MAYBE:**
> "What questions do you still have?"
> "What would make this a clear yes?"
> "Is there anything I can prepare to help you make the case internally?"

**If NO:**
> "No problem — what would need to change for this to be a fit?"
> "Would Enterprise Growth be a better starting point?"
> *(Add to nurture sequence; follow up in 3 months)*

---

### Step 3: Follow-Up Sequence

**Day 0 (same day as call):**
- Email with: meeting recap, contract (if agreed), trial license key, demo recording link, case study

**Day 7:**
> "Hi [Name], how's the trial going? Any questions on setup? Happy to jump on a 15-min call."

**Day 21:**
> "You're two-thirds through the trial. What results are you seeing? Ready to expand to the full team?"

**Day 30:**
- Close or extend trial by 2 weeks if they need more time (one extension only)

**Day 45 (if no close):**
> "I want to make sure this is still relevant for you. Is the timing still right, or should we revisit in Q[X]?"

---

## CRM Status Values

| Status | Meaning |
|--------|---------|
| `New` | Chatbot qualified, pre-call email sent |
| `Call Scheduled` | Calendly booked |
| `Trial` | 30-day trial license issued |
| `Negotiating` | Contract in review |
| `Closed-Won` | Paid, onboarded |
| `Closed-Lost` | Declined — log reason |
| `Nurture` | Not ready yet — scheduled follow-up |

---

## Cost to Operate Enterprise Sales

| Activity | Tool | Cost/month |
|----------|------|------------|
| Chatbot pre-qualification | Intercom | $99 |
| Calendly scheduling | Calendly | $12 |
| CRM | HubSpot (free) | $0 |
| Sales time (Enterprise Growth reviews) | Internal | ~$50 |
| Sales time (Enterprise Plus calls) | Internal | ~$200 |
| **Total** | | **~$361/month** |

**Break-even:** One Enterprise Growth deal ($149/month) more than covers the full sales operation cost.

---

*Next: See `prd.md` Section 11.4 for tier structure overview.*
