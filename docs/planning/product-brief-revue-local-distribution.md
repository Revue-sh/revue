# Product Brief — `/revue-local` Distribution

**Version:** 1.0
**Date:** May 2026
**Status:** Draft — Phase 2 productisation plan
**Owner:** Revue Team
**Parent document:** `docs/planning/prd.md` (v2.0). This brief specifies *how `/revue-local` ships*; the PRD specifies *what* and *why*. Defers from PRD §9.4 and §14 Open Question 1.

> **Sibling-doc contract.** Where the PRD defines customer-facing behaviour for `/revue-local`, this brief defines the distribution channel, install flow, licence enforcement, free-tier counting, paywall mechanics, integration patterns for non-Claude-Code AI agents, the cost-saving dashboard, and the cost-care messaging rollout. Anything the PRD already specifies (the agent pipeline, `.revue.yml` schema, finding format, severity model) is referenced, not duplicated.

---

## 1. Executive Summary

Revue's strategic pivot (PRD §1) shifts the primary customer surface from a CI/CD service to `/revue-local` — a Claude Code skill the customer's AI-coding agent invokes inside its own workflow, before the AI commits code. The product story Revue tells customers, in priority order, is:

1. **Your AI bill is the win.** Every issue Revue catches inside the customer's AI session is one fewer review cycle their CI bot has to pay for. Revue is positioned as a partner in AI-spend discipline at a moment when AI inference is a board-level line item.
2. **Customer pays nothing extra for inference.** `/revue-local` consumes the customer's existing Claude Code subscription. No Revue-side per-API-call billing.
3. **Same multi-agent quality.** The skill runs the identical agent pipeline (Cleo → Zara/Kai/Maya/Leo → Nova → Sage → Vex) that the CI track runs, against the same `.revue.yml`. One configuration, two execution surfaces.

This brief addresses the gaps the PRD explicitly leaves open:

- **Distribution channel** — recommended: **GitHub-based distribution with a one-line installer hosted at `revue.sh/install`**, paired with a curated registry index at `revue.sh/skills`. Anthropic's first-party skill registry (when it stabilises) becomes a downstream syndication target, not the primary channel. Avoids Anthropic lock-in at the distribution layer, mirroring the AI-model-agnostic principle in PRD §6.4.
- **Licence enforcement in fully-local execution** — recommended: **signed JWT licence keys with a single daily online check against the licence server, cached for 24h, identical rules for every tier; server-side usage ledger as the authoritative free-tier counter**. The threat model is anti-piracy (unauthorised redistribution of a paywall-stripped fork), not "untrusted skill execution". See §6.
- **Cost-saving dashboard** — recommended: **dual surface**. Per-session summary in CLI output (immediate gratification), aggregate spend-delta view in the `Revue` web app (monthly trend). Telemetry uses one anonymous event per `/revue-local` invocation — never source, never diffs. See §7.

Phase 2 of the roadmap (PRD §12) executes against this brief.

### 1.1 Why now

Two market windows are open simultaneously:

1. **AI inference spend is becoming a procurement-grade conversation.** Engineering leaders are scrutinising Claude/GPT/OpenRouter line items the same way they scrutinise AWS bills. A code-review tool that *reduces* their AI line item — instead of adding to it — is differentiated by economic structure, not feature claims.
2. **Skills, custom tools, and `.rules` files are crossing the chasm into mainstream AI-coding workflows.** Claude Code's skill mechanism, Cursor's `.cursor/rules`, and Windsurf's `windsurfrules` all expose the same insertion point: a place where a customer's AI agent can be instructed to invoke an external review step before commit. `/revue-local` is that step.

Missing either window risks Revue being categorised as "another AI code-review tool that adds to my AI bill" instead of "the review tool that lowers it."

---

## 2. Problem & Opportunity

### 2.1 Customer pain

The PRD frames the problem in terms of code-quality bottlenecks (§2.1). Phase 2 reframes the same problem in terms of **money**.

| Pain | Where it bites | Today's cost shape |
|------|----------------|--------------------|
| AI-generated code carries 2× the bug rate of human-written code | Every PR opened by an AI agent | A CI-side AI reviewer re-pays inference for every bug the AI's own session could have caught earlier |
| PR volume grows 10× while reviewer headcount stays flat | Code-review queues, CI minutes | Compounding: each AI-written PR costs another AI review pass |
| AI inference pricing changes (e.g. Anthropic Sonnet step-up) are unpredictable | Monthly AI bill | The same review workload costs materially more month-over-month with no quality improvement |
| AI agents commit without an independent review pass | Layer 2 of the 4-layer model (PRD §2.2) | The bug arrives at CI; CI pays to find it |

The CI-only product shape (Revue v1.x) addresses **bug detection** but not **cost**. By the time a Revue CI review fires, the customer has already paid for the AI session that wrote the bug, and is now paying again for the AI session that reviews it. `/revue-local` collapses those two payments into one.

### 2.2 The leverage point

The single highest-leverage moment in an AI-coding workflow is the gap between *AI writes* and *AI commits*. The customer's AI agent is already loaded with the context, the diff is staged but unflushed, and the cost of intervention is one in-session message. This is the moment where:

- A finding can be acted on by the same session that produced the code (no context reload).
- A fix lands before any CI minute is billed.
- A bug never propagates to a PR, an inline comment, or a CI re-review.

`/revue-local` is the product that owns this moment.

### 2.3 What this brief unlocks

| Unlocks | Mechanism |
|---------|-----------|
| Phase 2 of the roadmap (PRD §12) | Concrete distribution + install + paywall plan, removing the last blockers to building |
| The "we care about your AI bill" positioning (PRD §11, §3.5) | Customer-visible cost-saving dashboard + cost-care messaging rollout |
| Free-tier conversion (PRD §11.3) | Free-tier usage counted server-side; soft-paywall UX inside the skill drives Indie upgrades |
| AI-model-agnostic principle at the distribution layer | GitHub-based primary channel; Anthropic registry is downstream syndication |

---

## 3. Goals & Non-Goals

### 3.1 Measurable goals (Phase 2 KPIs)

| # | Goal | Target | Measurement |
|---|------|--------|-------------|
| G1 | `/revue-local` skill installs | 500 cumulative installs by month 6 post-launch | Installer telemetry: one anonymous event per successful install |
| G2 | Wire-up rate (installs that integrate into an AI-coding workflow) | ≥ 60% of installs run `/revue-local` at least once with a non-empty diff within 7 days of install | First-invocation usage-emit carries an "integrated" flag |
| G3 | Customer AI-spend reduction (`/revue-local` users vs CI-only baseline) | **MVP:** instrumented; baseline cohort established. **6-month:** ≥ 40% reduction (mirrors PRD §3.5 two-stage phrasing) | Spend-delta dashboard: reviews caught locally × typical per-review API cost from PRD §11.2 TCO table. Baseline cohort = customers with ≥ 30 reviews in both 90-day windows |
| G4 | Time-to-first-review (install → first finding rendered) | ≤ 5 minutes for the 75th percentile | Installer emits install timestamp; first-review timestamp from first-invocation usage-emit |
| G5 | Free → Indie conversion among `/revue-local` users | ≥ 7% within 90 days (vs PRD §11.3 baseline of ≥ 5% for CI-only users) | Stripe + licence-key correlation |
| G6 | Skill-discoverability rank | Top-3 result on `revue.sh/skills`, top-10 on Anthropic skill registry within 90 days of Anthropic listing | Manual quarterly check |

### 3.2 Non-goals (explicit)

- **Not a git pre-commit hook.** `/revue-local` lives in the AI-workflow integration layer (Layer 2), not in `.git/hooks/`. Customers can wire a git hook on top of the skill if they want; Revue does not ship one in Phase 2.
- **Not a single-vendor skill.** The skill is not exclusively a Claude Code skill. The Phase 2 install flow targets Claude Code, Cursor, and Windsurf with first-class snippets (see §8). The underlying review entrypoint is a CLI binary; the "skill" is a thin wrapper.
- **Not a code-search or codebase-indexing tool.** `/revue-local` reviews diffs only (consistent with PRD §13 non-goals).
- **Not free of usage limits.** Phase 2 preserves the existing tier structure exactly (PRD §11.2). The skill enforces tier caps with the same logic the CI track uses; the surface is just different.
- **Not a marketing channel for other Revue products.** The skill output is review findings + a one-line cost-saving footer. No upsell prompts inside the skill output beyond the cost-saving footer.
- **Not a replacement for the CI track.** The CI track remains supported (PRD §12 Phase 2b). Customers wanting both layers run both. The skill catches issues pre-commit; CI catches what slips through and what regresses.

---

## 4. Distribution Strategy

Three distribution channels are viable. Each is evaluated on the same six dimensions; the recommendation closes the section.

### 4.1 Option A — Anthropic's official skill registry / marketplace

**Shape.** Anthropic publishes a skill registry tied to Claude Code; Revue submits `/revue-local` as a registered skill; customers install via Anthropic's discovery surface (e.g. `claude skill install revue-local`).

| Dimension | Assessment |
|-----------|------------|
| Discoverability | Highest within Claude Code's user base. Lowest outside it — Cursor and Windsurf users see nothing. |
| Time-to-ship | Dependent on Anthropic's registry maturity. As of 2026-05, the registry surface is still evolving; betting Phase 2 on it adds external schedule risk. |
| Paywall enforcement | Indirect — Revue must add licence-key validation post-install, since the registry does not gate by paid status. No worse than other channels, but no better. |
| Lock-in risk | **High.** Distribution becomes coupled to one AI vendor, contradicting the AI-model-agnostic principle (PRD §6.4). If Anthropic policies, pricing, or terms shift, the channel shifts with them. |
| AI-agnostic compatibility | **Poor.** The Cursor and Windsurf paths are second-class by definition. |
| Pros | Single best surface for Claude Code users. Low friction for that audience. |
| Cons | Vendor lock-in at the distribution layer; non-Claude-Code users not served; ship date dependent on Anthropic. |

### 4.2 Option B — Revue-hosted skill registry (`revue.sh/skills`)

**Shape.** Revue runs its own skill index at `revue.sh/skills`. Customers install via a Revue installer command (`curl -sSL https://install.revue.sh | bash`, or `pipx install revue-local`). The index lists `/revue-local` and any future Revue skills.

| Dimension | Assessment |
|-----------|------------|
| Discoverability | Low at launch — Revue does not yet have organic search authority for skill discovery. Improves over 12 months with SEO, launch posts, and inbound. |
| Time-to-ship | Highest — Revue controls the surface end-to-end. Phase 2.a ship target unblocked. |
| Paywall enforcement | Best — installer + skill talk to the Revue API for licence validation by default. Tightest control loop. |
| Lock-in risk | None at the distribution layer (Revue's own surface). |
| AI-agnostic compatibility | Native — the installer targets multiple AI-coding tools (Claude Code, Cursor, Windsurf) symmetrically. |
| Pros | Time-to-ship, paywall control, vendor neutrality. |
| Cons | Discoverability burden falls entirely on Revue. Without marketing investment, the index sees little organic traffic. |

### 4.3 Option C — GitHub-based distribution (clone-and-install)

**Shape.** The skill lives in a public GitHub repo (`revue-io/revue-local`). Customers install by running a one-line installer that clones the repo into the right location for their AI tool and writes the necessary config. The installer is hosted at `revue.sh/install` (CDN-fronted shell script). The repo is the *authoritative source*; the installer is the *thin convenience layer*.

| Dimension | Assessment |
|-----------|------------|
| Discoverability | Solid — GitHub stars, README SEO, and the broader Awesome-X list ecosystem give organic surface area. Pairs naturally with launch-post content. |
| Time-to-ship | High — no platform-specific approval queues, no third-party gate. Phase 2.a ship target unblocked. |
| Paywall enforcement | Same as Option B — installer + skill validate against Revue API. The source being open does not weaken enforcement: the licence-key check is the active artefact a pirate fork would have to rip out, and the official channel (signed release + revue.sh/skills index) is what honest customers consume (see §6). |
| Lock-in risk | None — GitHub is provider-neutral relative to the AI vendor stack. Worst case (GitHub policy change), the repo migrates to GitLab/Codeberg with no skill-level disruption. |
| AI-agnostic compatibility | Native — installer detects the user's AI tool (Claude Code / Cursor / Windsurf) and writes the right config. |
| Pros | Discoverability, time-to-ship, AI-vendor neutrality, transparency (open install script builds trust). |
| Cons | The skill code is open, which means a forked-and-stripped clone is technically possible — addressed in §6 via the anti-piracy mix (active licence check, vendor-controlled channels, signed releases, official-version signalling). |

> **Note on `revue.sh/*` URLs throughout this brief.** All references to `revue.sh/install`, `revue.sh/activate`, `revue.sh/skills`, `revue.sh/dashboard` etc. describe the **post-MVP steady-state shape**. The `revue.sh` domain is not purchased until MVP launch (per memory `project_api_revue_io_gap.md`). Until then, the same install script content is served from `raw.githubusercontent.com/cbscd/revue/main/scripts/install.sh`, the licence-validation endpoint from a pre-existing fly.dev URL, and the curated skill catalogue from the GitHub repo's `README.md`. The DNS swap at MVP launch is a one-line content update across these surfaces — no behavioural change to customers.

### 4.4 Recommendation

**Recommended channel: Option C (GitHub-based distribution) as primary, Option B (`revue.sh/skills` registry index) as the curated catalogue surface, Option A (Anthropic registry) as downstream syndication when stable.**

Concretely:

1. **Primary install path:** `curl -fsSL https://revue.sh/install.sh | bash` post-MVP, or the equivalent GitHub-raw URL pre-MVP (see §5.2). The installer is a thin bootstrapper that delegates to `uv tool install revue-local` (with `pipx` as a fallback path), detects the user's AI tool, writes config, and prompts for licence-key activation. Updates flow through `uv tool upgrade` — no bespoke updater to maintain.
2. **Discovery surface:** `revue.sh/skills` lists `/revue-local` with a copy-paste install command and links to the GitHub repo. This is also where the cost-saving dashboard (§7) lives, so the page does double duty.
3. **Downstream syndication:** when Anthropic's skill registry is stable enough to warrant the integration cost, Revue submits `/revue-local` there as well. The registry entry points back to the canonical installer; the GitHub repo remains the source of truth.

**Why this combination wins on the load-bearing criteria:**

- **AI-vendor neutrality** (mirrors PRD §6.4). The primary channel does not couple distribution to Anthropic.
- **Time-to-ship.** No third-party approval queue gates Phase 2.a.
- **Discoverability.** GitHub + a curated Revue index + (later) Anthropic registry covers three discovery surfaces, not one.
- **Paywall enforcement is independent of channel** (see §6) — the same enforcement model works across all three.

### 4.5 Channel rollout sequencing

| Phase | Channel | Trigger to advance |
|-------|---------|---------------------|
| Phase 2.a | GitHub primary + `revue.sh/skills` index | Ships at Phase 2.a launch |
| Phase 2.b | Add Cursor/Windsurf installer paths to the same primary installer | Phase 2.b polish window |
| Phase 2.c | Submit to Anthropic skill registry as downstream syndication | Anthropic registry has documented submission process + paywall-compatible model |

---

## 5. Install Flow

### 5.1 Design goals

- **One command.** Copy-paste from `revue.sh/skills` into a terminal; install completes in under 60 seconds on a warm cache.
- **Detect, don't ask.** The installer auto-detects which AI-coding tool the user runs (Claude Code, Cursor, Windsurf) by probing for known config paths. Falls back to a multiple-choice prompt only when detection is ambiguous.
- **Idempotent.** Re-running the installer upgrades in place without breaking existing config.
- **Honest about what is changed.** The installer prints the exact files it will create or modify, and the exact lines it will append to existing files, *before* doing so. The user can `-y` to skip the confirmation in scripted contexts.
- **Auto-detect `.revue.yml`.** If the current working directory contains `.revue.yml`, the installer uses it. If not, it offers to generate a minimal one from the project's detected language.

### 5.2 One-command install

**Post-MVP (once `revue.sh` is purchased and DNS lands):**

```bash
curl -fsSL https://revue.sh/install.sh | bash
```

**Pre-MVP / dogfood phase (GitHub-hosted, identical script content):**

```bash
curl -fsSL https://raw.githubusercontent.com/cbscd/revue/main/scripts/install.sh | bash
```

The install script is **idempotent** — re-running it upgrades in place. It is a thin bootstrapper that delegates to a real package manager (see §5.3); we do not maintain a bespoke updater.

Variants:

```bash
# Pin to a specific version
curl -fsSL <install-url> | bash -s -- --version 2.1.0

# Non-interactive (CI / scripted)
curl -fsSL <install-url> | bash -s -- --yes --tool claude-code

# Install globally instead of project-local
curl -fsSL <install-url> | bash -s -- --global

# Uninstall
curl -fsSL <install-url>/uninstall | bash
```

Equivalent package-manager install paths shipped alongside:

```bash
uv tool install revue-local          # preferred — fastest, isolated, modern Python tool installer
pipx install revue-local             # alternative for customers already on pipx
brew install revue-io/tap/revue-local  # macOS / Linuxbrew users
```

All paths funnel into the same first-run experience (§5.4). **Updates**:

```bash
uv tool upgrade revue-local          # the canonical update command
# or re-run the install script — idempotent, picks up the latest tagged release
```

Additionally, every `/revue-local` invocation piggy-backs on the daily licence check (§6.3) to read the latest published version; if the local install is behind by ≥1 minor version, the skill prints a one-line non-blocking nudge ("v2.x.x available — run `uv tool upgrade revue-local` to update"). For security-critical updates, the licence-validation endpoint returns a `required_min_version` and the skill enforces it on next invocation.

> **No code-signing requirement.** Because `/revue-local` ships as a Python wheel loaded by the customer's existing Python interpreter (via `uv tool install` / `pipx`), the orchestration binary is never directly launched by the OS. macOS Gatekeeper and Windows SmartScreen do not apply. An Apple Developer ID (~$99/yr) and Authenticode certificate (~$300/yr) are **not required** for this distribution shape. They would only become relevant if Revue chose to additionally ship a standalone native binary outside the wheel — which Phase 2.a does not.

### 5.3 What the installer does

1. **Detects the host AI tool.**
   - Probes for `~/.claude/`, `~/.cursor/`, `~/.codeium/windsurf/` (and equivalents). Records the detected tools.
   - If multiple tools are detected, prompts the user to pick the primary one. The skill can later be wired to all detected tools with `revue-local wire --all`.
2. **Downloads the skill bundle.**
   - Clones (or downloads a tagged archive of) `github.com/revue-io/revue-local` into `~/.revue/skills/revue-local/`.
3. **Writes the host-tool integration.**
   - For Claude Code: writes `~/.claude/skills/revue-local/SKILL.md` pointing to the canonical bundle.
   - For Cursor: writes `.cursor/rules/revue-local.mdc` (project-local) or `~/.cursor/rules/revue-local.mdc` (global), depending on user choice.
   - For Windsurf: writes the equivalent `windsurfrules` entry.
4. **Prompts for licence-key activation.**
   - Default flow: opens `https://revue.sh/activate?installer=<install-id>` in the user's browser. The browser flow returns a JWT licence key, which the installer writes to `~/.revue/licence` (chmod 600).
   - Headless flow: `revue-local activate --key <KEY>` for CI and scripted installs.
   - Skip flow: the user can defer activation; the skill enters Free-tier mode (PRD §11.2) on first invocation and prompts to activate.
5. **Auto-detects (or offers to create) `.revue.yml`.**
   - If `$PWD/.revue.yml` exists, it is left alone.
   - Otherwise the installer offers to write a minimal `.revue.yml` derived from the project's primary language (PRD §8.1 default schema).
6. **Prints the cost-savings welcome message.**
   - First-run output leads with the cost narrative (see §9 for exact copy).
7. **Verifies install.**
   - Runs `revue-local --version`, `revue-local doctor` (checks licence + config + tool integration), and prints next steps.

### 5.4 First-run experience

The first successful invocation of `/revue-local` from inside the host AI tool emits a one-time onboarding panel — terse, dense, copy-pasteable:

```
Revue is wired into Claude Code.

How to use:
  Before each commit, ask your agent to run `/revue-local`.
  Findings appear inline. Resolve High-severity findings before commit.

You're on the Free tier — 25 reviews/month included.
Reviews caught locally save you the CI-side AI inference cost.

Estimated saving this month so far: $0.00 (run your first review to start)
See your dashboard: https://revue.sh/dashboard
```

After this one-time panel, subsequent invocations print only the cost-saving footer (see §7.4) below findings.

### 5.5 `CLAUDE.md` (and equivalent) wiring snippet

The installer appends — *with explicit confirmation* — a wiring block to the project's `CLAUDE.md`, `.cursor/rules/`, or `windsurfrules` file. Exact copy is reproduced in §8. The block instructs the AI agent to call `/revue-local` before commit and to act on High-severity findings.

If the user declines the wiring step, the skill is installed but inert until the user manually wires it. The installer prints the snippet to copy-paste in that case.

### 5.6 Failure modes

| Failure | Detection | Recovery |
|---------|-----------|----------|
| No supported AI tool detected | Installer probes return empty | Print a list of supported tools + manual install instructions; exit 1 |
| Network failure during clone | Git/curl error | Retry up to 3 times with backoff; on final failure, print offline install instructions |
| Licence activation server unreachable | HTTP timeout | Installer exits with a clear error and a retry command; activation must complete online (the daily-check model in §6.3 requires at least one successful validation before first use) |
| `.revue.yml` exists but malformed | YAML parse error during `doctor` | Skill installs successfully; `doctor` prints the specific parse error and a link to the schema docs |
| Conflicting existing skill | File at target path | Refuse to overwrite without `--force`; print diff |
| Host AI tool is corporate-locked (no user-writable rules dir) | Permission error on write | Print the manual wiring snippet for the user's admin to install |

---

## 6. Licensing & Paywall

### 6.1 Threat model stated honestly

The threat model for `/revue-local` is **anti-piracy / anti-circumvention**, not "untrusted skill execution". This distinction matters because the two have different solutions.

**What is *not* the threat.** Claude Code (and Cursor, and Windsurf) runs skills as the user instructs them. We already ship ad-hoc skills in this codebase (`/revue-local`, `/dogfood`, `/jira-ticket`, etc.) and have never observed a runtime-trust problem from the host AI tool: the tool faithfully executes what the skill tells it to. There is no scenario where the AI tool secretly rewrites the skill, lies about responses, or refuses to call documented endpoints. Treating the skill as "untrusted code the AI agent might subvert" is the wrong frame and produces over-engineered mechanisms (e.g. server-delivered agent prompts per session) for a problem that does not exist.

**What *is* the threat.** Somebody forks the public skill repo, strips the licence-key check, redistributes it as a free clone, and a non-trivial fraction of would-be paying customers install the clone instead of the official version. This is paywall circumvention by redistribution, the standard threat for any paywalled, customer-installed software whose source is open.

The standard tools against this threat are well-known and Revue uses the standard mix:

1. **An active licence-key check that a pirated fork must explicitly remove.** Not obfuscation — just a check whose absence is what a fork has to ship. The work to maintain the patch across upstream updates is friction that scales over time.
2. **Distribution channels Revue controls.** The one-line installer is hosted at `revue.sh/install`. The curated catalogue lives at `revue.sh/skills`. The GitHub repo is canonical; release artefacts are signed. Honest customers, given a choice between `revue.sh/install` and `random-fork/install.sh`, pick the official channel.
3. **Clear "this is the official version" signalling.** Signed release tarballs (Sigstore / cosign), a published version manifest at `revue.sh/skills/manifest.json` listing the current canonical SHA, and `revue-local --version` reporting against the manifest. A pirated fork either keeps the manifest URL (in which case `--version` flags it as stale / unofficial) or replaces it (which makes the fork distinguishable on inspection).

These three together are the recommended mix. They do not require treating the skill as untrusted; they do not require server-delivered agent prompts; they do not require a Nuitka-compiled enforcement binary. They are commensurate with the actual threat.

### 6.2 What stays the same

Per Daniel's directive (project_anthropic_deprioritisation §4), the tier structure stays exactly as in PRD §11.2:

| Tier | Reviews/month | Price |
|------|---------------|-------|
| Free | 25 | $0 |
| Indie | 100 | $9/mo |
| Pro | Unlimited | $29/mo |
| Enterprise (Starter / Growth / Plus) | Unlimited | $59 / $149 / Custom |

No change to pricing, tier caps, or feature gating. The skill enforces the same caps the CI track enforces.

### 6.3 Licence-key validation — daily check, 24h cache, same rules every tier

**Validation model: signed JWT + one online check per day against the Revue licence server, cached for 24h, identical rules for Free, Indie, Pro, and Enterprise.**

1. **Licence key shape.** A JWT signed with Revue's private key. Claims: `tier`, `seat_id`, `workspace_id`, `agents_allowed`, `iat`, `exp` (1 year). Public-key verification offline, so the skill can short-circuit obvious-invalid keys without a network round-trip.
2. **First `/revue-local` invocation of the day** triggers an online licence-validity check against `POST /api/v2/licence/validate` (payload: `{ licence_jwt, workspace_id, machine_fingerprint }`).
3. **On success**, the skill caches `{ valid_until: now + 24h, tier, reviews_remaining_this_period, agents_allowed }` locally. The 24h-from-validation-timestamp window is preferred over a calendar-day boundary because it makes the customer experience predictable across time zones and travel — a successful check at 23:50 local time should not expire ten minutes later. Subsequent `/revue-local` invocations within that 24h window run without a network round-trip for licence validation.
4. **On failure** (network unreachable, server unreachable, invalid response), the skill continues to run *if* a previous successful check is still inside its 24h window. The cached "valid" stays in force until it expires.
5. **After 24h without any successful check**, `/revue-local` blocks usage and surfaces a clear error message: *"Licence validation unavailable for >24h — restore network connectivity or contact support@revue.sh."* No partial-functionality fallback, no degraded mode; the skill simply refuses to run a review until a fresh check succeeds.
6. **No tier-specific grace logic.** The same rules apply to Free, Indie, Pro, and Enterprise. One mechanism, one code path.

**Rationale (load-bearing — keep verbatim).** Network access is a prerequisite for using Claude Code itself. If the customer cannot reach our licence server, they cannot reach Anthropic's API either — so requiring network for licence checks does not introduce a new offline-mode requirement. A daily check + 24h cache is the minimum-viable mechanism: one network call per day, full offline tolerance for short blips, hard block only after a full day without connectivity. Simpler than tier-graded grace, easier to communicate, single code path.

**Per-invocation usage emit.** Each completed review POSTs `{ workspace_id, finding_count, duration_ms, model_used }` to `POST /api/v2/usage/emit`. This is best-effort: failed emits are queued locally and flushed on the next successful network round-trip. The server is authoritative for `reviews_remaining_this_period`; the skill *displays* the balance returned by the most recent successful exchange.

**Anti-piracy posture, restated.** Per §6.1, the licence check is the active artefact a pirated fork has to remove, and the signed-release + version-manifest mechanism is how honest customers verify they are running the official build. The daily check + 24h cache is the mechanism for honest customers; the anti-piracy mix is what protects revenue against forks.

### 6.4 Free-tier counting

**Model: server-side ledger is authoritative; the skill is an emitter.**

The Free tier (25 reviews/month) is enforced as follows:

1. Each completed review emits a usage record (§6.3). The skill displays the remaining balance returned by the server.
2. When `reviews_remaining_this_period` hits 0, the next daily validation returns `{ tier: "Free", reviews_remaining: 0, paywall: true }`. The skill responds by:
   - Refusing to start a multi-agent review.
   - Printing a paywall message with a one-click upgrade URL (`revue.sh/upgrade?from=local-skill&workspace=...`).
   - Exiting non-zero so the host AI agent surfaces the paywall in its next response.
3. Within the 24h cache window, the skill counts reviews against the cached balance and refuses to proceed when the cache shows 0 remaining, even if the server has not yet been re-contacted. The next successful daily check reconciles cache against server.

A pirate fork that strips the paywall check is the §6.1 threat, addressed by the anti-piracy mix there (active check, vendor-controlled channels, signed releases). It is not addressed by routing agent prompts through the server per session — that mechanism is dropped from this brief.

### 6.5 Validation summary

| Tier | Validation mode | Cache window | Hard block |
|------|-----------------|--------------|------------|
| Free | Daily online check | 24h from last successful check | >24h without a successful check |
| Indie | Daily online check | 24h from last successful check | >24h without a successful check |
| Pro | Daily online check | 24h from last successful check | >24h without a successful check |
| Enterprise | Daily online check | 24h from last successful check | >24h without a successful check |

Identical mechanism for every tier. No tier-specific grace windows, no Enterprise carve-out.

### 6.6 Multi-licence per team handling

A team that buys an Indie or Pro subscription receives N licence JWTs (one per seat). Each developer installs their own and activates with their own licence. The server enforces per-licence concurrent-machine caps via the `machine_fingerprint` recorded at validation; the workspace dashboard shows aggregate usage. There is no team-licence-key model in Phase 2 — Phase 3 can add SSO-driven enterprise issuance, but Phase 2 stays on per-seat keys.

### 6.7 What we do *not* claim

- We do not claim the licence check is uncrackable. A determined fork can rip it out. The anti-piracy mix (§6.1) raises the friction; it does not eliminate it.
- We do not claim the daily check survives every network topology. Customers on persistent-offline networks (air-gapped CI, restricted enterprise networks) need a separate provisioning path. Phase 2 does not ship one; Phase 3 may, if customer demand emerges.

The product economics work as long as paying customers pay willingly. The enforcement mix raises the cost of free-riding above the cost of an Indie subscription for the median customer. That is the right bar.

---

## 7. Cost-Saving Dashboard

Addresses PRD §14 Open Question 2.

### 7.1 What we measure

Two signals per `/revue-local` invocation, both server-side, neither requiring source-code visibility:

| Signal | Source | What it represents |
|--------|--------|--------------------|
| **Findings caught locally** | `usage_emit` event from the skill | Count of High/Critical findings the customer's AI agent saw before commit |
| **Estimated per-review API cost** | `models_registry.yml` cost table | Static per-token cost × typical review token count (PRD §11.2 baseline: 120K prompt + 20K completion). One value per supported provider/model. |

The estimated dollar saving per review is the product: `findings_caught × probability_of_triggering_a_CI_review_cycle × per_review_API_cost`. The middle term is set to **1.0 in v1 of the dashboard** — the simplest defensible default, conservative on Revue's side (we are not inflating the saving). Phase 2.b can refine this with telemetry from customers who run both layers.

### 7.2 What the dashboard never sees

- No source code.
- No diffs.
- No finding text (only finding *count* and *severity*).
- No file paths.
- No commit messages.

The privacy posture is identical to the CI orchestrator (PRD §4.2): the Revue server sees licence + usage metadata only. The cost-saving dashboard is built entirely from already-collected metadata.

### 7.3 Where the dashboard lives

**Dual surface, recommended:**

1. **In-CLI summary** (immediate gratification, every invocation):
   ```
   Revue caught 2 High-severity findings before commit.
   Estimated saving on this commit: $0.07 (DeepSeek-V4-Pro on OpenRouter pricing).
   Month-to-date saving: $4.23 across 61 reviews.
   ```
   Printed below the findings, one block, three lines max. Always visible, never burying the cost narrative.

2. **Web dashboard** (`https://revue.sh/dashboard`, monthly trend):
   - Headline number: total estimated saving this billing period.
   - Sparkline: reviews caught locally per day.
   - Comparison band: estimated CI-only spend vs estimated `/revue-local`-augmented spend.
   - One CTA: "Tell your team" (share link generator) or "Upgrade to Indie" (if Free-tier user is consistently saving more than $9/mo).

**Why dual.** The CLI footer ensures the cost narrative is impossible to miss in day-to-day workflow. The web dashboard provides the monthly-aggregate view leadership wants for procurement conversations. Single-surface designs lose one audience or the other.

### 7.4 Telemetry instrumentation

The skill emits a single anonymous event per invocation. The event payload is fixed:

```json
{
  "workspace_id": "<scoped to workspace, not user-identifying>",
  "finding_counts": {"critical": 0, "high": 2, "medium": 4, "low": 1},
  "duration_ms": 38421,
  "model_used": "deepseek/deepseek-v4-pro",
  "review_size_class": "small | medium | large",
  "ai_host": "claude-code | cursor | windsurf | other",
  "skill_version": "2.1.0"
}
```

The event is the same one that drives free-tier counting (§6.3) — one transmission, two consumers. No additional network calls. Emits failing for network reasons are queued locally and flushed on the next successful round-trip (consistent with the daily-check model in §6.3).

### 7.5 Comparing against the customer's actual AI bill

For customers who want a fully-grounded saving figure (instead of a model-based estimate), Phase 2.c offers an optional read-only OAuth connection to their OpenRouter / Anthropic billing API. The dashboard then renders the *actual* monthly spend alongside the *projected* spend without `/revue-local`. This is opt-in and never required — the model-based estimate is the default.

### 7.6 Privacy and compliance posture

- The metadata payload contains no PII, no code, no diffs, no file paths.
- `workspace_id` is opaque and not linkable to a developer identity across workspaces.
- Customers can disable telemetry entirely with `revue-local --no-telemetry` or `REVUE_TELEMETRY=off`. Disabling telemetry disables the cost-saving dashboard for that user; the skill still functions, and the daily licence check still runs (the licence check is separate from telemetry and is required for usage regardless).
- Enterprise tier offers a self-hosted telemetry sink, mirroring the air-gapped support story (PRD §4.2).

---

## 8. Customer Workflow Integration Patterns

Three AI-coding tools are first-class for Phase 2: Claude Code, Cursor, and Windsurf. Each gets a published snippet the installer can write (with confirmation) into the customer's project. The snippets are deliberately short — the goal is to instruct the AI agent without lecturing it.

### 8.1 Claude Code (`CLAUDE.md`)

Wiring block appended to the project's `CLAUDE.md`:

```markdown
## Pre-commit review (Revue)

Before staging a commit, invoke `/revue-local` on the diff against the
current base branch. The skill returns multi-agent findings with severity.

Rules:
- Resolve any Critical or High finding before committing, or request
  explicit user override.
- Re-run `/revue-local` after each fix to confirm the finding cleared.
- Medium and Low findings are advisory; surface them in the commit message
  if you decide not to fix them.

Why this matters: every issue Revue catches here is one fewer CI review
cycle billed against your AI subscription. See revue.sh/dashboard for
your saving.
```

### 8.2 Cursor (`.cursor/rules/revue-local.mdc`)

```mdc
---
description: Pre-commit review via Revue's /revue-local skill.
globs: ["**/*"]
alwaysApply: false
---

Before committing code you have just written or modified, run `/revue-local`
on the staged diff. Use the resulting findings as follows:

- Critical / High: resolve before commit, or request explicit override.
- Medium: surface in the commit message if not addressed.
- Low: address opportunistically.

After each fix, re-run `/revue-local` to confirm the finding cleared.

Rationale: catching issues here means fewer CI review cycles billed
against your AI inference subscription.
```

### 8.3 Windsurf (`windsurfrules`)

```
# Revue — pre-commit review

When the user signals intent to commit (or you are about to commit on the
user's behalf), invoke `/revue-local` against the staged diff first.

- Resolve any Critical or High finding before commit.
- Re-run `/revue-local` after each fix.
- Capture Medium/Low findings in the commit body if not fixed.

The skill runs Revue's multi-agent review in your current session. Issues
caught here are not paid for again by CI.
```

### 8.4 Cross-tool entry point (CLI)

All three tools call the same underlying CLI: `revue-local review --base main`. The skill wrapper is thin; the work is in the CLI. This means customers using less-common AI tools (Aider, Continue, custom internal agents) can wire `/revue-local` by instructing their agent to run the CLI directly. The installer can write a generic `.revue/AGENTS.md` for these cases on request.

### 8.5 Anti-pattern (do not ship)

The wiring snippets must not:

- Add Revue branding inside the customer's prompt context budget beyond the minimum (the customer pays for their token budget, not Revue).
- Instruct the agent to upsell, market Revue, or mention competitors.
- Require any data flow outside the skill itself (no "send me the diff to grade").
- Include emojis (the customer's terminal output is theirs; we keep it austere).

---

## 9. Cost-Care Messaging Rollout

Applies the rule from `feedback_customer_cost_messaging`. Each customer-facing surface gets concrete draft copy the messaging team can lift or refine. All copy is British English, no marketing fluff, leads with the customer outcome.

### 9.0 Two cost-savings figures — disambiguation

Revue's cost-savings story carries **two distinct percentages**. They measure different deltas and **compound** for customers who do both. Every customer-facing surface below must make clear which delta is being quoted.

| Figure | What it measures | Where it lives |
|---|---|---|
| **~79–88%** | Total cost of ownership reduction from swapping the Anthropic Sonnet 4.5 baseline to the DeepSeek-V4-Pro default (model-swap delta) | PRD §1, PRD §11.2 TCO table, Phase 2 epic E-P2A-S7 |
| **~40%** | Customer AI-inference reduction from moving from CI-only review to `/revue-local` pre-commit review (workflow delta — fewer paid review cycles) | Brief §9.1 README, §9.4 launch post, brief §3.1 G3 |

**Headline rule.** The README and launch-post copy below quote ~40% because the v2.0 narrative anchors on the `/revue-local` workflow shift — the audience for that surface is a customer deciding whether to install the skill. The pricing-page TCO table (§9.3) is where the ~79–88% model-swap figure lives because the audience there is comparing pricing tiers, not deciding to install. The two figures are additive in practice — a customer who switches model *and* installs `/revue-local` saves both deltas.

### 9.1 README (first 10 lines, above the fold)

```markdown
# Revue

Revue catches bugs in AI-written code before they cost you another round of
AI inference.

The pattern: your AI agent writes code → it asks `/revue-local` for a
multi-agent review → it fixes what it finds → it commits. Issues never
reach CI, so CI never re-pays for them.

Typical saving: ~40% lower AI inference spend versus running CI-only
review. The skill itself runs inside your existing Claude Code, Cursor,
or Windsurf session — no Revue-side per-call billing.
```

### 9.2 Website hero (`revue.sh`)

```
Stop paying twice for the same AI bug.

Revue plugs into your AI-coding agent before it commits. Bugs caught here
never trigger a CI review cycle — and your AI bill stops growing in line
with your PR volume.

[ Install in 60 seconds ]   [ See the saving model ]

Works with Claude Code, Cursor, and Windsurf.
Bring your own AI provider. We never see your code.
```

### 9.3 Pricing page header

```markdown
## Pricing that lowers your total AI bill

Revue is the only review tool priced *against* your AI spend, not
on top of it.

- You pay Revue for orchestration, not for AI inference (BYOK).
- Default model is DeepSeek-V4-Pro on OpenRouter — about 10× cheaper
  per review than Anthropic Sonnet 4.5, with no measurable quality
  regression on our supported-tier protocol.
- `/revue-local` runs inside your existing Claude Code subscription;
  there is no Revue-side per-call cost.
- Every issue Revue catches before commit is one fewer CI review cycle
  billed against your AI provider.

The table below shows total cost of ownership (Revue + AI inference) at
each tier, comparing the default DeepSeek model against the Anthropic
Sonnet baseline.

[ TCO table — pulled from PRD §11.2 ]
```

### 9.4 Launch post — top of post

```markdown
# Revue 2.0 — the AI review layer that lowers your AI bill

AI coding tools write code 5–10× faster, with 2× the bug rate. The
industry response so far has been to bolt a second AI on top — a code
reviewer that runs in CI and pays for inference, again, to find the
mistakes the first AI made.

We think this is the wrong architecture. The cheapest place to catch
an AI-written bug is inside the same AI session that wrote it, before
the code is committed.

Revue 2.0 ships `/revue-local`: a skill your AI agent invokes against
its own working diff, before commit. Same multi-agent review, same
quality, but running inside your existing Claude Code (or Cursor,
or Windsurf) subscription. Zero Revue-side inference cost.

Customers running `/revue-local` see ~40% lower AI inference spend
versus running our CI-only product. The mechanism is simple: fewer
review cycles billed.

[ Install command + link to brief / docs ]
```

### 9.5 Docs site index page

```markdown
# Revue documentation

Revue is built on one premise: your AI bill should not grow faster
than your team. Every Revue product surface is designed to reduce
AI inference cost without giving up review quality.

Start here:

- **Install `/revue-local`** — the skill your AI agent invokes before
  it commits. Zero Revue-side per-call cost; lowers your CI-side AI
  spend by ~40% on average.
- **The 4-layer model** — where Revue sits in your AI-coding workflow,
  and why catching issues here costs less than catching them later.
- **Configuration (`.revue.yml`)** — one config, two execution surfaces
  (`/revue-local` and CI). Switch models without touching code.
```

### 9.6 In-product copy summary

| Surface | Lead with | Example |
|---------|-----------|---------|
| Installer first-run | The saving claim with a $0.00 starting figure | "Estimated saving this month so far: $0.00 — run your first review to start" |
| CLI footer below findings | This commit's saving + month-to-date | "Estimated saving on this commit: $0.07. Month-to-date: $4.23 across 61 reviews." |
| Paywall message | Saving already delivered + upgrade rationale | "You've saved $32 this month — Indie pays for itself at $9/mo. Upgrade?" |
| Web dashboard headline | Monthly saving in dollars, large | "$237 saved this month across 1,418 reviews" |

The dollar figures above are illustrative; the dashboard uses the customer's actual metadata.

### 9.7 What not to ship

Three messaging anti-patterns to actively reject in draft copy reviews:

- Leading with technical capability ("multi-agent specialised review with verifier and consolidator…") before the customer-cost outcome.
- Comparing Revue to other code-review tools on review quality alone — the cost pairing is what makes the positioning differentiated.
- Treating `/revue-local` as a *developer convenience* feature ("review locally!") instead of a *cost-control* feature ("catch it before CI charges you again").

---

## 10. Phased Plan

Three sub-phases inside the PRD's Phase 2. Each sub-phase has scope, exit criteria, dependencies on existing tickets, and a rough effort estimate.

### 10.1 Phase 2.a — Minimum Viable Distribution

**Scope.**

- Skill bundle published at `github.com/revue-io/revue-local` (public).
- Signed release artefacts (Sigstore/cosign) and a published version manifest at `revue.sh/skills/manifest.json` (per §6.1 anti-piracy mix).
- One-command installer at `revue.sh/install` (Claude Code path only).
- Licence JWT issuance from `revue.sh/activate` browser flow.
- Server-side daily-validate, usage-emit, and free-tier counting endpoints (`/api/v2/licence/validate`, `/api/v2/usage/emit`).
- CLI cost-saving footer (printed below findings every invocation).
- `revue.sh/skills` index page with copy-paste install command.
- Cost-care messaging rolled to README + website hero + pricing-page header (§9.1, §9.2, §9.3).

**Exit criteria.**

- End-to-end install on a clean Claude Code workstation in under 5 minutes (G4 target).
- A user with a fresh install can run `/revue-local` on a real diff and see findings + a cost-saving footer in one Claude Code session.
- 25-review free-tier cap is enforced end-to-end (verified against a deliberately-exhausted test workspace).
- Daily-check + 24h-cache behaviour verified: first invocation hits the server, subsequent invocations within 24h skip the round-trip, simulated network failure inside the 24h window still allows reviews, simulated >24h offline blocks reviews with the documented error message.

**Dependencies on existing tickets.**

- Reuses Phase 1 CI pipeline (PRD §12) for agent prompts and finding rendering. Agent prompts ship in the skill bundle (they are not server-delivered per session — that mechanism was considered and dropped, see §6.1).
- Reuses Phase 1 licence-key endpoint (`POST /api/license/validate`) as the basis for `POST /api/v2/licence/validate`; the v2 endpoint adds the daily-check + cache contract specified in §6.3.
- Reuses `.revue.yml` schema (PRD §8.1) unchanged.

**Rough effort.** 3–5 engineer-weeks. Largest items are the signed-release pipeline and the licence-validate / usage-emit endpoints with the daily-cache semantics.

### 10.2 Phase 2.b — Polish

**Scope.**

- Cursor installer path (write `.cursor/rules/revue-local.mdc`).
- Windsurf installer path (write `windsurfrules`).
- `revue-local doctor` diagnostic command.
- Web dashboard at `revue.sh/dashboard` — monthly aggregate, sparkline, share-link generator.
- Launch post and ongoing-content rollout (§9.4).
- Refinement of the saving-calculation middle term (replace `1.0` default with telemetry-derived probability if data warrants).
- Soft-paywall UX inside the skill (the upgrade prompt copy in §9.6).

**Exit criteria.**

- Wire-up rate (G2) reaches ≥ 60% for new installs in the trailing 30 days.
- Web dashboard receives ≥ 100 unique workspace visits per week.
- Phase 2.a + 2.b cumulative installs ≥ 200 (early signal toward the 500 G1 target).

**Rough effort.** 3–4 engineer-weeks. Cursor and Windsurf detection logic is straightforward; the dashboard front-end is the largest item.

### 10.3 Phase 2.c — Scale

**Scope.**

- Anthropic skill registry submission (downstream syndication).
- Optional read-only OpenRouter / Anthropic billing-API connection for grounded saving figures.
- Free → Indie conversion experiments (CTA copy, paywall timing).
- Self-hosted telemetry sink for Enterprise customers.
- Multi-licence-per-team handling tightened (per-seat workspace dashboard).

**Exit criteria.**

- Cumulative installs ≥ 500 (G1).
- Customer AI-spend reduction (G3) measurable at ≥ 40% for active users with billing-API connection.
- Free → Indie conversion (G5) ≥ 7% within 90 days for the Phase 2.c install cohort.

**Rough effort.** 4–6 engineer-weeks, plus marketing investment proportional to the conversion target.

### 10.4 Sequencing notes

- Phase 2.a and 2.b can overlap if the team can run two parallel tracks (one on the installer paths, one on the dashboard). The licence/paywall plumbing is on the critical path for 2.a and must finish first.
- Phase 2.c is gated on Anthropic registry maturity and customer demand for billing-API integration — both external dependencies. Phase 2.b can ship without 2.c indefinitely.

---

## 11. Risks & Open Questions

### 11.1 Risk: skill discoverability is hard to bootstrap

GitHub stars + a Revue-hosted index do not guarantee discovery. The most likely failure mode is "we shipped Phase 2.a and nobody installed it." Mitigations:

- Launch post on Hacker News + practitioner newsletters timed for Phase 2.a.
- Awesome-X list submissions (Awesome Claude Code, Awesome Cursor Rules) — low-cost, durable surface area.
- Partnerships with AI-coding-tool vendors (Anthropic, Cursor, Codeium) for blog cross-posts.
- Treat the cost-saving dashboard as a referrable artefact ("$237 saved this month — share with your team").

**Trigger to re-plan:** if installs at week 12 post-launch are below 100, prioritise Phase 2.c marketing investment over Phase 2.c feature work.

### 11.2 Risk: Claude Code session quota collisions

A long `/revue-local` invocation consumes the customer's Claude Code session budget. For users on Anthropic's lower-tier Claude Code plans, a multi-agent review may exhaust their hourly quota. Mitigations:

- Document the session-budget cost honestly in `revue.sh/skills` (estimated message-count per review).
- Provide a `--lite` flag that runs Maya-only for fast iterations.
- Surface the customer's quota status if Claude Code exposes it; otherwise print a duration estimate before starting long reviews.

### 11.3 Risk: cross-AI-agent compatibility drift

Claude Code, Cursor, and Windsurf evolve their skill / rules interfaces on their own schedules. Mitigations:

- Per-AI-tool integration tests in CI, running weekly against the latest stable host versions.
- Versioned `revue-local` releases; the installer pins the matching host-tool integration version.
- A `revue-local doctor` command that detects host-tool API drift and prints actionable upgrade instructions.

### 11.4 Risk: pirated forks circulate

Per §6.1, the threat is somebody forking the public repo, stripping the licence-key check, and redistributing a paywall-free clone. The anti-piracy mix (active check, vendor-controlled installer / curated index, signed releases, version manifest) raises the friction; it does not eliminate it.

**Mitigations** if the risk becomes material (evidence: low conversion rate inconsistent with install telemetry, or direct sightings of unofficial forks gaining traction):

- Tighten the version-manifest signalling: make `revue-local --version` surface a prominent "unofficial build" banner when the running SHA does not match the published manifest.
- Use the installer-served channels (`revue.sh/install`, `revue.sh/skills`) as the canonical SEO surfaces, so the official version dominates search rankings against any fork.
- Reach out to the AI-coding-tool vendors (Anthropic, Cursor, Codeium) to delist known infringing forks from their respective discovery surfaces.

None of these are built in Phase 2 beyond the baseline manifest and signed releases. Heavier mitigations wait on evidence of material revenue impact.

### 11.5 Risk: customers wire `/revue-local` and never actually use it

Install-and-forget is the silent failure mode. G2 (wire-up rate) is the metric that catches it. Mitigation:

- The first-run experience (§5.4) leads with a concrete "ask your agent to run `/revue-local` now" prompt.
- A two-week post-install email surfaces the customer's $0 saving and asks if onboarding stalled.
- Inside the Claude Code wiring snippet (§8.1), the language is imperative ("Before staging a commit, invoke `/revue-local`") not suggestive.

### 11.6 Risk: multi-licence-per-team confusion

A team buys an Indie tier (which is per-seat), each developer activates their own licence, and confusion arises about "team-wide" usage. Mitigation:

- The workspace dashboard aggregates by `workspace_id`, not by licence.
- The installer prompts "Is this licence for personal or team use?" — team selection guides the user to the per-seat purchase flow.
- Phase 2.c tightens this with explicit per-seat workspace UI.

### 11.7 Open questions for Phase 2 review

1. **Saving-calculation middle term default.** Is `1.0` (every locally-caught finding would have triggered a CI cycle) the right default, or should we ship with `0.7` to err conservative? **Recommendation:** ship `1.0` in Phase 2.a (matches customer's instinct), tune in 2.b with real data.
2. **Installer telemetry on install failures.** Should the installer phone home on failure to help us debug? **Recommendation:** opt-in only, prompted at install time. Default off — protect customer trust.
3. **Skill versioning policy.** Pinned vs auto-upgrading. **Recommendation:** auto-upgrade by default for patch versions, prompt-to-upgrade for minor versions, never auto-upgrade across major versions.
4. **Multi-host installation.** A user runs Claude Code at home and Cursor at work. Same licence? **Recommendation:** yes — licence is per-developer, not per-host. The per-licence concurrent-session cap (§6.3) handles abuse.

---

## 12. Success Metrics

Restated from §3.1 with the measurement plumbing now specified.

| # | Metric | Target | Measurement source |
|---|--------|--------|---------------------|
| G1 | Cumulative `/revue-local` installs | 500 by month 6 | Installer phone-home (one event per successful install) |
| G2 | Wire-up rate (installs that run a real review within 7 days) | ≥ 60% | First-invocation usage-emit flag set on first non-empty-diff invocation |
| G3 | Customer AI-spend reduction (`/revue-local` vs CI-only) | ≥ 40% | Spend-delta dashboard, per §7 |
| G4 | Time-to-first-review (install → first finding) | ≤ 5 minutes (75th percentile) | Installer install-timestamp + first-review timestamp from first-invocation usage-emit |
| G5 | Free → Indie conversion among `/revue-local` users | ≥ 7% within 90 days | Stripe + licence-key cohort analysis |
| G6 | Discoverability rank | Top-3 on `revue.sh/skills`, top-10 on Anthropic registry (when listed) | Manual quarterly check |
| G7 | Skill-driven NPS | ≥ 50 | In-skill prompt after the 10th invocation: "Recommend Revue to a colleague? (0–10)" |
| G8 | Paywall hit-to-upgrade ratio | ≥ 15% of Free-tier exhaustion events convert to Indie within 14 days | Stripe + paywall event correlation |

Targets are validated quarterly. If G3 (the cost-savings claim) is consistently below 30% at month 6, the positioning needs re-grounding — see §11.7 question 1.

---

## 13. Glossary

| Term | Definition |
|------|-----------|
| `/revue-local` | The Claude Code (and Cursor, Windsurf) skill the customer's AI-coding agent invokes inside its own workflow, before the AI commits code. Runs Revue's full multi-agent review pipeline (Cleo, Zara, Kai, Maya, Leo, Nova, Sage, Vex) in the customer's existing AI session, against the same `.revue.yml` the CI track uses. |
| Skill registry | A discovery + install surface for AI-agent skills. Three are in scope: Anthropic's first-party registry (when stable), Revue's curated index at `revue.sh/skills`, and GitHub as the primary distribution surface. |
| Customer-side execution context | Code that runs on the customer's machine, inside the customer's AI session, consuming the customer's AI subscription. The skill executes here; the Revue server does not. |
| AI-workflow integration | The act of instructing a customer's AI-coding agent (Claude Code, Cursor, Windsurf, etc.) — via `CLAUDE.md`, `.cursor/rules`, or `windsurfrules` — to invoke `/revue-local` at a specific point in its loop (pre-commit). |
| Pre-commit AI review | A multi-agent review of a staged-but-uncommitted diff, invoked by the AI agent that wrote the diff, before it commits. The Layer 2 product in the 4-layer model (PRD §2.2). Distinct from a git `pre-commit` hook, which runs in `.git/hooks/` outside the AI session. |
| BYOK | Bring Your Own Key. Customer provides their own AI provider API key (OpenRouter, Anthropic, OpenAI, Azure, custom). Revue never stores or sees it. For `/revue-local`, the "key" is the customer's existing Claude Code (or Cursor / Windsurf) subscription — no separate API key is needed for inference, only the Revue licence JWT. |
| Licence JWT | A signed JSON Web Token issued by `revue.sh/activate`. Encodes `tier`, `seat_id`, `workspace_id`, `agents_allowed`, `iat`, `exp`. Verified offline by the skill; revalidated online once per day against the Revue licence server (§6.3). |
| Daily licence check | The first `/revue-local` invocation of each 24h window calls `POST /api/v2/licence/validate`. On success the result is cached for 24h. After 24h without a successful check, the skill blocks usage. Same rules every tier (§6.3). |
| Cost-saving dashboard | The dual-surface (CLI footer + web aggregate) view of estimated customer AI-spend reduction from running `/revue-local`. Built entirely from already-collected licence + usage metadata; never ingests source code or diffs. |
| Wire-up | The act of writing `/revue-local` into the customer's AI agent's rules file (`CLAUDE.md`, `.cursor/rules/`, `windsurfrules`). The installer offers to do this; the customer can also do it manually. |
| Version manifest | A signed JSON document published at `revue.sh/skills/manifest.json` listing the canonical release SHA, signature, and version metadata for `/revue-local`. Used by `revue-local --version` and by the installer to verify the user is running an official build (§6.1 anti-piracy mix). |

---

## 14. Revision history

| Date | Pass | Summary |
|------|------|---------|
| 2026-05-17 | v1.0 — initial draft | Distribution recommendation (GitHub primary + revue.sh/skills + Anthropic syndication); licensing modelled as "untrusted skill" with session-start handshake, server-delivered agent prompts, Nuitka-compiled enforcement binary, tier-graded offline grace (Free/Indie/Pro online-only, Enterprise 72h). |
| 2026-05-18 | v1.1 — corrections | Two corrections applied: (1) "untrusted skill" framing dropped — Claude Code runs skills as instructed and the runtime-trust problem is not real; §6 reframed around anti-piracy / paywall-circumvention with an active licence check, vendor-controlled distribution channels, and signed releases + version manifest as the standard mix. (2) Licence validation simplified to a single daily online check + 24h cache with identical rules for every tier; the tier-graded offline grace and Enterprise 72h carve-out removed. Downstream references updated in §1 executive summary, §4.3, §5.6, §7.4, §7.6, §10.1, §11.4, §13. The server-delivered-prompts mechanism and the Nuitka enforcement-binary requirement are dropped from the build scope. |

---

*Sibling document: `docs/planning/prd.md` v2.0.*
*Memory references: `project_anthropic_deprioritisation`, `feedback_customer_cost_messaging`.*
