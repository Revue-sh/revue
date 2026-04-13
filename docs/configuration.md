# Configuration Reference

Revue.io is configured via a `.revue.yml` file in the project root. All keys are optional except `version`.

For the full schema reference, see [revue-yml-reference.md](revue-yml-reference.md).

## Star-Rating Formula (`rating`)

The `rating` section controls how findings translate into the 1–5 star score shown in every PR summary comment.

Each finding severity has a configurable weight. The score starts at 5.0 and each finding subtracts its penalty:

```
score = 5.0 − (high × w_high + medium × w_medium + low × w_low + info × w_info)
```

The result is clamped to `[floor, 5.0]`.

| Key | Default | Description |
|-----|---------|-------------|
| `weights.high` | `1.5` | Penalty per HIGH finding |
| `weights.medium` | `0.3` | Penalty per MEDIUM finding |
| `weights.low` | `0.05` | Penalty per LOW finding |
| `weights.info` | `0.0` | Penalty per INFO finding |
| `floor` | `1.0` | Minimum possible score |

`revue init` pre-fills this section with the defaults above. Adjust weights to match your team's standards — a strict team might raise `medium` to `1.0`, a lenient team might lower `high` to `0.5`.

For the full reference and team-specific examples, see [revue-yml-reference.md — rating](revue-yml-reference.md#rating).

---

## Pattern Configuration (`noise_filters`)

The `noise_filters` section controls false-positive suppression. Two pattern lists let you teach the reviewer about intentional design decisions:

### `allowed_patterns`

Patterns representing intentional design decisions that should **not** be flagged as findings. Each entry is injected into agent system prompts before every review.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `pattern` | string | yes | Natural-language description of the code pattern |
| `rationale` | string | yes | Why this pattern is intentional / acceptable |

### `disallowed_patterns`

Patterns that should **always** be flagged, regardless of confidence score. Useful for enforcing team standards (e.g., no TODO comments in production).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `pattern` | string | yes | Natural-language description of the pattern to flag |
| `rationale` | string | yes | Why this pattern should always be reported |

### Example

```yaml
noise_filters:
  disable: []
  low_confidence_threshold: 0.5
  allowed_patterns:
    - pattern: "_def attribute access on LoadedAgent"
      rationale: "Internal implementation detail, no public API"
    - pattern: "Inline lazy httpx import in pr_description_adapter"
      rationale: "Intentional lazy loading pattern, now replaced with module-level import"
    - pattern: "test_vcs_adapter.py deletion"
      rationale: "Test coverage consolidated into test_vcs_adapters.py"
    - pattern: "Bare except in _inject_pr_context"
      rationale: "Intentional catch-all, PR context injection must not crash the review loop"
  disallowed_patterns:
    - pattern: "TODO comments in production code"
      rationale: "TODOs should be tracked as Jira tickets"
```

### How it works

When the review pipeline initializes agents, it checks for configured patterns:

1. **Allowed patterns** are prepended to each agent's system prompt under a `## Allowed Patterns — Do Not Flag` section.
2. **Disallowed patterns** are prepended under a `## Disallowed Patterns — Always Flag` section.
3. If both lists are empty, no injection occurs and the agent runs with its default prompt.

Patterns are natural-language descriptions — not regex or glob matchers. The LLM agent interprets them contextually when generating findings.

### Validation

Invalid pattern entries produce clear errors at startup:

- Missing `pattern` key: `noise_filters.allowed_patterns[0] is missing required 'pattern' key`
- Non-string value: `noise_filters.allowed_patterns[0].pattern must be a string, got int`
- Existing configs without pattern keys continue to work (backward compatible).
