# Revue — Product Requirements Document
**Version:** 2.0  
**Date:** May 2026  
**Status:** Draft — strategic pivot revision (cost-driven)  
**Owner:** Revue Team

> **v2.0 changes** (vs v1.4, March 2026): Default AI backend swapped from Anthropic Sonnet to DeepSeek-V4-Pro on OpenRouter; primary customer surface shifted from CI/CD service to `/revue-local` — a Claude Code skill invoked inside the customer's AI-coding workflow before the AI commits; CI integrations (GitHub Actions, GitLab CI, Bitbucket Pipelines) moved to deprecated-but-maintained status; architecture principle made explicit: AI-model-agnostic. Driver: Anthropic API pricing made the original CI-centric model uneconomic for both Revue and customers. Positioning pillar added: customer AI-bill reduction.
>
> **v2.0 addendum (2026-05-18):** Validator-driven fix pass applied. Resolved 9 Critical and 14 Warning findings from `validation-report-prd-2026-05-18.md`: Executive Summary re-anchored on customer cost outcome; agent inventory reconciled to 8 across all sections (Cleo, Zara, Kai, Maya, Leo, Nova, Sage, Vex) with Vex and Sage now specified in §7.1; §5.2 GitHub Actions example moved to DeepSeek/OpenRouter default; §4.2 split into `/revue-local` (primary) and CI (deprecated-but-maintained) surfaces; §10 gains a `/revue-local` output sample; §11.1 custom-agents row disambiguated (YAML vs UI); §3.5 KPIs gain a measurement-method column and the ≥40% AI-spend target is now method-anchored; cost-savings positioning surfaces consistently in §1, §11, and Appendix A.

---

## 1. Executive Summary

**Revue cuts customer AI API expenditure by ~79–88% at typical review volumes** (§11.2 TCO table) and eliminates Revue-side AI spend entirely on the `/revue-local` path. Revue is a code reviewer that catches issues inside the customer's AI-coding session — before the AI commits — so the team stops re-paying for the same defects in CI. AI API expenditure is a board-level line item; Revue is positioned as a partner in cost discipline at exactly the moment that bill is climbing.

Revue ships in two configurations:

1. **`/revue-local` — primary surface (v2.0).** A Claude Code skill the customer's AI-coding agent invokes inside its own workflow, before it commits code to the repository. Same pattern teams already use for code generation: AI writes → Revue reviews → AI revises → AI commits. Customer pays only their existing Claude Code subscription — no Revue-side AI API spend.
2. **CI/CD service — deprecated-but-maintained (originally shipped v1.x, still supported on v2.0+).** Multi-agent review that runs inside the customer's CI pipeline on every PR/MR, posted as inline comments. Supported with the cheapest available model (DeepSeek-V4-Pro on OpenRouter, ~10× cheaper per typical review than Anthropic Sonnet 4.5 — REVUE-265) but no longer the primary product investment focus.

**Core Principles:**
- **We care about your AI bill.** Catching issues in `/revue-local` before commit means fewer CI review cycles, fewer AI API calls, lower customer spend. ~79–88% TCO reduction vs the v1.x Anthropic Sonnet baseline (§11.2). Cost-savings is a first-class product promise, not a feature note.
- **Multi-agent by default.** Specialised reviewers outperform a single generalist on every signal measured to date. Eight agents ship today (§7).
- **AI-model-agnostic.** DeepSeek-V4-Pro on OpenRouter is the default. Anthropic, OpenAI, Azure, OpenRouter, and self-hosted gateways are all supported via the registry/dispatcher abstraction. No Anthropic-specific code in business logic.
- **Bring your own key.** Customers pay their AI provider directly; Revue charges only for orchestration.
- **Platform agnostic.** GitHub, GitLab, and Bitbucket inline-comment posting supported. Azure DevOps planned for Phase 3.
- **Configurable blocking.** Teams decide when to gate commits or merges.

---

## 2. Problem Statement

### 2.1 The AI Development Paradox

AI coding tools (GitHub Copilot, Cursor, Windsurf) are creating a paradox:
- **AI-assisted code is more often insecure — and developers trust it more.** In a controlled study, participants with an AI assistant wrote significantly less secure code yet were *more* confident their code was secure ([Perry et al., "Do Users Write More Insecure Code with AI Assistants?", Stanford, ACM CCS 2023](https://arxiv.org/abs/2211.03622)).
- **Roughly half of AI-generated code contains a vulnerability.** Across five leading models, almost half of generated snippets contained bugs that are often impactful and could be exploited ([Cybersecurity Risks of AI-Generated Code, Georgetown CSET, Nov 2024](https://cset.georgetown.edu/publication/cybersecurity-risks-of-ai-generated-code/)).
- **Throughput is rising faster than review capacity.** AI adoption correlates with larger batch sizes and lower software-delivery stability and throughput ([DORA 2024](https://dora.dev/research/2024/dora-report/)), while overall developer and pull-request activity keeps climbing year over year ([GitHub Octoverse](https://github.blog/news-insights/octoverse/)) — yet reviewer headcount stays flat.
- The model that wrote the code has context bias — it misses its own mistakes.

### 2.2 Current State of Code Review

The industry has four layers of code quality checks (per the 4-layer model):

| Layer | What | Timing | Tools |
|-------|------|--------|-------|
| **1** | Automate the obvious | Pre-commit git hooks | ESLint, SAST, linters |
| **2** | **AI-agent self-review before commit** | Inside the AI-coding agent's loop, before it commits | **Revue via `/revue-local` ← primary surface (v2.0)** |
| **3** | Multi-agent review on CI | Every PR/MR | Revue CI service ← deprecated-but-maintained track |
| **4** | Human review | PR/MR | Engineer judgement |

Most teams have Layer 1. Almost none have Layer 2 in a structured form — AI agents typically commit without an independent review pass. Few have Layer 3 done well at multi-agent granularity. **Revue's v2.0 strategy concentrates on Layer 2 via `/revue-local`**: catching issues at the moment of highest leverage (in-session, pre-commit) eliminates downstream CI review cycles — saving the customer AI API spend at the layer that bills it. The CI track (Layer 3) remains supported but is no longer the primary investment focus.

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

**Why this matters now.** AI coding tools (Copilot, Cursor, Windsurf) have created a structural paradox (§2.1): AI-assisted developers ship code faster but write code that is *less* secure — while being *more* confident it is fine ([Stanford, ACM CCS 2023](https://arxiv.org/abs/2211.03622)) — and roughly half of AI-generated snippets contain a vulnerability ([Georgetown CSET, Nov 2024](https://cset.georgetown.edu/publication/cybersecurity-risks-of-ai-generated-code/)). Throughput is rising faster than review capacity ([DORA 2024](https://dora.dev/research/2024/dora-report/); [GitHub Octoverse 2025](https://github.blog/news-insights/octoverse/)). The model that wrote the code has context bias — it misses its own mistakes. Human reviewers are the only backstop, and they are overwhelmed.

Revue's job is to remove that bottleneck without forcing teams to trade quality for throughput. Every comment Revue posts must give a developer the same value as a senior teammate's inline review: anchored where the issue is, attributed to who is saying it, and — where possible — one-click applicable. Anything less is noise, and noise makes the bottleneck worse.

### 3.2 MVP Goals — shipped

**Shipped in v1.x (CI-era):**
- [x] Multi-agent CI review on GitHub, GitLab, and Bitbucket PRs/MRs
- [x] AI backend abstraction: OpenAI, Anthropic, Azure, OpenRouter, custom gateway — all via the per-model registry + dispatcher
- [x] Inline review comments with severity levels
- [x] Sage (Resolver) — scoped, confidence-gated fix suggestions posted as platform-native 1-click suggestions
- [x] Configurable blocking behaviour
- [x] Self-service onboarding (GitHub App, GitLab integration)

**Shipped in v2.0 (cost-driven pivot):**
- [x] Eight specialised agents — Cleo, Zara, Kai, Maya, Leo, Nova, Sage, Vex (Vex added in REVUE-241 / REVUE-261 as the in-loop semantic verifier)
- [x] **DeepSeek-V4-Pro on OpenRouter as default model** (REVUE-267) — ~10× cheaper per review than Anthropic Sonnet 4.5 (REVUE-265; see `docs/research/deepseek-v4-pro-evaluation.md`)
- [x] **`/revue-local` Mode 2 — native Claude Code Task-based review pipeline** (REVUE-259/260/261) — runs in the customer's existing Claude Code session at zero Revue-side AI spend

### 3.3 Phase 2 Goals (v2.x) — `/revue-local` productisation
- [ ] **Distribution: `/revue-local` as an installable customer-facing Claude Code skill** (skill registry, install flow, paywall mechanics — covered in `docs/planning/revue-local-distribution-brief.md`, TBC — brief not yet authored)
- [ ] **Pre-commit AI review workflow** — published patterns for invoking `/revue-local` from common AI-coding agents (Claude Code, Cursor, Windsurf) before they commit
- [ ] **Cost-saving dashboard** — show customers their AI-bill reduction vs. the CI-only path (delta = number of reviews caught locally × per-review API cost)
- [ ] Sage v2 — auto-commit fixes to branch + multi-round loop
- [ ] Custom agent authoring UI
- [ ] Review analytics dashboard

### 3.4 Phase 3 Goals (v3.x) — Scale
- [ ] Azure DevOps adapter (CI track maintenance)
- [ ] Enterprise SSO/SAML, self-hosted Revue backend
- [ ] Agent marketplace (community agents)

### 3.5 Success Metrics

| Metric | MVP Target | 6-Month Target | Measurement Method |
|--------|-----------|----------------|---------------------|
| Platforms supported (CI inline-comment posting) | GitHub, GitLab, Bitbucket | + AzDO | Shipped adapter count, verified in CI integration tests |
| `/revue-local` skill installs (Phase 2 KPI) | n/a | 500 | Skill registry install counter, distinct workspace IDs |
| Active workspaces (CI track) | 100 free, 20 paid | 500 free, 150 paid | Workspaces with ≥1 review in the trailing 30 days, queried from usage-tracker |
| **Customer AI-spend reduction** (`/revue-local` adopters vs same-customer CI-only 90-day baseline) | Instrumented; baseline cohort established | **≥40% reduction** | Per-customer A/B: baseline = customer's prior 90-day OpenRouter/Anthropic CI token spend pre-`/revue-local`; post = same customer's 90-day spend post-adoption; attribution = `findings_caught_locally × avg_per_review_CI_token_cost`. Cohort: customers with ≥30 reviews in both windows. Reported per-customer (median) and aggregate. Methodology owner: PM; gate resolution before Phase 2 dashboard ticket enters sprint planning. |
| Avg review time (CI) | <3 min | <2 min | p95 wall-clock across all review runs in the trailing 30 days, instrumented in orchestrator |
| False positive rate | <15% | <10% | Findings marked `false_positive` by developer accept/reject UI ÷ total findings, sampled per 1,000 findings, adjudicated by PR-author feedback |
| User satisfaction (NPS) | >40 | >50 | Quarterly in-product NPS survey, n ≥ 50 per quarter |
| Critical issue detection improvement vs single-agent | 35%+ | 40%+ | Internal labelled corpus; multi-agent run findings ∩ ground-truth Critical ÷ single-agent baseline on same corpus |

---

## 4. Architecture Overview

### 4.1 The 4-Layer Integration Model

Revue's v2.0 strategy is a **Layer 2 product** (`/revue-local` invoked inside the customer's AI agent before commit) with Layer 3 as a deprecated-but-maintained track (CI multi-agent review):

```
Developer (or AI coding agent) writes code
       │
       ▼
Layer 1: Git hooks (linting, SAST) ─── NOT Revue
       │
       ▼
Layer 2: Pre-commit AI review ─────── Revue /revue-local (primary surface — v2.0)
   (multi-agent, in customer's          ← runs in customer's Claude Code session
    Claude Code session, before          ← customer pays only Claude Code subscription
    the AI commits)                      ← catches issues before CI bills another round
       │
       ▼
Layer 3: CI/CD PR review ──────────── Revue multi-agent CI service
   (DeepSeek-default, BYOK)             ← deprecated-but-maintained track
       │
       ▼
Layer 4: Human review ─────────────── Human decision (Revue output informs this)
```

### 4.2 Deployment Model

Revue ships two execution surfaces, each with its own deployment topology.

#### 4.2a `/revue-local` — Primary Surface

`/revue-local` runs as a Claude Code skill inside the customer's existing AI-coding session. No Revue-managed runtime, no Revue-side AI compute, no Revue-side API key required for inference. The customer's Claude Code subscription is the only billed channel.

```
Customer's AI-coding session (Claude Code)
         │
         ▼
  /revue-local skill invoked on staged diff
         │
         ▼
  Multi-agent pipeline (Cleo → Zara/Kai/Maya/Leo → Nova → Sage → Vex)
  runs inside the same Claude Code session
         │
         ▼
  Findings streamed back to the calling AI
  (markdown digest + BLOCK_COMMIT / WARN_PROCEED / OK signal)
```

**What leaves the customer's machine:** nothing during the review itself. Distribution-mechanic concerns (skill install, licence validation, paywall enforcement for `/revue-local`) are covered in the sibling product brief `docs/planning/revue-local-distribution-brief.md` (TBC — brief not yet authored).

#### 4.2b CI Orchestrator — Deprecated-but-Maintained

The CI orchestrator runs **entirely inside the customer's CI environment**. Source code and diffs never leave the customer's infrastructure. This surface still ships and is still supported on v2.0+; no new feature investment.

```
User's Repo → CI Trigger → Revue Orchestrator (runs on CI runner)
                                    │
                          Validates license key
                                    │
                             ┌──────▼──────┐
                             │  Revue API  │  ← license validation + usage
                             │  (cloud)    │    tracking only (no code)
                             └─────────────┘
                                    │
                          User's AI Provider API Key
                                    │
                    ┌───────────────▼───────────────┐
                    │  OpenRouter (default) /        │
                    │  Anthropic / OpenAI / Azure /  │
                    │  Custom Gateway                │
                    └───────────────┬───────────────┘
                                    │
                         Review Results → PR/MR Comment
```

**What Revue's cloud sees (license + usage API only):**
- License key validation: `{ key, repo_id, ci_run_id }` — **no source code**
- Usage tracking: `{ key, agents_used, duration_ms }` — **no source code**

**What Revue's cloud never sees:**
- PR/MR diffs
- Source code
- Review findings

**IP Protection (4.2b only):**
- **All tiers:** CI orchestrator compiled to a binary distribution that resists decompilation (currently Nuitka). Distributed as platform-specific `.whl` (Free/Indie/Pro) or Docker image (Enterprise).
- **Runtime enforcement:** License key validated on orchestrator startup via `POST /api/license/validate` — returns tier, agents allowed, reviews remaining. Hard stop on invalid key. 72h offline grace period for Enterprise airgapped environments.

**Webhook security (4.2b only):** All incoming webhooks are verified using platform-native secret tokens (GitHub webhook secrets, GitLab secret tokens, Bitbucket signed payloads). Requests with invalid signatures are rejected before processing.

### 4.3 Multi-Agent BMAD Architecture

```
PR/MR Opened  (or /revue-local invocation)
     │
     ▼
┌──────────────┐
│     Cleo     │ ← Analyses diff, selects agents, routes
│ (Orchestrator│
└──────┬───────┘
       │ parallel dispatch
  ┌────┴───────────────────┬───────────────────────┐
  │ Security pillar        │ Quality pillar        │
  ▼                        ▼                       ▼
┌──────┐               ┌──────────┐         ┌──────────┐
│ Zara │               │   Maya   │         │   Leo    │
│      │               │          │         │          │
│ Sec. │   ┌──────┐    │  SOLID/  │         │Patterns/ │
│ OWASP│   │ Kai  │    │Maintain. │         │  Design  │
│ CVEs │   │ Perf │    │Tech debt │         │ Coupling │
└──┬───┘   └──┬───┘    └────┬─────┘         └────┬─────┘
   │          │              │                    │
   └────┬─────┘              └──────────┬─────────┘
        │                               │
        └───────────────┬───────────────┘
                        ▼
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
   ┌─────────────────────────────────┐
   │              Vex                │
   │     (Semantic Verifier — in-loop)│
   │                                 │
   │  Per finding (Nova / Sage):     │
   │  ├─ apply → keep finding        │
   │  ├─ drop_cr_keep_prose → keep   │
   │  │    explanation, drop fix     │
   │  └─ reject_finding → suppress   │
   │       (hallucination / wrong    │
   │        attribution)             │
   └──────────────┬──────────────────┘
                  │
                  ▼
         PR/MR Comments + Suggestions
         (or /revue-local digest)
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

| Capability | MVP (v1.0) | Phase 2 (v2.x) |
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

### 5.1 Supported Platforms (GitHub, GitLab, Bitbucket)

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

**Bitbucket Integration:**
- Bitbucket OAuth App + webhook
- Triggers on `pullrequest:created` and `pullrequest:updated` events
- Uses Bitbucket API: fetch PR diff, post inline comments via `pullrequests/{id}/comments`
- CI integration via Bitbucket Pipelines step
- Supports Bitbucket Cloud; Bitbucket Data Center on the Enterprise tier

**VCS Abstraction Layer:**

Note: GitHub and GitLab use different comment position models. GitHub requires a diff-position offset; GitLab requires a `line_code` hash. The abstraction uses a rich `DiffPosition` type that each adapter translates natively.

```python
@dataclass
class DiffPosition:
    file_path: str
    line_number: int       # Logical line number (adapter translates)
    side: str              # "new" | "old"
    raw_diff_hunk: str     # For adapter-specific position calculation

class VCSAdapter(Protocol):
    def get_diff(self, pr_id: str) -> str: ...
    def post_review_comment(self, pr_id: str, position: DiffPosition, body: str) -> None: ...
    def post_summary_comment(self, pr_id: str, body: str) -> None: ...
    def set_review_status(self, pr_id: str, status: str) -> None: ...
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool: ...

class GitHubAdapter(VCSAdapter): ...   # Translates DiffPosition → diff offset
class GitLabAdapter(VCSAdapter): ...   # Translates DiffPosition → line_code hash
class BitbucketAdapter(VCSAdapter): ...  # Shipped — translates DiffPosition → inline comment payload
class AzureDevOpsAdapter(VCSAdapter): ...  # Phase 3
```

### 5.2 CI/CD Integration Patterns

**GitHub Actions:**
```yaml
- name: Revue AI Code Review
  uses: revue-io/action@v1
  with:
    revue_token: ${{ secrets.REVUE_TOKEN }}
    ai_api_key: ${{ secrets.OPENROUTER_API_KEY }}
    ai_provider: openrouter  # openrouter (default) | anthropic | openai | azure | custom
    ai_model: deepseek/deepseek-v4-pro  # cost-optimised default
    mode: multi-agent  # multi-agent | single-agent
    # Opt back into Anthropic Sonnet:
    # ai_provider: anthropic
    # ai_model: claude-sonnet-4-5-20250929
    # ai_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
```

**GitLab CI:**
```yaml
include:
  - remote: 'https://raw.githubusercontent.com/revue-io/revue/main/ci/gitlab.yml'

revue-review:
  variables:
    REVUE_TOKEN: $REVUE_TOKEN
    AI_API_KEY: $OPENROUTER_API_KEY
    AI_PROVIDER: openrouter
    AI_MODEL: deepseek/deepseek-v4-pro
```

**Direct (any CI platform):**
```bash
curl -sSL https://install.revue.sh | bash
revue review --provider=gitlab --pr=123 --config=.revue.yml
```

---

## 6. AI Backend Support

### 6.1 Default Model

**Default: `deepseek/deepseek-v4-pro` on OpenRouter.** Selected on cost grounds (~10× cheaper per typical review than Anthropic Sonnet 4.5) with no observed quality regression on Revue's supported-tier protocol (REVUE-265 smoke evaluation: 3/3 schema-valid trials with `tool_choice_first_turn: required`, multi-call context fetching, three-state envelope compliance).

Customers can override the default per project in `.revue.yml` or via the `REVUE_PROVIDER` / `REVUE_MODEL` environment variables. Anthropic Sonnet 4.5 remains supported as an opt-in for teams that explicitly require Anthropic infrastructure.

### 6.2 Supported Providers

| Provider | Default model | Auth Method | Notes |
|----------|--------------|-------------|-------|
| **OpenRouter (default)** | `deepseek/deepseek-v4-pro` | API key | 100+ models via single API; default cost-optimised route |
| Anthropic | `claude-sonnet-4-5-20250929` | API key | Sonnet, Haiku — opt-in via explicit config |
| OpenAI | GPT-4o | API key | Supported as a customer-extended tier |
| Azure OpenAI | Customer's deployment | API key + endpoint | Enterprise Azure deployments |
| Custom Gateway | Customer's choice | API key + base URL | Any OpenAI-compatible endpoint |

Each model is registered in the built-in per-model registry (`models_registry.yml`) with its own knob set: `provider`, `schema_mode`, `schema_strict`, `tool_choice_first_turn`, `max_tokens_default`, `tier`. Customers can extend or override the registry via the `models:` section of `.revue.yml`. Run `revue list-models` to see the live merged registry.

### 6.3 Configuration

```yaml
# .revue.yml — defaults
ai:
  provider: openrouter             # openai | anthropic | azure | openrouter | custom
  model: deepseek/deepseek-v4-pro  # default — see docs/configuration/per-model-knobs.md
  api_key_env: OPENROUTER_API_KEY  # env var name (never hardcoded)
  temperature: 0.2
  max_tokens: 2048

# Opt back into Anthropic Sonnet:
# ai:
#   provider: anthropic
#   model: claude-sonnet-4-5-20250929
#   api_key_env: ANTHROPIC_API_KEY
```

### 6.4 Architecture Principle — AI-Model-Agnostic

**Business logic must remain provider-neutral.** All multi-provider behaviour lives behind:
- The **per-model registry** (`src/revue/core/models_registry.yml`) — declarative knob set per model.
- The **dispatcher** (`src/revue/core/models_registry.py`) — validates `ai_config.model` and `synthesis_model` against the registry at config-load time; rejects unknown or mis-tiered models.
- The **AIClient protocol** — one abstract interface; concrete clients per provider.

```python
class AIClient(Protocol):
    async def complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> str: ...

class OpenRouterClient(AIClient): ...   # Default route
class AnthropicClient(AIClient): ...
class OpenAIClient(AIClient): ...
class AzureOpenAIClient(AIClient): ...
class CustomGatewayClient(AIClient): ...

def create_ai_client(config: AIConfig) -> AIClient:
    """Factory based on provider config; dispatcher gate has already validated the model."""
```

`create_ai_client(default_config())` returns an `OpenRouterClient` configured for `deepseek/deepseek-v4-pro`.

Provider-specific quirks (Anthropic's `output_config`, OpenRouter's `tool_choice_first_turn: required` for Qwen/DeepSeek, etc.) live behind the registry knob — not in agent prompts, not in pipeline code, not in reviewer logic. Adding a new model = adding a registry row; no code change in business logic.

---

## 7. Agent System

### 7.1 Core Agents (8 shipped)

| Agent | ID | Focus | Triggers |
|-------|----|-------|---------|
| **Cleo** *(Orchestrator)* | `orchestrator` | Diff analysis, agent routing, complexity assessment | Always runs |
| **Zara** *(Security)* | `security-analyst` | OWASP Top 10, auth, injection, secrets | auth/crypto/input keywords, sensitive file patterns |
| **Kai** *(Performance)* | `performance-expert` | Algorithm complexity, N+1, memory leaks, blocking I/O | query/loop/cache keywords |
| **Maya** *(Code Quality)* | `code-quality-expert` | SOLID, maintainability, naming, dead code | Always runs (secondary) |
| **Leo** *(Architecture)* | `architecture-reviewer` | Coupling, patterns, design decisions | Large diffs, structural changes |
| **Nova** *(Consolidator)* | `consolidator` | Merge + deduplicate + prioritise findings | Always runs (final step) |
| **Sage** *(Resolver)* | `resolver` | Per-finding classification: fixable as 1-click platform suggestion vs needs-human; confidence-gated (≥90%); never auto-commits in v1 | Runs after Nova on every review |
| **Vex** *(Semantic Verifier)* | `semantic-verifier` | Verifies each finding against the actual code; classifies as `apply` / `drop_cr_keep_prose` / `reject_finding` to suppress hallucinated or misattributed findings before they reach the developer | Runs in-loop after Nova/Sage (REVUE-241, made in-loop in REVUE-261) |

Agent system prompts must remain **provider-neutral** — no model-specific framing, no Anthropic/OpenAI-shaped instructions, no language-specific code examples. The coding language is injected from file extensions at runtime; provider-specific quirks live behind the registry/dispatcher (§6.4), never in the agent prompt.

### 7.2 Planned Agents (Phase 2+)

| Agent | Focus |
|-------|-------|
| **Finn** *(Test coverage)* | Test coverage gaps, test quality, missing edge cases |
| **Dara** *(Documentation)* | Documentation completeness, API docs, inline comments |
| **Arlo** *(Accessibility)* | WCAG compliance for UI code, a11y patterns |
| **Remy** *(Migrations)* | DB migrations, schema changes, backward compatibility |
| **Sora** *(Concurrency)* | Async/await, race conditions, thread safety (Swift 6, Kotlin coroutines) |
| **Rex** *(Dependencies)* | CVEs in added dependencies, licence issues |

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

Agent definitions must remain **provider-neutral**: no `model:` or `provider:` constraints in the YAML, no Anthropic/OpenAI-shaped framing inside `system_prompt`, no language-specific code examples. The provider, model, and language are injected at runtime by the dispatcher (§6.4) and the file-extension router. A `tier:` field is reserved for downstream registry binding (e.g. `tier: supported` to gate the agent against models in that registry tier).

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
  provider: openrouter             # default; opt back into Anthropic with provider: anthropic
  model: deepseek/deepseek-v4-pro  # cost-optimised default (see docs/configuration/per-model-knobs.md)
  api_key_env: OPENROUTER_API_KEY
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
| `REVUE_LICENSE_KEY` | Yes | Revue licence key (from revue.sh/account) |
| `OPENROUTER_API_KEY` | Yes (default path) | OpenRouter API key — required for the DeepSeek default; swap for `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` if opting into those providers |
| `REVUE_PROVIDER` | No | Default: `openrouter`. Override values: `anthropic`, `openai`, `azure`, `custom` |
| `REVUE_MODEL` | No | Default: `deepseek/deepseek-v4-pro`. Override per registry entry |
| `REVUE_BASE_URL` | No | For custom/Azure gateways |
| `REVUE_BLOCK_ON_CRITICAL` | No | Override blocking config |

---

## 9. `/revue-local` — Pre-Commit AI Review

### 9.1 What it is

`/revue-local` is a **Claude Code skill** the customer's AI-coding agent invokes inside its own workflow, **before the AI commits code to the repository**. It runs Revue's full multi-agent pipeline against the work-in-progress diff, surfacing findings the AI can act on (revise, ask for clarification, or proceed) before the commit lands.

This is Revue's **primary product surface as of v2.0**. The CI/CD service (§5, §10) remains supported but is no longer the primary investment focus.

### 9.2 Why this shape

**Cost-savings for the customer.** Every issue caught in `/revue-local` is one fewer issue that triggers a CI review cycle and bills another round of AI API calls. At scale, this is the single largest lever a Revue customer has to reduce their AI spend.

**Same AI session, no Revue-side API cost.** `/revue-local` runs as a Claude Code skill inside the customer's existing Claude Code session. The customer pays only their existing Claude Code subscription — Revue does not bill per-API-call.

**Aligned with how AI-coding workflows actually work.** Modern AI-coding agents (Claude Code, Cursor, Windsurf) operate in a write-review-revise loop. `/revue-local` is the *review* step in that loop. Without it, the AI's own output is the only quality gate before commit; with it, a multi-agent reviewer with specialised focus areas (Security, Performance, etc.) intercepts before commit.

**Fresh context.** The agent that wrote the code has context bias — it tends to miss its own mistakes. `/revue-local` gives a fresh-context multi-agent review at the moment of highest leverage (pre-commit, in the same session).

### 9.3 How customers invoke it

The customer instructs their AI-coding agent to call `/revue-local` before each commit. Example pattern in a project's `CLAUDE.md` or equivalent:

```markdown
## Commit workflow

Before committing, run `/revue-local` on the staged diff. If any High-severity
finding appears, resolve it (or get explicit user override) before proceeding
to the commit. Repeat the review after each fix.
```

The skill itself runs the same multi-agent pipeline (Cleo, Zara, Kai, Maya, Leo, Nova, Sage, Vex) the CI path runs — same config (`.revue.yml`), same agents, same output format. The only difference is where it runs (customer's Claude Code session) and who pays for compute (the customer's Claude Code subscription, not Revue).

### 9.4 Design constraints

- Runs inside the customer's Claude Code session — no Revue-managed API key required for inference.
- Shares the same `.revue.yml` config as the CI path. One config, two execution surfaces.
- Configurable exit behaviour: block the commit on High-severity findings, warn-only, or summary-only.
- Works on uncommitted, staged, or specified-range diffs.
- Distribution mechanics (skill registry, install flow, paywall, licence enforcement) are covered in `docs/planning/revue-local-distribution-brief.md` (TBC — brief not yet authored). This PRD only defines the customer-facing behaviour.

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
### 🧠 Sage's Suggestions (2)
- `UserRepository.py:47` — Apply parameterised query fix *(1-click)*
- `api/users.py:23` — Apply input sanitisation fix *(1-click)*

---
*Agents: Zara 🔒 · Kai ⚡ · Maya ✨ · Leo 🏗️ · Sage 🧠*
*AI: DeepSeek-V4-Pro (OpenRouter) · Team: auto → team-security-focus*
*[View full report](https://app.revue.sh/reviews/abc123)*
```

### 10.3 `/revue-local` Output Format

The `/revue-local` skill streams findings back into the customer's Claude Code session as a markdown digest, structured identically to §10.2 so the calling AI can parse it without surface-specific handling. The CI-only fields (`[View full report]` link, run ID, posted-comment count) are omitted; everything else — severities, agent attributions, model identifier, Sage suggestions — matches §10.2 byte-equivalent.

A trailing block signals the recommended next action to the calling AI:

```markdown
## 🔍 Revue /revue-local — pre-commit review

**Files reviewed:** 4 | **Issues found:** 3 | **Review time:** 22s

### 🔴 Critical (1)
- SQL injection in `src/api/users.py:47`

### 🟠 High (1)
- Missing input validation in `src/api/users.py:23`

### 🟡 Medium (1)
- Magic number in `src/models/order.py:78`

### 🧠 Sage's Suggestions (2)
- `src/api/users.py:47` — Apply parameterised query fix
- `src/api/users.py:23` — Apply input sanitisation fix

---
**Recommended action:** BLOCK_COMMIT — 1 Critical finding present.
Resolve before commit, or pass `--override` with explicit user approval.

*Agents: Cleo · Zara 🔒 · Kai ⚡ · Maya ✨ · Leo · Nova · Sage 🧠 · Vex*
*AI: customer's Claude Code session (no Revue-side API spend)*
```

Exit semantics: `BLOCK_COMMIT` (any Critical or configured-block severity), `WARN_PROCEED` (findings below block threshold), or `OK` (no findings). The calling AI uses this signal to decide whether to commit, revise, or escalate to the user.

---

## 11. Pricing & Tiers

> **Cost-care positioning — load-bearing.** Revue's pricing story is "we save you money on your AI bill." Two mechanisms:
>
> 1. **Default model is cost-optimised.** DeepSeek-V4-Pro on OpenRouter runs at ~10× lower per-token cost than Anthropic Sonnet 4.5 (see TCO table below). This is the default — customers benefit without configuration.
> 2. **`/revue-local` eliminates Revue-side AI spend entirely.** Running reviews inside the customer's existing Claude Code session means the customer's AI bill is the only line item — and `/revue-local` actively *reduces* it by catching issues before they trigger CI review cycles. Every issue caught locally is one fewer round of CI-side AI calls billed.
>
> This positioning must be visible in the README, the website, the pricing page, the launch post, and every customer-facing surface. It is not a feature note; it is the headline.

### 11.1 Tier Comparison

> **BYOK model:** Customers pay their AI provider directly (OpenRouter, Anthropic, OpenAI, etc.). Revue charges only for orchestration, prompt engineering, and agent coordination — not AI compute. This is structurally different from competitors who absorb AI costs (and price accordingly).

| Feature | Free | Indie | Pro | Enterprise |
|---------|------|-------|-----|------------|
| **Reviews/month** | 25 | 100 | Unlimited | Unlimited |
| **Agents** | Cleo + 1 reviewer + Nova | All 8 | All 8 | All 8 |
| **Code location** | User's CI | User's CI | User's CI | User's CI or self-hosted |
| **AI API keys** | User-provided | User-provided | User-provided | User-provided |
| **Orchestrator** | Nuitka .whl | Nuitka .whl | Nuitka .whl | Nuitka Docker image |
| **License validation** | Online | Online | Online | Online or offline |
| **Custom rules** | ✅ | ✅ | ✅ | ✅ |
| **Custom agents (YAML)** | ✅ | ✅ | ✅ | ✅ |
| **Custom agents (authoring UI)** | ❌ | ❌ | ✅ (Phase 2) | ✅ (Phase 2) |
| **Custom models** | ❌ | ❌ | ❌ (Post-MVP) | ❌ (Post-MVP) |
| **Support** | Community | Email | Priority | Dedicated + SLA |

### 11.2 Pricing

| Tier | Monthly | Annual | Target |
|------|---------|--------|--------|
| **Free** | $0 | $0 | Hobbyists, OSS maintainers |
| **Indie** | $9/mo | $79/yr ($6.58/mo) | Solo devs, micro-teams |
| **Pro** | $29/mo | $249/yr ($20.75/mo) | Startups, agencies (5–20 devs) |
| **Enterprise Starter** (1–10 seats, self-serve) | $59/mo | $499/yr | Small enterprises |
| **Enterprise Growth** (11–50 seats, light-touch) | $149/mo | $1,249/yr | Mid-size enterprises |
| **Enterprise Plus** (51+ seats, high-touch sales) | Custom | Required annual | Large enterprises |

**Total Cost of Ownership (TCO = Revue price + customer's AI provider cost).**

Two columns: the cost under the **legacy Anthropic Sonnet 4.5 default** (v1.x), and the cost under the **DeepSeek-V4-Pro default** (v2.0+). The delta is the customer's saving from the model swap alone — *before* `/revue-local` further reduces CI cycles.

**Assumptions (rate card sampled 2026-05-18):**
- Anthropic Sonnet 4.5 on Anthropic API: $3/M prompt + $15/M completion.
- DeepSeek-V4-Pro on OpenRouter: $0.435/M prompt + $0.87/M completion (per `docs/research/deepseek-v4-pro-evaluation.md`).
- Typical review: ~120K prompt + ~20K completion tokens.
- Customers who additionally adopt `/revue-local` shift those API costs to their existing Claude Code subscription, eliminating Revue-side AI spend entirely.

| Tier | Revue price | TCO (v1.x — Anthropic Sonnet baseline) | TCO (v2.0 — DeepSeek default) | Monthly saving |
|------|-------------|----------------------------------------|-------------------------------|----------------|
| Free (25 reviews/mo) | $0 | ~$17 | ~$2 | **~$15 (~88%)** |
| Indie (100 reviews/mo) | $9 | ~$75 | ~$16 | **~$59 (~79%)** |
| Pro (~500 reviews/mo, team of 5) | $29 | ~$359 | ~$64 | **~$295 (~82%)** |
| Enterprise Starter (~1,000 reviews/mo) | $59 | ~$719 | ~$129 | **~$590 (~82%)** |

At Indie TCO (~$16/month under DeepSeek default), Revue is **structurally cheaper** than CodeRabbit's $12/dev/month for a 2-dev team while delivering multi-agent specialised review, BYOK data sovereignty, and source code that never leaves the customer's infrastructure. For teams adopting `/revue-local`, the gap widens further — CI-side AI cost approaches zero as more reviews land pre-commit.

### 11.3 Free Tier Strategy

Launch with **25 reviews/month** to create urgency to upgrade while still delivering value. Track conversion metrics for 6 months post-launch:

- **Free → Indie conversion target:** >5% within 90 days
- **Decision trigger:** If <3% convert → lower to 15/month. If >7% convert → limit is working, hold.
- **Viral growth metric:** Track referral source on signup ("How did you hear about us?")

A conversion tracking dashboard (Epic E6) will provide the data needed to make this decision with confidence.

### 11.4 Enterprise Sales Process

**Enterprise Starter (1–10 seats):** Fully self-serve. Auto-verify email domain + GitHub/GitLab org activity. Instant license key. No human involvement.

**Enterprise Growth (11–50 seats):** Self-serve form + automated Slack alert to sales. Sales reviews within 4 hours, approves 99% of cases. Optional setup call offered in welcome email.

**Enterprise Plus (51+ seats):** Smart chatbot pre-qualification (Intercom, ~$99/month) routes qualified leads to Calendly. Full 45-min discovery/demo/close call. 30-day 10-seat trial before full commitment. See `docs/enterprise-sales-playbook.md` for full call script.

---

## 12. Phased Roadmap

### Phase 1: Foundation — MVP (shipped, v1.0–v2.0)
**Goal:** Ship a working multi-agent reviewer with platform-agnostic adapters and a cost-optimised default.

| Feature | Status | Notes |
|---------|--------|-------|
| GitHub App integration | ✅ Done | Webhook + API adapter |
| GitLab integration | ✅ Done | |
| Bitbucket adapter | ✅ Done | Promoted earlier than original Phase 2 plan |
| Multi-agent BMAD engine | ✅ Done | Cleo, Zara, Kai, Maya, Leo, Nova, Sage, Vex |
| AI provider abstraction via per-model registry + dispatcher | ✅ Done | REVUE-262/263/264 |
| **DeepSeek-V4-Pro on OpenRouter as default model** | ✅ Done | REVUE-267 — cost-driven default swap |
| **`/revue-local` Mode 2 — native Claude Code Task pipeline** | ✅ Done | REVUE-259/260/261 — zero Revue-side AI spend |
| Sage (Resolver) — scoped MVP | ✅ Done | Suggestion-only, confidence-gated |
| `.revue.yml` config schema | ✅ Done | |
| Inline + summary comments | ✅ Done | |
| Configurable blocking | ✅ Done | |
| Self-service workspace onboarding | ✅ Done | Web UI |
| Free / Indie / Pro tier billing | ✅ Done | Stripe + license key |
| Basic analytics (run history, issue counts) | ✅ Done | |
| Documentation site | ✅ Done | |

### Phase 2: `/revue-local` productisation (current focus)
**Goal:** Turn `/revue-local` from an internal dogfooding tool into a customer-facing skill, distributed via a registry, with a clear install path and the existing paywall preserved.

| Feature | Priority | Notes |
|---------|----------|-------|
| **`/revue-local` skill packaging & distribution** | P0 | Distribution mechanics covered in `docs/planning/revue-local-distribution-brief.md` (TBC) |
| **Install + onboarding flow** | P0 | One-command install into customer's Claude Code; auto-detect `.revue.yml` |
| **Pre-commit AI-workflow integration patterns** | P0 | Published patterns / `CLAUDE.md` snippets for Claude Code, Cursor, Windsurf |
| **Cost-saving dashboard** | P0 | Show customer their AI-bill reduction vs CI-only baseline (delta = reviews caught locally × per-review API cost) |
| Customer-cost-care messaging rollout | P0 | README, website, pricing page, launch post — see [[feedback_customer_cost_messaging]] |
| Sage v2 — auto-commit + multi-round loop | P1 | Builds on MVP Sage |
| Custom agent authoring (UI) | P1 | |
| Slack / Teams notifications | P2 | |
| Review analytics dashboard | P2 | Trend data, false positive tracking |

### Phase 2b: CI track — deprecated-but-maintained
**Status:** Receive only keep-the-lights-on maintenance. No new feature investment. Continues to work and stays cheap thanks to the DeepSeek default. Strategic focus has moved to Phase 2 (`/revue-local`).

| Feature | Priority | Notes |
|---------|----------|-------|
| Bug fixes on existing CI integrations | P1 (reactive only) | GitHub Actions, GitLab CI, Bitbucket Pipelines |
| Azure DevOps adapter | P3 | De-prioritised; revisit if customer demand warrants |
| Concurrency specialist (Swift 6, Kotlin coroutines) | P3 | Deferred from original Phase 2 plan |

### Phase 3: Scale (later)
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

## 13. Technical Constraints & Non-Goals

### Constraints
- Source code and diffs must never be stored by Revue's cloud backend
- Agent runner must be runnable as a standalone binary / Docker image / pip package
- Must support air-gapped environments (self-hosted AI + self-hosted VCS)
- Review must complete within 3 minutes for diffs up to 2,000 changed lines
- **AI-model-agnostic.** Business logic, agent prompts, and pipeline code must remain provider-neutral. All provider-specific behaviour lives behind the per-model registry + dispatcher. No direct `anthropic.Anthropic(...)` (or any other provider SDK) usage outside the corresponding `AIClient` implementation. Anthropic-shaped APIs (`output_config`, etc.) and OpenRouter-shaped APIs (`response_format`, `tool_choice_first_turn`) must not leak across the abstraction boundary. New provider support = a registry row + an `AIClient` implementation, never a business-logic conditional.
- **Large diffs (> configurable limit, default 2,000 lines):** Revue stops the review immediately — before any AI call — and posts a single comment explaining the limit, why it exists, and suggesting a logical PR breakdown. Exit is a warning (non-blocking), not a failure. Batch mode (chunked review across multiple agent passes) is a future feature.
- **Graceful degradation:** If an agent fails or times out (default: 90s per agent), Nova proceeds with available findings and marks the failing agent's contribution as unavailable in the summary. The review run does not fail entirely.
- **Monorepos:** Multiple `.revue.yml` files are supported via path-scoped configuration. Each top-level service path can define its own team and agent settings.
- **Token budget:** Each agent receives only the diff portions relevant to its trigger patterns, not the full diff. Cleo is responsible for routing the correct diff slices per agent.

### Legal & Compliance (launch gate)
- **Privacy Policy + Terms of Service are a hard public-launch gate**, not post-launch polish. `revue.sh` is a paid SaaS that collects emails at activation and processes payments, creating GDPR/DSA exposure in the EU; separately, Stripe payout setup will not activate without a live ToS URL. No money-in path exists until these pages are live. Tracked as **REVUE-357** (blocks **REVUE-315** Stripe).
- **Open ownership risk:** the ToS/Privacy copy is template-adapted. Template legal text for a GDPR/DSA-exposed paid product is a liability until a named owner reviews and approves it before it goes live — this is a sign-off decision, not an engineering task.
- **MVP scope boundary:** cookie-consent banner and an Enterprise-tier DPA are explicitly out of scope for launch (deferred per REVUE-357 Out-of-Scope).

### Non-Goals (v1.0)
- Sage does **not** auto-commit fixes — suggestions require explicit developer acceptance
- Sage does **not** fix issues that require context outside the diff — it defers those to humans
- Revue does **not** replace linters or SAST tools (it complements Layer 1)
- Revue does **not** store or index the codebase (it reviews diffs only, not full context)
- Revue is **not** a code search or refactoring tool

### Non-Goals (v2.0 — `/revue-local` shape)
- `/revue-local` is **not** a git pre-commit hook — it is an AI-workflow integration step that lives in the customer's prompt / skill / agent configuration, invoked by the customer's AI-coding agent before the AI commits
- `/revue-local` does **not** replace the customer's AI-coding agent (Claude Code, Cursor, Windsurf) — it runs alongside it as the review step in the write-review-revise loop
- `/revue-local` does **not** require a Revue-side AI API key — inference runs inside the customer's existing Claude Code session at zero Revue-side AI spend

---

## 14. Open Questions

1. **`/revue-local` distribution mechanics (NEW — v2.0 pivot):** How is `/revue-local` packaged, distributed, installed, licensed, and paywall-gated as a customer-facing Claude Code skill? → Covered in `docs/planning/revue-local-distribution-brief.md` (TBC — brief not yet authored); this PRD specifies *what* and *why*, the brief specifies *how it ships*. Outstanding sub-questions: skill registry choice (Anthropic's vs Revue-hosted vs GitHub-based), one-command install pattern, licence-key validation inside the skill, free-tier enforcement in a customer-side execution context.
2. **Cost-saving dashboard methodology:** How do we measure customer AI-bill reduction credibly? → Proposal: instrument `/revue-local` to count "issues caught locally" and multiply by typical per-review CI cost (DeepSeek per-review × team's review frequency). Dashboard surfaces both raw count and estimated dollar saving. Compare against the customer's actual OpenRouter/Anthropic monthly spend if they grant read-only access. **Owner:** PM. **Target resolution:** before the Phase 2 cost-saving dashboard ticket enters sprint planning. Gates the §3.5 ≥40% AI-spend headline KPI.
3. **Agent Marketplace:** Should community-contributed agents be hosted on Revue or distributed via GitHub? → Recommend GitHub-hosted with a curated index on Revue.
4. **Cross-model review:** Priority for Phase 2 or 3? → High value differentiator; deferred to Phase 3 now that `/revue-local` productisation is the Phase 2 spearhead.
5. **Sage v2 auto-commit:** When Sage pushes a fix autonomously — same commit on same branch, or a new commit? → New commit on same branch, clearly attributed to Revue (e.g. `[revue] fix: parameterise SQL query`).
6. **Confidentiality of findings:** Should review comments be private (visible only to the PR author) or public by default? → Public by default, configurable.
7. **Free tier limits:** 25 runs/month, resets monthly. Rationale: enough to evaluate, not enough to avoid upgrading. Viral growth tracked via conversion metrics for 6 months post-launch before adjusting.
8. **Sage confidence threshold:** Is 90% the right cutoff, or should teams be able to configure it? → Recommend 90% default, configurable per project in `.revue.yml`.

---

## Appendix A: Competitive Positioning Summary

| Feature | Revue (v2.0) | CodeRabbit | Greptile | Copilot Review | Snyk Code | SonarCloud |
|---------|--------------|------------|----------|----------------|-----------|------------|
| **Cost-savings positioning (we reduce customer AI bill)** | ✅ first-class pillar | ❌ | ❌ | ❌ | ❌ | ❌ |
| **AI-workflow integration (pre-commit review by AI agent)** | ✅ via `/revue-local` (primary surface) | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Multi-agent specialised review** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **AI-model-agnostic (DeepSeek default, opt-in Anthropic/OpenAI/Azure)** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Customer pays AI provider directly (BYOK) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| GitHub | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| GitLab | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ |
| Bitbucket | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ |
| Code stays in customer infra | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Pre-commit CLI hook (legacy shape) | n/a — superseded by `/revue-local` | ❌ | ❌ | ❌ | ✅ (CLI) | ✅ (CLI) |
| Resolver (fix suggestions) | ✅ MVP | ❌ | ❌ | ❌ | Limited | Limited |
| Resolver (auto-commit loop) | Phase 2 | ❌ | ❌ | ❌ | ❌ | ❌ |
| Custom agents | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Configurable blocking | ✅ | Limited | ❌ | ❌ | ✅ | ✅ |
| Security focus | ✅ (Zara) | Limited | ❌ | ❌ | ✅ primary | ✅ primary |
| Self-hosted option | Phase 3 | ❌ | ❌ | ❌ | ✅ | ✅ (Sonar) |

**Strongest competitive differentiators (v2.0):**
1. **AI-workflow integration via `/revue-local`** — no competitor offers a code-review tool the customer's AI agent invokes inside its own session. Every competitor still assumes review happens *after* code lands on a branch.
2. **Cost-savings as the pricing pillar** — competitors absorb AI costs and price accordingly, hiding the spend; Revue's BYOK + cost-optimised default + `/revue-local` triple makes the saving visible and quantifiable on the customer's own AI bill.
3. **AI-model-agnostic by architecture, not by claim** — DeepSeek default, Anthropic/OpenAI/Azure/custom all supported via the registry; competitors lock to one provider.

---

## Appendix B: Glossary

| Term | Definition |
|------|-----------|
| **Workspace** | A Revue account associated with one or more VCS organisations or groups. Billing and configuration are workspace-scoped. |
| **Review run** | A single execution of the Revue agent pipeline triggered by a PR/MR event. One run = one diff reviewed. |
| **Agent** | A specialised AI persona with a defined system prompt, trigger rules, and focus area. Runs within a review run. |
| **Team** | A named group of agents that run together for a specific review scenario (e.g. `team-security-focus`). |
| **Diff** | The set of changed lines in a PR/MR. Revue reviews diffs only — not the full codebase. |
| **Finding** | A single issue identified by an agent, with severity, location, description, and remediation. |
| **Suggestion** | A Sage-generated fix posted as a platform-native suggested change (GitHub) or apply suggestion (GitLab). Requires explicit developer acceptance. |
| **Blocking** | Configured behaviour where Revue fails the CI job or sets a "changes requested" review status when findings of a specified severity are found. Off by default. |
| **BYOK** | Bring Your Own Key — customer provides their own AI provider API key. Revue never stores it. |

---

## Appendix C: Open Design Decisions

### ✅ RESOLVED: Cleo's `auto` Routing Algorithm

Cleo's routing is a direct evolution of the existing `evaluate_triggers()` implementation in the current GitLab service. The mechanism is proven and carries over unchanged. Only **team auto-selection** is new — agent selection within a team already works via keyword + file pattern triggers.

**Two-step decision:**

**Step 1 — Team selection (new, currently hardcoded to `team-swift-ios`)**

```
Hard limit check (runs first — before any AI call):
  Diff > configurable limit (default: 2,000 lines)
                         →  STOP. Do not run review.
                            Post a single PR/MR comment:
                            "This PR is too large to review automatically.
                             Breaking it into smaller PRs is a best practice.
                             Here are suggested logical breakpoints: [list]"
                            Exit with warning (not failure — don't block merge
                            for a tooling limit).

Security override (highest priority after size check):
  Any of: auth, password, token, jwt, encrypt, decrypt, sql, secret, api_key
  found in diff content  →  force team-security-focus

Size heuristic:
  Diff < 50 lines        →  team-quick (Maya only — fast, low cost)
  50–2,000 lines         →  normal team selection continues below

Language detection (default — file extensions):
  *.swift                →  team-swift-ios
  *.kt / *.kts           →  team-kotlin-android
  *.py                   →  team-python
  *.ts / *.tsx           →  team-typescript
  mixed / unknown        →  team-full-review

Priority order: hard limit → security override → size heuristic → language
```

**Hard limit behaviour in detail:**

When a PR/MR exceeds the limit, Revue posts a comment like:

```
⚠️ Revue — PR too large to review automatically

This PR contains 3,847 changed lines across 42 files.
Revue's limit is 2,000 lines to ensure reliable, focused reviews.

**Why this matters:** Large PRs are harder to review accurately — for
humans and AI alike. Smaller, focused PRs get better feedback, merge
faster, and carry lower risk.

**Suggested breakdown:**
- `src/auth/` (312 lines) → PR 1: Authentication changes
- `src/api/` (891 lines) → PR 2: API layer refactor
- `src/models/` (1,204 lines) → PR 3: Data model updates
- `tests/` (1,440 lines) → PR 4: Test suite additions

The limit is configurable in `.revue.yml`:
  review:
    max_diff_lines: 2000  # increase if needed
```

The breakdown suggestion uses Cleo's diff analysis (grouped by directory/concern) — no extra AI call, uses the `shared_analysis` output.

**Post-MVP (Phase 2): Batch mode**

Instead of stopping, Revue will chunk the diff by logical file groups, run each batch independently through the agent pipeline, and merge findings. Each batch stays within model context limits. The PR/MR receives a single consolidated review across all batches.

The existing `shared_analysis` upfront AI call already produces `complexity`, `security_concerns`, and `performance_concerns` — this output is wired into Step 1 to inform team selection. No additional AI call needed.

**Step 2 — Agent selection within team (already implemented)**

The existing `evaluate_triggers()` function handles this exactly as-is:
- `always: true` agents always run (e.g. Orchestrator, Maya)
- Keyword scan across full diff content activates domain agents
- File path pattern matching activates language-specific agents (e.g. Sora for `*.swift` with `async/await`)
- Fallback to primary agents if no triggers match

**Expanding from 3 to 7 agents:** The trigger system is purely data-driven (YAML team files). Adding Sage, Leo, and future agents (Finn, Dara, Arlo, Remy, Sora, Rex) requires only new agent definition files and updated team YAML — no code changes to the routing logic.

---

### ⏳ OPEN: Token Budget Strategy

Each agent currently receives the full diff. The constraint states agents should receive only relevant diff slices. Slicing logic design needed:
- How to handle overlapping concerns (e.g. a function is both a security risk and a performance issue)?
- Does slicing happen at file level or hunk level?
- **Interim decision:** Keep full diff per agent for MVP (matches current proven behaviour). Revisit in Phase 2 with real token cost data.

### ⏳ OPEN: Diff Caching

If 3 commits are pushed rapidly, Revue will run 3 full reviews on near-identical diffs. Caching strategy needed:
- Invalidation key: commit SHA + config hash (catches both code changes and config changes)
- TTL: 1 hour (covers rapid push scenarios)
- Scope: per workspace, per repo, per branch
- **Interim decision:** No caching in MVP. Instrument token costs in Phase 1, design caching in Phase 2 based on real data.

---

*Market Analysis: see `market-analysis.md`*  
*Implementation reference: see `context/ai-code-review-service/`*  
*v1.3 — Hard diff limit added: stop + suggest breakdown (MVP). Batch mode deferred to Phase 2.*
