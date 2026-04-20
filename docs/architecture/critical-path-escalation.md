# Critical Path Protection and Escalation Policy

**Status:** Proposed
**Updated:** 2026-04-20
**Jira:** REVUE-169

---

## Problem

Revue can block a merge when findings exceed a severity threshold. It cannot do anything more targeted than that.

Two related problems flow from this:

**Problem 1 — No critical path concept.**
Some areas of a codebase require more scrutiny than others regardless of finding severity: authentication, payments, session management, cryptography. A change to `src/auth/` that produces zero findings is not the same risk as a change to `src/utils/formatting.py` that produces zero findings. Revue treats them identically.

Teams compensate with manual process: "always get a senior to look at auth PRs." This is undocumented, inconsistently applied, and invisible to Revue.

**Problem 2 — No human escalation routing.**
When Revue does find a Critical severity issue, it posts a comment. The developer sees it and decides whether to fix it, dismiss it, or ignore it. There is no mechanism for Revue to say "this needs a specific person's sign-off before proceeding."

Blocking mode prevents the merge — but someone still has to manually assign the right reviewer. That assignment step is manual, forgettable, and not enforced.

The result: Revue is a passive commenter. It generates signal but does not close the loop between signal and human accountability.

Root causes:
1. No config surface for teams to declare which code areas are sensitive
2. Revue only writes to the PR comment thread — it does not interact with platform review workflows (assignments, approvals)
3. Escalation policy (who reviews what, under what conditions) lives in team culture, not in tooling

---

## Decision

### D1 — Critical Path Registry in `.revue.yml`

Teams declare sensitive code areas in `.revue.yml`. When a diff touches a declared path, Revue activates elevated review behaviour for that PR.

```yaml
critical_paths:
  - path: "src/auth/"
    label: "Authentication"
    reviewers: ["@tech-lead", "@security-eng"]
  - path: "src/payments/"
    label: "Payments"
    reviewers: ["@payments-lead"]
  - path: "src/core/models.py"
    label: "Domain Models"
    reviewers: []   # escalation comment only, no reviewer mention
```

**What "elevated review behaviour" means:**
- All findings from files in the critical path are promoted one severity level (Low → Medium, Medium → High, High → Critical) for blocking threshold evaluation only — the displayed severity is unchanged, but the blocking calculation uses the elevated value
- A top-level PR comment is posted by Revue before inline findings: "**Critical path touched:** `src/auth/` (Authentication). This area requires elevated scrutiny."
- If `reviewers` is non-empty, the comment @-mentions them

**Path matching:**
Prefix match — `src/auth/` matches any file under that directory. Glob patterns (`src/**/models.py`) are supported via Python's `fnmatch`. Matching is case-sensitive.

> **Implementation note**: Critical path detection runs in Cleo's pre-pass, before agents dispatch. The elevated severity is applied in Nova's consolidation step — agents receive the real severity in their prompts; Nova adjusts for blocking threshold evaluation only. This keeps agent prompts honest.

---

### D2 — Escalation Policy in `.revue.yml`

A declarative escalation stanza that maps Revue's finding conditions to actions. Evaluated after Nova consolidates findings.

```yaml
escalation:
  - condition: "critical_count >= 1"
    action: post_comment
    message: "Critical findings require resolution or explicit dismissal before merge."

  - condition: "critical_path_touched AND high_count >= 2"
    action: post_comment
    message: "High-severity findings in a critical path. Senior review recommended."
    mention: ["@tech-lead"]

  - condition: "critical_path_touched"
    action: post_comment
    message: "Change in {critical_path_label}. See critical path policy."
    mention: "{critical_path_reviewers}"
```

**Condition grammar (MVP):**
| Token | Meaning |
|-------|---------|
| `critical_count` | Number of Critical findings in this review |
| `high_count` | Number of High findings |
| `critical_path_touched` | Boolean — any declared critical path touched |
| `critical_path_label` | Name of the matched critical path |
| `critical_path_reviewers` | Reviewer list from the matched critical path config |

Conditions support `AND`, `OR`, `>=`, `<=`, `==`. No nested parens in MVP.

**Actions (MVP — comment-based only):**
| Action | Effect |
|--------|--------|
| `post_comment` | Posts a top-level PR comment with the configured message |
| `block_merge` | Sets the Revue status check to failed (same as existing blocking mode, but policy-triggered) |

**Why comment-based only for MVP:**
Platform review assignment APIs (GitHub `requested_reviewers`, GitLab `reviewers`) require additional OAuth scopes and platform-specific implementation. Comment-based escalation works identically across all platforms today. API-based assignment is deferred to Phase 2 (see Out of Scope).

**Policy evaluation order:**
All conditions are evaluated independently. Multiple escalation rules can fire on the same PR. Rules are evaluated in declaration order; the first `block_merge` action that fires ends evaluation for blocking (but comment actions continue).

> **Implementation note**: Condition evaluation happens in Nova's post-consolidation step. The evaluator is a small expression parser — do not use `eval()`. Implement as a simple token-walking parser against the grammar above.

---

### D3 — Nova Escalation Comment Format

All escalation comments posted by Revue follow a consistent, scannable structure.

```
---
**Revue — Escalation Notice**

**Reason:** Critical path touched: Authentication (`src/auth/`)
**Policy triggered:** `critical_path_touched AND high_count >= 2`
**Recommended action:** Senior review before merge.

cc: @tech-lead
---
```

Rules:
- Always includes the reason (human-readable, not the condition expression)
- Always includes the policy that triggered it (for auditability)
- Always includes the recommended action
- Posted as a top-level PR comment, not an inline finding
- Posted once per PR, not per finding. If multiple escalation rules fire, they are merged into a single escalation comment with multiple reason entries

---

## Out of scope

- **Platform review assignment API (Phase 2)**: Automatically assigning a reviewer via GitHub `requested_reviewers` or GitLab API requires platform-specific OAuth scopes and significantly more implementation work. Deferred. The comment + @-mention approach achieves 80% of the value with none of the platform-specific complexity.
- **AI-provenance-based escalation**: Whether code is AI-generated as a trigger condition is a separate RFC. This ADR is path-based, not authorship-based, and applies regardless of how the code was written.
- **Escalation audit log**: A persistent record of which escalation rules fired on which PRs is post-MVP (analytics dashboard, REVUE-87 epic).
- **Reviewer rotation / load balancing**: Selecting which reviewer from a pool to assign is out of scope. Teams configure a list; all are mentioned.
- **Escalation suppression / snooze**: Mechanisms for developers to dismiss escalation notices without fixing the underlying condition are post-MVP.

---

## Expected impact

| Metric | Current | After |
|--------|---------|-------|
| Critical path PRs with explicit escalation signal | 0% | 100% of PRs touching declared paths |
| Mean time for senior to be notified on critical-path PRs | Manual / inconsistent | Immediate (on PR open / Revue run) |
| Escalation policy violations (wrong reviewer, skipped review) | Undetectable | Detectable via PR audit trail |
| `.revue.yml` config surface | Existing keys | + `critical_paths`, `escalation` stanzas |

Impact is conditional on teams adopting the config. Zero config = zero change in behaviour.

---

## Affected files

| File | Change |
|------|--------|
| `src/revue/core/pipeline.py` | Cleo pre-pass: critical path detection against diff file list |
| `src/revue/core/models.py` | New fields: `CriticalPathMatch`, `EscalationResult` |
| `src/revue/core/cleo_router.py` | Critical path registry loading and matching |
| `src/revue/comments/service.py` | Escalation comment posting; D3 format; policy evaluation |
| `docs/revue-yml-reference.md` | New config stanzas: `critical_paths`, `escalation` |
| `docs/architecture/README.md` | ADR index update |

---

## Consequences

- **False-alarm fatigue**: If teams declare too many critical paths, every PR triggers escalation. The signal degrades. Mitigation: documentation should recommend ≤5 critical paths, scoped to genuinely high-risk areas. Revue should log a warning if more than 10 paths are declared.
- **Stale reviewer lists**: `reviewers` in `critical_paths` config are static. If the tech lead changes, the config is stale. This is a config maintenance burden, not a Revue failure. Mitigation: document clearly; consider a future `codeowners` integration that reads from CODEOWNERS file instead.
- **Comment noise**: Multiple escalation rules can produce a single escalation comment but multiple top-level comments if escalation fires on separate CI runs (e.g., re-run on push). Deduplication by run within a PR is required — escalation comments should be edited/updated, not duplicated. Existing comment threading logic in `comments/service.py` should handle this.
- **No enforcement without blocking**: `post_comment` without `block_merge` is advisory only. Developers can ignore it. This is intentional for MVP — teams opt into `block_merge` explicitly. The comment creates an audit trail even without enforcement.

---

## Review Notes

*Add name, date, and comment. Remove resolved items before moving to Accepted.*
