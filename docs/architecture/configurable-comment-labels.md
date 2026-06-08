# Configurable Comment Vocabulary

**Status:** Proposed
**Updated:** 2026-06-07
**Tracking:** REVUE-422 documents the reconciled proposal; implementation requires a separate Jira ticket.

---

## Context

Revue comments already expose an actionability signal independently of severity:

| Condition | Current label |
|---|---|
| Informational severity | `Note` |
| Finding has a `code_replacement` | `Action` |
| Other actionable finding | `Suggest` |

`BodyBuilder` derives these labels deterministically. Agents do not choose an action
class, and Nova does not escalate one. This is intentionally different from the legacy
proposal, which added an AI-produced `action` field and duplicated actionability logic
across agent prompts, synthesis, models, and rendering.

The remaining product question is narrower: should a team be able to change the displayed
words while preserving Revue's deterministic classification?

---

## Proposed Decision

Allow display-only overrides in `.revue.yml`:

```yaml
review_vocabulary:
  action: "Action"
  suggest: "Suggest"
  note: "Note"
```

Each key is optional. Missing, empty, or whitespace-only values fall back to the defaults.
The semantic keys remain fixed as `action`, `suggest`, and `note`; configured strings are
presentation only.

Examples:

```yaml
# Regulated team
review_vocabulary:
  action: "Required"
  suggest: "Recommended"
  note: "Informational"
```

```yaml
# RFC vocabulary
review_vocabulary:
  action: "Must"
  suggest: "Should"
  note: "May"
```

The existing emoji and classification rules remain unchanged:

- `info` severity renders the configured `note` label.
- A non-info finding with `code_replacement` renders the configured `action` label.
- Any other finding renders the configured `suggest` label.

The labels must be resolved by configuration loading and injected into `BodyBuilder`.
Rendering code must not read `.revue.yml` directly.

---

## Rationale

Severity answers how serious a finding is. The vocabulary label answers what the author
can do next. Keeping classification deterministic makes the behavior auditable, while
display-only overrides let teams use familiar review language without changing finding
semantics.

This preserves the useful part of the April 2026 UX proposal while rejecting its obsolete
parts:

- No agent-generated `action` field.
- No Nova action-escalation rule.
- No second actionability model alongside `code_replacement`.
- No return to the retired `cli.py` comment formatter.

---

## Compatibility

- Existing projects without `review_vocabulary` render exactly as they do today.
- Existing serialized findings do not change.
- The proposal does not alter comment fingerprints, severity, positioning, attribution,
  suggestion fences, or posting behavior.
- Non-English labels are technically possible because values are display strings, but
  built-in localization is out of scope.

---

## Implementation Boundary

This ADR is documentation only. A future implementation ticket must define:

- The typed configuration model and parsing rules.
- Constructor injection into `BodyBuilder`.
- Length and control-character validation for platform-safe rendering.
- Unit coverage for defaults, partial overrides, invalid values, singleton comments, and
  grouped comments.
- Updates to the `.revue.yml` reference.

Until that ticket is accepted and merged, `Action`, `Suggest`, and `Note` remain fixed.

---

## Reconciliation Record

REVUE-422 reviewed every artifact from the former `feat/ux-design-comment-format` branch:

| Legacy artifact | Disposition |
|---|---|
| Configurable comment labels | Rewritten here against current `BodyBuilder` behavior |
| Platform-native suggestion blocks | Superseded by `code_replacement` and the platform formatter registry; rationale retained in `comment-posting.md` |
| Nova synthesis mode | Superseded by the typed Consolidator and Nova synthesis architecture; rationale retained in `consolidation.md` |
| Design-system and SVG-token documents | Superseded by the active `codex/brand-guidelines` worktree |
| `resources/icon.svg` | Dropped; branding assets are owned by the brand-guidelines work |
| `_BRAND_FOOTER` constant | Dropped; it was unused and belonged to the retired formatter path |
