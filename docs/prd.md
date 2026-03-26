# Revue.io — Product Requirements Document
**Version:** 1.0  
**Date:** March 2026  
**Status:** Draft  
**Owner:** Revue Team

---

## 1. Executive Summary

Revue is a platform-agnostic, AI-powered code review service that runs inside CI/CD pipelines and — optionally — as a pre-commit git hook. It uses a **multi-agent BMAD architecture** where specialised AI agents (Security, Performance, Code Quality, Architecture, and more) review code in parallel, then consolidate findings into a single, prioritised, actionable review posted directly as inline comments on pull/merge requests.

Revue is designed for the **AI-first development era**: teams generating more code faster with AI coding tools need automated review that handles the mechanical layer, so human reviewers can focus on judgement calls.

**Core Principles:**
- Multi-agent by default; single-agent available as a lightweight mode
- Fully open AI backend — bring your own key or use any compatible gateway
- Hybrid deployment — cloud-orchestrated, runs locally in the CI runner
- Platform agnostic — GitHub, GitLab first; Bitbucket, Azure DevOps to follow
- Configurable blocking — teams decide when to gate merges

---

## 2. Problem Statement

### 2.1 The AI Development Paradox

AI coding tools (GitHub Copilot, Cursor, Windsurf) are creating a paradox:
- Code is written 5–10x faster
- AI-generated code has **2x more bugs** than human-written code
- PR volume grows **10x** while reviewer headcount stays flat
- The model that wrote the code has context bias — it misses its own mistakes

### 2.2 Current State of Code Review

The industry has four layers of code quality checks (per the 4-layer model):

| Layer | What | Timing | Tools |
|-------|------|--------|-------|
| **1** | Automate the obvious | Pre-commit hooks | ESLint, SAST, linters |
| **2** | Review locally before push | Before push | AI agent (fresh context) |
| **3** | Review on CI | Every PR/MR | Automated AI review ← **Revue's core** |
| **4** | Human review | PR/MR | Engineer judgement |

Most teams have Layer 1. Almost none have Layer 2. Few have Layer 3 done well. Revue owns Layer 3 (CI review) and extends into Layer 2 (pre-commit, git hook mode).

### 2.3 Problems with Existing Tools

1. **Single-agent limitations** — One LLM call tries to cover security, performance, architecture, quality simultaneously. Context gets diluted. Critical issues get treated the same as style nits.
2. **Platform lock-in** — Most tools are built around one VCS. Teams using GitLab can't use a GitHub-only tool.
3. **AI backend lock-in** — Competitors are tied to OpenAI or their own model. Enterprise teams with Azure OpenAI, custom gateways, or strict data policies are excluded.
4. **Source code leaves infra** — SaaS tools that receive and process code are a security risk for enterprises. Revue runs inside the customer's CI runner; only the review output (not source code) is handled by Revue's backend.
5. **No agentic loop** — Current tools post comments and stop. There's no triage step, no fix proposal, no iteration. They're reviewers, not reviewers + resolvers.

---

## 3. Vision & Goals

### 3.1 Product Vision
> Revue is the AI review layer that every engineering team installs once and relies on forever — the safety net that catches what humans miss, at every stage of the development lifecycle.

### 3.2 MVP Goals (v1.0)
- [ ] Multi-agent CI review that runs on GitHub and GitLab PRs/MRs
- [ ] Configurable specialised agents: Cleo, Zara, Kai, Maya, Leo, Nova
- [ ] Support any AI backend (OpenAI, Anthropic, Azure, OpenRouter, custom gateway)
- [ ] Inline review comments with severity levels
- [ ] **Sage (Resolver)** — scoped, confidence-gated fix suggestions posted as platform-native suggestions (1-click accept)
- [ ] Configurable blocking behaviour
- [ ] Self-service onboarding (GitHub App, GitLab integration)

### 3.3 Phase 2 Goals (v1.5)
- [ ] Single-agent mode as pre-commit git hook (Layer 2)
- [ ] Sage v2 — auto-commit fixes to branch + multi-round loop
- [ ] Review analytics dashboard
- [ ] Bitbucket + Azure DevOps support
- [ ] Custom agent authoring UI

### 3.4 Success Metrics
| Metric | MVP Target | 6-Month Target |
|--------|-----------|----------------|
| Platforms supported | GitHub, GitLab | + Bitbucket, AzDO |
| Active workspaces | 100 free, 20 paid | 500 free, 150 paid |
| Avg review time (CI) | <3 min | <2 min |
| False positive rate | <15% | <10% |
| User satisfaction (NPS) | >40 | >50 |
| Critical issue detection improvement vs single-agent | 35%+ | 40%+ |

---

## 4. Architecture Overview

### 4.1 The 4-Layer Integration Model

Revue is primarily a **Layer 3 tool** (CI-triggered, automatic) with Layer 2 extension (pre-push hook):

```
Developer writes code
       │
       ▼
Layer 1: Git hooks (linting, SAST) ─── NOT Revue
       │
       ▼
Layer 2: Pre-push review ──────────── Revue single-agent hook (Phase 2)
       │
       ▼
Layer 3: CI/CD PR review ──────────── Revue multi-agent (Core product)
       │
       ▼
Layer 4: Human review ─────────────── Human decision (Revue output informs this)
```

### 4.2 Deployment Model (Hybrid)

```
┌─────────────────────────────────────────────────────────┐
│                     Revue Cloud                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ Orchestration│  │  Agent Store │  │  Analytics   │  │
│  │   Service    │  │  & Config    │  │  Dashboard   │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└────────────────────────┬────────────────────────────────┘
                         │  config + webhooks
                         │  (no source code)
┌────────────────────────▼────────────────────────────────┐
│              Customer's CI Runner                       │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │              Revue Agent Runner                 │   │
│  │  (checked out by CI pipeline, runs locally)     │   │
│  │                                                 │   │
│  │  Fetches diff via VCS API                       │   │
│  │  Runs agents locally against AI backend         │   │
│  │  Posts comments back via VCS API                │   │
│  └───────────────────┬─────────────────────────────┘   │
└──────────────────────│──────────────────────────────────┘
                       │  API calls only
          ┌────────────▼─────────────┐
          │      AI Backend          │
          │  (customer's choice)     │
          │  OpenAI / Anthropic /    │
          │  Azure / OpenRouter /    │
          │  Custom Gateway          │
          └──────────────────────────┘
```

**Key point:** Source code never leaves the customer's infrastructure. The CI runner fetches the diff, runs Revue locally, calls the AI backend directly, and posts comments back to the VCS. Revue's cloud only receives configuration and webhook triggers — never source code.

This is consistent with the current design of the existing GitLab implementation and extends it cleanly.

### 4.3 Multi-Agent BMAD Architecture

```
PR/MR Opened
     │
     ▼
┌──────────────┐
│     Cleo     │ ← Analyses diff, selects agents, routes
│ (Orchestrator│
└──────┬───────┘
       │ parallel dispatch
  ┌────┴─────────────────────────────────┐
  │            │            │            │
  ▼            ▼            ▼            ▼
┌──────┐  ┌──────┐  ┌──────────┐  ┌──────────┐
│ Zara │  │ Kai  │  │   Maya   │  │   Leo    │
│      │  │      │  │          │  │          │
│ Sec. │  │Perf. │  │  SOLID/  │  │Patterns/ │
│ OWASP│  │ Algo │  │Maintain. │  │  Design  │
│ CVEs │  │  N+1 │  │Tech debt │  │ Coupling │
└──┬───┘  └──┬───┘  └────┬─────┘  └────┬─────┘
   │         │            │             │
   └────┬────┘            └──────┬──────┘
        │                        │
        ▼                        ▼
   ┌─────────────────────────────────┐
   │              Nova               │
   │  Merge → Deduplicate → Prioritise│
   │  → Format → Inline Comments     │
   └──────────────┬──────────────────┘
                  │
                  ▼
   ┌─────────────────────────────────┐
   │              Sage               │
   │          (Resolver — MVP)       │
   │                                 │
   │  Per finding:                   │
   │  ├─ Self-contained? → Post      │
   │  │    platform suggestion       │
   │  │    (1-click accept)          │
   │  └─ Context-needed? → Label     │
   │       "Needs human" + reason    │
   └──────────────┬──────────────────┘
                  │
                  ▼
         PR/MR Comments + Suggestions
         Blocking decision (configurable)
```

### 4.4 Sage — The Resolver Agent

Sage evaluates each of Nova's findings and decides whether a fix can be safely suggested from the diff alone — or whether it requires human judgement.

**Core principle: Sage knows what it doesn't know.**

```
Nova finding received
        │
        ▼
   Is the fix entirely self-contained
   within the changed lines?
        │
   ┌────┴─────┐
  YES          NO
   │            │
   ▼            ▼
Confidence    Label "Needs human"
check ≥ 90%   Post comment with:
   │          - Why it can't fix it
   ▼          - What context is needed
Post as       - Suggested next step
platform
suggestion:
GitHub  → Suggested Change
GitLab  → Apply Suggestion
(developer accepts with 1 click,
 Sage never commits autonomously)
```

#### Self-Contained Findings (Safe to Suggest)

| Agent | Examples Sage can fix |
|-------|-----------------------|
| **Zara** | SQL injection → parameterised query, hardcoded secret → env var reference, missing input sanitisation on a new field |
| **Kai** | Allocation moved outside a newly introduced loop, obvious O(n²) in a new code block |
| **Maya** | Unused import, missing null check on a new variable, magic number → named constant, SOLID violation introduced in the diff (new class doing too many things, tightly coupled new dependency) |

#### Context-Dependent Findings (Always Human)

- All **Leo (Architecture)** findings — require broader codebase understanding
- Any fix requiring changes to files **outside the diff**
- Findings where Sage confidence is **< 90%**
- Anything touching existing code not introduced in this PR/MR

#### Sage v1 vs v2

| Capability | MVP (v1.0) | Phase 2 (v1.5) |
|-----------|-----------|----------------|
| Classify: fixable vs needs-human | ✅ | ✅ |
| Post fix as platform suggestion (1-click) | ✅ | ✅ |
| Explain why it can't fix something | ✅ | ✅ |
| Confidence score on every suggestion | ✅ | ✅ |
| Auto-commit fix to branch | ❌ | ✅ |
| Multi-round loop (fix → re-review → fix) | ❌ | ✅ |
| Fix files outside the diff | ❌ | ❌ (never) |

---

## 5. Platform Integration

### 5.1 MVP: GitHub + GitLab

**GitHub Integration:**
- GitHub App (OAuth + webhook-based)
- Triggers on `pull_request` events
- Uses GitHub API: fetch PR diff, post PR review comments
- CI integration via GitHub Actions step or `curl` installer script
- Supports GitHub Enterprise (self-hosted)

**GitLab Integration:**
- GitLab OAuth App + webhook
- Triggers on `merge_request` events
- Uses GitLab API: fetch MR diff, post inline comments
- CI integration via `.gitlab-ci.yml` include (existing pattern)
- Supports GitLab Self-Managed

**VCS Abstraction Layer:**

```python
class VCSAdapter(Protocol):
    def get_diff(self, pr_id: str) -> str: ...
    def post_review_comment(self, pr_id: str, file: str, line: int, body: str) -> None: ...
    def post_summary_comment(self, pr_id: str, body: str) -> None: ...
    def set_review_status(self, pr_id: str, status: str) -> None: ...

class GitHubAdapter(VCSAdapter): ...
class GitLabAdapter(VCSAdapter): ...
class BitbucketAdapter(VCSAdapter): ...  # Phase 2
class AzureDevOpsAdapter(VCSAdapter): ...  # Phase 2
```

### 5.2 CI/CD Integration Patterns

**GitHub Actions:**
```yaml
- name: Revue AI Code Review
  uses: revue-io/action@v1
  with:
    revue_token: ${{ secrets.REVUE_TOKEN }}
    ai_api_key: ${{ secrets.OPENAI_API_KEY }}  # or any provider
    ai_provider: openai  # openai | anthropic | azure | openrouter | custom
    ai_model: gpt-4o
    mode: multi-agent  # multi-agent | single-agent
```

**GitLab CI:**
```yaml
include:
  - remote: 'https://raw.githubusercontent.com/revue-io/revue/main/ci/gitlab.yml'

revue-review:
  variables:
    REVUE_TOKEN: $REVUE_TOKEN
    AI_API_KEY: $AI_API_KEY
    AI_PROVIDER: anthropic
    AI_MODEL: claude-sonnet-4-5
```

**Direct (any CI platform):**
```bash
curl -sSL https://install.revue.io | bash
revue review --provider=gitlab --pr=123 --config=.revue.yml
```

---

## 6. AI Backend Support

### 6.1 Supported Providers (MVP)

| Provider | Auth Method | Notes |
|----------|------------|-------|
| OpenAI | API key | GPT-4o, GPT-4 Turbo |
| Anthropic | API key | Claude Sonnet, Claude Opus |
| Azure OpenAI | API key + endpoint | Enterprise Azure deployments |
| OpenRouter | API key | 100+ models via single API |
| Custom Gateway | API key + base URL | Any OpenAI-compatible endpoint |

### 6.2 Configuration

```yaml
# .revue.yml in client repo
ai:
  provider: anthropic          # openai | anthropic | azure | openrouter | custom
  model: claude-sonnet-4-5
  base_url: https://custom-gateway.example.com  # for custom/azure
  api_key_env: AI_API_KEY     # env var name (never hardcoded)
  temperature: 0.2
  max_tokens: 4000
```

### 6.3 AI Provider Abstraction

```python
class AIClient(Protocol):
    async def complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        max_tokens: int
    ) -> str: ...

class OpenAIClient(AIClient): ...
class AnthropicClient(AIClient): ...
class AzureOpenAIClient(AIClient): ...
class OpenRouterClient(AIClient): ...
class CustomGatewayClient(AIClient): ...

def create_ai_client(config: AIConfig) -> AIClient:
    """Factory based on provider config"""
```

---

## 7. Agent System

### 7.1 Core Agents (MVP)

| Agent | ID | Focus | Triggers |
|-------|----|-------|---------|
| **Cleo** *(Orchestrator)* | `orchestrator` | Diff analysis, agent routing, complexity assessment | Always runs |
| **Zara** *(Security)* | `security-analyst` | OWASP Top 10, auth, injection, secrets | auth/crypto/input keywords, sensitive file patterns |
| **Kai** *(Performance)* | `performance-expert` | Algorithm complexity, N+1, memory leaks, blocking I/O | query/loop/cache keywords |
| **Maya** *(Code Quality)* | `code-quality-expert` | SOLID, maintainability, naming, dead code | Always runs (secondary) |
| **Leo** *(Architecture)* | `architecture-reviewer` | Coupling, patterns, design decisions | Large diffs, structural changes |
| **Nova** *(Consolidator)* | `consolidator` | Merge + deduplicate + prioritise findings | Always runs (final step) |

### 7.2 Planned Agents (Phase 2+)

| Agent | Focus |
|-------|-------|
| **TestBot** | Test coverage, test quality, missing edge cases |
| **DocBot** | Documentation completeness, API docs |
| **AccessibilityBot** | WCAG compliance for UI code |
| **MigrationBot** | DB migrations, schema changes, backward compat |
| **ConcurrencyBot** | Async/await, race conditions, thread safety (Swift 6, Kotlin coroutines) |
| **DependencyBot** | CVEs in added dependencies, licence issues |

### 7.3 Agent Definition Format (Declarative YAML/Markdown)

Agents are defined as Markdown files with YAML frontmatter — fully declarative, no code changes required to add or modify agents:

```markdown
# security-analyst.md

```yml
agent:
  name: Zara
  id: security-analyst
  icon: 🔒
  version: "1.0.0"
  whenToUse: "Security-sensitive changes: auth, crypto, user input, APIs"

persona:
  role: Senior Security Engineer — OWASP, penetration testing, secure architecture
  style: Thorough, actionable, OWASP-referenced
  
  core_principles:
    - Defense in depth
    - Zero trust
    - OWASP Top 10 compliance
    - Input validation always

review_focus:
  critical:
    - Injection vulnerabilities (SQL, XSS, Command)
    - Authentication and authorisation flaws
    - Secrets/credentials in code
    - Insecure cryptography
  high:
    - Session management
    - Missing input validation
    - Insecure deserialization
  medium:
    - Sensitive data in logs
    - Verbose error messages

triggers:
  keywords: [auth, password, token, jwt, encrypt, decrypt, user_input, sql, query, secret, api_key]
  file_patterns: ["**/auth/**", "**/security/**", "**/*login*", "**/*password*"]

system_prompt: |
  You are a Senior Security Engineer with expertise in application security...
```
```

Custom agents can be added per project without touching Revue's core code:
```yaml
# .revue.yml
custom_agents:
  - path: .revue/agents/domain-expert.md
```

### 7.4 Review Teams (Agent Groups)

Pre-configured teams for common review scenarios:

| Team | Agents | Use Case |
|------|--------|---------|
| `team-swift-ios` | Concurrency specialist, Maya, Zara | Swift iOS changes |
| `team-security-focus` | Zara, Maya, (Dependency specialist) | Auth/crypto/payment changes |
| `team-performance` | Kai, Leo, Maya | Performance-critical paths |
| `team-full-review` | All agents | Complex features, large diffs |
| `team-quick` | Maya only | Trivial/small changes |

Teams are configured in YAML and selected automatically based on diff analysis or set explicitly:
```yaml
# .revue.yml
review:
  default_team: auto       # auto | team-swift-ios | team-security-focus | etc.
  force_team: null
```

---

## 8. Configuration

### 8.1 Project Configuration (`.revue.yml`)

```yaml
# .revue.yml — placed in the root of the client repository

version: "1.0"

ai:
  provider: anthropic
  model: claude-sonnet-4-5
  api_key_env: AI_API_KEY
  temperature: 0.2
  max_tokens: 4000

review:
  mode: multi-agent           # multi-agent | single-agent
  default_team: auto
  language: auto              # auto | swift | kotlin | python | typescript | etc.
  
  # Blocking behaviour — fully configurable
  blocking:
    enabled: false            # Set true to block PR/MR merge on findings
    block_on: [critical]      # critical | high | medium
    fail_pipeline: true       # Fail CI job (not just review status)
  
  # Noise reduction
  filters:
    suppress_comments_with: []    # Suppress findings in comment-only lines
    ignore_paths:
      - "**/*.generated.*"
      - "**/vendor/**"
      - "**/Pods/**"
    max_findings_per_file: 10

agents:
  enabled:
    - orchestrator
    - security-analyst
    - performance-expert
    - code-quality-expert
    - architecture-reviewer
    - consolidator
  disabled: []
  
  # Per-agent overrides
  overrides:
    security-analyst:
      triggers:
        keywords:
          - custom_auth_pattern
          - company_specific_secret

custom_agents:
  - path: .revue/agents/domain-expert.md

notifications:
  summary_comment: true       # Post a summary comment to the PR/MR
  inline_comments: true       # Post inline comments on specific lines
  slack_webhook: null         # Optional Slack notification
```

### 8.2 Environment Variables (CI)

| Variable | Required | Description |
|----------|----------|-------------|
| `REVUE_TOKEN` | Yes | Revue workspace API token |
| `AI_API_KEY` | Yes | AI provider API key |
| `AI_PROVIDER` | No | Default: `anthropic` |
| `AI_MODEL` | No | Default: `claude-sonnet-4-5` |
| `AI_BASE_URL` | No | For custom/Azure gateways |
| `REVUE_MODE` | No | `multi-agent` or `single-agent` |
| `REVUE_BLOCK_ON_CRITICAL` | No | Override blocking config |

---

## 9. Single-Agent Mode (Git Hook — Phase 2)

### 9.1 Use Case

Layer 2 of the 4-layer model: review locally before pushing. The agent that wrote the code has context bias. Revue in single-agent mode gives a **fresh context review** before the code leaves the developer's machine.

```bash
# Install the pre-push hook
revue hook install

# Or manually in .git/hooks/pre-push
revue review --local --mode=single-agent --diff=HEAD..origin/main
```

### 9.2 Design Considerations
- Single-agent only (speed matters — this is synchronous)
- Configurable exit behaviour (warn vs block)
- Respects `.revueignore` for local overrides
- Works offline with a locally running model (Ollama, LM Studio)
- Shares the same `.revue.yml` config as the CI runner

---

## 10. Review Output Format

### 10.1 Inline Comments

Posted directly on the changed lines in the PR/MR:

```
🔒 [CRITICAL] Potential SQL injection vulnerability

The query is constructed with direct string interpolation from user input.
An attacker could manipulate `userId` to exfiltrate or corrupt data.

Remediation:
Use parameterised queries instead:
```python
# ❌ Vulnerable
query = f"SELECT * FROM users WHERE id = {user_id}"

# ✅ Safe
query = "SELECT * FROM users WHERE id = %s"
cursor.execute(query, (user_id,))
```

References: OWASP A03:2021 – Injection, CWE-89
Agent: Zara 🔒 | Sage suggests: apply fix above ✨
```

### 10.2 Summary Comment

A consolidated summary posted to the PR/MR:

```markdown
## 🔍 Revue AI Code Review

**Files reviewed:** 8 | **Issues found:** 12 | **Review time:** 47s

### 🔴 Critical (1)
- SQL injection in `UserRepository.py:47`

### 🟠 High (2)
- Missing input validation in `api/users.py:23`
- N+1 query pattern in `models/order.py:78`

### 🟡 Medium (4)
...

### ✅ Strengths
- Good use of type hints throughout
- Clear separation of concerns in service layer

---
*Agents: Zara 🔒 · Kai ⚡ · Maya ✨ · Leo 🏗️*
*AI: Claude Sonnet 4.5 · Team: auto → team-security-focus*
*[View full report](https://app.revue.io/reviews/abc123)*
```

---

## 11. Phased Roadmap

### Phase 1: Foundation — MVP (Months 1–3)
**Goal:** Ship a working multi-agent CI reviewer for GitHub + GitLab

| Feature | Priority | Notes |
|---------|----------|-------|
| GitHub App integration | P0 | Webhook + API adapter |
| GitLab integration (migrate existing) | P0 | Port from internal tool |
| Multi-agent BMAD engine | P0 | Port from internal tool, extend |
| AI provider abstraction (OpenAI, Anthropic, Azure, OpenRouter, Custom) | P0 | |
| Core agents: Cleo, Zara, Kai, Maya, Leo, Nova | P0 | |
| **Sage (Resolver) — scoped MVP** | P0 | Suggestion-only, self-contained fixes, confidence-gated |
| `.revue.yml` config schema | P0 | |
| Inline + summary comments | P0 | |
| Configurable blocking | P0 | |
| Self-service workspace onboarding | P0 | Web UI |
| Free tier (BYOK, 100 runs/month) | P1 | |
| Pro tier billing | P1 | Stripe |
| Basic analytics (run history, issue counts) | P1 | |
| Documentation site | P1 | |

### Phase 2: Expansion (Months 4–6)
| Feature | Priority | Notes |
|---------|----------|-------|
| Single-agent git hook (pre-push) | P0 | Layer 2 |
| Bitbucket + Azure DevOps adapters | P0 | |
| **Sage v2** — auto-commit + multi-round loop | P1 | Builds on MVP Sage foundation |
| Concurrency specialist (Swift 6, Kotlin coroutines) | P1 | |
| Custom agent authoring (UI) | P1 | |
| Slack / Teams notifications | P2 | |
| Review analytics dashboard | P2 | Trend data, false positive tracking |

### Phase 3: Scale (Months 7–12)
| Feature | Priority | Notes |
|---------|----------|-------|
| Enterprise SSO/SAML | P0 | |
| Self-hosted Revue backend | P0 | On-prem enterprise |
| Agent marketplace (community agents) | P1 | |
| Cross-model review (write with model A, review with model B) | P1 | Competitive differentiator |
| IDE plugin (VS Code) | P2 | |
| API for programmatic access | P2 | |
| Compliance export (audit logs, SOC2) | P2 | |

---

## 12. Technical Constraints & Non-Goals

### Constraints
- Source code must never be stored by Revue's cloud backend
- Agent runner must be runnable as a standalone binary / Docker image / pip package
- Must support air-gapped environments (self-hosted AI + self-hosted VCS)
- Review must complete within 3 minutes for diffs up to 500 changed lines

### Non-Goals (v1.0)
- Sage does **not** auto-commit fixes — suggestions require explicit developer acceptance
- Sage does **not** fix issues that require context outside the diff — it defers those to humans
- Revue does **not** replace linters or SAST tools (it complements Layer 1)
- Revue does **not** store or index the codebase (it reviews diffs only, not full context)
- Revue is **not** a code search or refactoring tool

---

## 13. Open Questions

1. **Agent Marketplace:** Should community-contributed agents be hosted on revue.io or distributed via GitHub? → Recommend GitHub-hosted with a curated index on revue.io.
2. **Cross-model review:** Priority for Phase 2 or 3? → High value differentiator, suggest Phase 2.
3. **Sage v2 auto-commit:** When Sage pushes a fix autonomously — same commit on same branch, or a new commit? → New commit on same branch, clearly attributed to Revue (e.g. `[revue] fix: parameterise SQL query`).
4. **Confidentiality of findings:** Should review comments be private (visible only to the PR author) or public by default? → Public by default, configurable.
5. **Free tier limits:** 100 runs/month or time-based (30 days)? → 100 runs/month, resets monthly.
6. **Sage confidence threshold:** Is 90% the right cutoff, or should teams be able to configure it? → Recommend 90% default, configurable per project in `.revue.yml`.

---

## Appendix A: Competitive Positioning Summary

| Feature | Revue | CodeRabbit | Greptile | Copilot Review |
|---------|-------|------------|----------|----------------|
| Multi-agent | ✅ | ❌ | ❌ | ❌ |
| Open AI backend | ✅ | ❌ | ❌ | ❌ |
| GitHub | ✅ | ✅ | ✅ | ✅ |
| GitLab | ✅ | ✅ | ✅ | ❌ |
| Bitbucket | Phase 2 | ✅ | ❌ | ❌ |
| Code stays in CI | ✅ | ❌ | ❌ | ❌ |
| Pre-commit hook | Phase 2 | ❌ | ❌ | ❌ |
| Resolver (fix suggestions) | ✅ MVP | ❌ | ❌ | ❌ |
| Resolver (auto-commit loop) | Phase 2 | ❌ | ❌ | ❌ |
| Custom agents | ✅ | ❌ | ❌ | ❌ |
| Configurable blocking | ✅ | Limited | ❌ | ❌ |
| Self-hosted option | Phase 3 | ❌ | ❌ | ❌ |

---

*Market Analysis: see `market-analysis.md`*  
*Implementation reference: see `context/ai-code-review-service/`*
