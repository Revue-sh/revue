---
title: "Product Brief: Revue"
status: "complete"
created: "2026-04-20"
updated: "2026-04-20"
inputs:
  - docs/prd.md
  - docs/market-analysis.md
  - docs/enterprise-sales-playbook.md
  - docs/post-mvp-ideas.md
  - docs/architecture/system-context-injection.md
  - docs/architecture/critical-path-escalation.md
  - README.md
---

# Product Brief: Revue

## Executive Summary

Engineering teams are generating more code than ever — and reviewing less of it carefully.
AI coding tools like GitHub Copilot and Cursor have multiplied developer output, but the
consequences are measurable: industry research published in late 2025 found AI-generated
code produces 1.7× more issues per pull request than human-written code, with 75% more
logic errors — and a Georgetown University study found security vulnerabilities appear in
AI-generated Python code at a rate of 32.8% (CSET, 2024). PR volume is growing far faster
than any team's review capacity.

The first generation of AI code reviewers tried to fill the gap. Most made it worse. A
single AI trying to cover security vulnerabilities, performance bottlenecks, architectural
problems, and code quality concerns simultaneously produces a noisy flood of
undifferentiated findings — which developers learn to dismiss, then stop reading entirely.
The tool becomes wallpaper. The risk stays in the code.

Revue is a multi-agent CI code reviewer built for engineering teams that refuse to choose
between speed and quality. Specialised agents review your code in parallel — each focused
on one domain — then a consolidation layer deduplicates and prioritises findings before
they ever reach your PR. The result is a review that reads like it was written by your
most experienced engineers, delivered in minutes, on every pull request, automatically.
Your code never leaves your CI runner.

---

## The Problem

**The AI development paradox is real — and most teams are already feeling it.**

AI coding tools have created a reviewer bottleneck at exactly the wrong time. Teams are
generating far more code per developer than they were two years ago, but human review
capacity has not moved. The consequence is not just more PRs — it is more PRs with a
higher defect density, reviewed by engineers who are already overloaded.

The tools designed to solve this have a structural flaw. A single-agent AI reviewer,
handed the entire diff, tries to be a security engineer, a performance analyst, an
architect, and a code quality expert all in one call. Context dilutes. Critical issues — a
subtle injection vulnerability buried in an auth refactor, a session invalidation bug that
only manifests under distributed cache semantics — get lost in a wall of style observations
and generic best-practice reminders.

Developers learn which AI reviewers are worth reading. Most are not. The feedback gets
ignored. Senior engineers still spend hours on every significant PR. The AI review is a
box to tick, not a layer of protection.

We know this failure mode intimately. Revue began as an internal GitLab review tool.
The first versions generated feedback that the team found noisy and easy to dismiss — which
made the tool actively counterproductive. Engineers stopped engaging with it. That
experience made one thing clear: an AI reviewer that produces noise does not just fail to
help — it trains teams to ignore automated feedback entirely. That is worse than nothing.

Revue was rebuilt from that lesson.

---

## The Solution

**A team of specialised AI agents, running in parallel, inside your CI, on every PR.**

When a pull request opens, Revue's orchestrator — Cleo — analyses the diff, determines
which domains require scrutiny, and dispatches the right agents:

- **Zara (Security)** — OWASP Top 10, injection vulnerabilities, hardcoded secrets,
  authentication and session management
- **Kai (Performance)** — N+1 queries, algorithm complexity, memory leaks, blocking I/O
- **Maya (Code Quality)** — SOLID violations, dead code, naming, maintainability,
  technical debt introduced in this diff
- **Leo (Architecture)** — coupling, design patterns, structural decisions, module
  boundary violations

Each agent reviews only what it is expert in. They run in parallel. **Nova**, the
consolidator, merges findings, deduplicates overlapping concerns, and prioritises output by
severity — so Critical issues appear first, not buried under informational observations.

**Sage** then evaluates each finding and, where the fix is entirely self-contained within
the changed lines and confidence is high, posts a platform-native suggested change: a
one-click accept on GitHub, GitLab, or Bitbucket. Developers do not just hear what is
wrong — they get a specific fix they can apply in seconds, without leaving the PR.

The entire review runs inside your CI pipeline. Your diff never reaches Revue's servers.

---

## What Makes Revue Different

**1. Multi-agent specialisation — the only tool doing this at CI level.**
Every competitor sends one AI agent through the diff. Revue runs a specialist team. A
security finding from Zara is not crowded out by performance observations from the same
context window. Domain focus produces domain expertise. The quality difference is
structural, not marginal.

**2. Your code stays on your infrastructure.**
Revue's orchestrator runs entirely inside your CI runner. The only thing Revue's cloud
handles is licence validation — a request containing your key and a run ID, nothing else.
No diffs. No source code. No review findings. For security-conscious teams, regulated
industries, and organisations with strict data residency requirements, this is not a
feature request — it is the minimum bar for adoption.

**3. Bring Your Own AI Key.**
Revue never touches your AI spend. Connect your own OpenAI, Anthropic, Azure OpenAI, or
any OpenAI-compatible gateway. Use the model your security policy permits. Switch
providers without migrating. You pay your AI provider directly; Revue charges for
orchestration, prompt engineering, and agent coordination only.

**4. Platform-agnostic from day one.**
GitHub, GitLab, and Bitbucket — first-class support, not an afterthought. Teams already
on GitLab or Bitbucket do not need to migrate or maintain two workflows. The same
`.revue.yml` configuration runs identically across all three platforms.

**5. Extensible by design.**
Agents are defined as declarative YAML/Markdown files. Teams add custom domain agents —
an API design reviewer, a company-specific naming convention checker — without touching
Revue's core code. Custom rules encode institutional knowledge in a version-controlled,
shareable form that survives team changes and onboarding.

**6. Sage — a resolver, not just a commenter.**
Most AI reviewers post a finding and stop. Sage goes further: where the fix is clear and
self-contained, it posts a suggested change the developer can accept with one click.
No copy-pasting. No manual implementation of what the reviewer just described. Fix it,
move on.

**7. Built from the noise problem up.**
Noise reduction is not a setting in Revue — it is the design principle. Severity
calibration, confidence gating, deduplication across agents, and per-team suppression
rules are core to how the pipeline works. The review your team receives is the one they
will read.

---

## Who This Serves

### Engineering Managers and CTOs

Responsible for code quality, incident prevention, and reviewer bandwidth. Their senior
engineers are spending too much time on mechanical review that should not reach a human.
They need a tool that handles the automated layer reliably — not one that creates a second
inbox to manage. Revue gives them a quality gate that works silently in CI, escalates only
what matters, and never requires their engineers to babysit it.

Coming shortly: **Critical Path Protection** — declare sensitive areas (`src/auth/`,
`src/payments/`) in `.revue.yml`, and Revue automatically elevates scrutiny, posts an
escalation notice, and @-mentions the right senior reviewer when those paths are touched.
The loop between automated signal and human accountability closes without a process
document.

### Developers

Who opened a PR at 5 pm and want honest, specific feedback before requesting review from
a colleague. Who do not want generic observations. Who want to know if there is a SQL
injection in line 47, why it matters, and exactly what the fix looks like. Revue posts
that directly on the diff, with a one-click fix if Sage can produce one.

Developers typically discover and adopt Revue. Engineering managers and CTOs approve and
expand it.

---

## Roadmap Thinking

**What Revue does today:**
- Multi-agent CI review on GitHub, GitLab, and Bitbucket
- Six specialised agents: Cleo, Zara, Kai, Maya, Leo, Nova
- Sage: one-click fix suggestions on self-contained, high-confidence findings
- Configurable blocking — gate merges on Critical or High findings
- Full BYOK support: OpenAI, Anthropic, Azure OpenAI, OpenRouter, any custom gateway
- Declarative agent customisation via YAML — extend or override agent behaviour without
  touching Revue's code
- Custom agents: define domain-specific reviewers for your codebase in a Markdown file

**Coming next:**
- **System Context Injection** — Revue reads your architectural contracts
  (`.revue/context.md`) and injects them into agent prompts. Catches assumption errors:
  code that is locally correct but violates a contract your system depends on. A new
  finding class — Architecture Drift — surfaces these at elevated severity, catching the
  exact failure mode AI-generated code produces most frequently.
- **Critical Path Protection** — Declarative sensitive-path configuration. Elevated
  scrutiny, escalation notices, and automated reviewer @-mentions when high-risk areas
  are touched.
- **Pre-push hook** — Single-agent review before code leaves the developer's machine,
  including offline support with locally-running models.

**The two-year vision:**
Revue becomes the standard review layer for every engineering team using AI coding tools —
present at every stage of the development lifecycle, from pre-commit to post-merge, with
an ecosystem of community-contributed domain agents that encode institutional knowledge in
a shareable, version-controlled form. The goal is a codebase that can move at AI speed
without accumulating invisible risk.

---

## Pricing

Revue uses a BYOK model. You pay your AI provider directly — Revue charges for
orchestration only. Total cost of ownership is substantially lower than per-seat SaaS
alternatives that absorb AI costs.

| Tier | Price | Reviews/month | Agents |
|------|-------|---------------|--------|
| **Free** | $0 / £0 per month | 25 | 1 agent |
| **Indie** | $9 / £7 per month | 100 | All 6 agents |
| **Pro** | $29 / £23 per month | Unlimited | All 6 agents |
| **Enterprise Starter** | From $59 / £47 per month | Unlimited | All 6 + custom rules |

Typical total cost of ownership for a team of five on Pro: approximately $59–79 / £47–63
per month (Revue subscription plus AI provider cost at typical usage). Comparable to
CodeRabbit at the same team size, with multi-agent specialisation, code remaining in your
CI runner, and no AI vendor lock-in.

Enterprise tiers include offline licence support for air-gapped environments, a
Nuitka-compiled native binary orchestrator, and dedicated support with SLA.

---

## Get Started

Revue installs as a single step in your existing CI pipeline. No infrastructure changes.
No new services to operate.

**GitHub Actions:**
```yaml
- name: Revue AI Code Review
  uses: revue-io/action@v1
  with:
    revue_token: ${{ secrets.REVUE_TOKEN }}
    ai_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
    ai_provider: anthropic
    ai_model: claude-sonnet-4-6
```

**GitLab CI:**
```bash
curl -sSL https://install.revue.sh | bash
revue review --provider=gitlab --pr=$CI_MERGE_REQUEST_IID
```

**Bitbucket Pipelines:**
```bash
curl -sSL https://install.revue.sh | bash
revue review --provider=bitbucket --pr=$BITBUCKET_PR_ID
```

Create a free workspace at [revue.sh](https://revue.sh) to get your `REVUE_TOKEN`.
Free tier available immediately. All agents on Indie and above.

**→ Start free at [revue.sh](https://revue.sh)**
