# REVUE-94 Before/After Comparison Evidence (AC4)

## Mechanism Verification

The pattern injection mechanism was verified through automated tests:

### Before (no patterns configured)

When `.revue.yml` has no `allowed_patterns`, the agent system prompt contains
only the base agent instructions. All four known false positives would be
flagged:

1. `_def attribute access on LoadedAgent` — flagged as accessing private attribute
2. `Inline lazy httpx import in pr_description_adapter` — flagged as non-standard import
3. `test_vcs_adapter.py deletion` — flagged as removing test coverage
4. `Bare except in _inject_pr_context` — flagged as overly broad exception handling

### After (four patterns configured)

With the four `allowed_patterns` entries in `.revue.yml`, each agent's system
prompt is prepended with:

```
## Allowed Patterns — Do Not Flag
The following patterns represent intentional design decisions. Do NOT report findings for these:
- _def attribute access on LoadedAgent — Internal implementation detail, no public API
- Inline lazy httpx import in pr_description_adapter — Intentional lazy loading pattern, now replaced with module-level import
- test_vcs_adapter.py deletion — Test coverage consolidated into test_vcs_adapters.py
- Bare except in _inject_pr_context — Intentional catch-all, PR context injection must not crash the review loop
```

### Test Evidence

| Test | Result | What it proves |
|------|--------|----------------|
| `test_yaml_parser_reads_allowed_patterns` | PASS | Parser correctly loads 4 patterns from YAML |
| `test_allowed_patterns_injected_into_system_prompt` | PASS | Patterns appear in agent prompt before LLM call |
| `test_empty_patterns_no_injection` | PASS | No injection when patterns absent (backward compat) |
| `test_revue_yml_contains_four_allowed_patterns` | PASS | Project config has all 4 FP patterns |
| `test_comparison_run_fp_reduction` | PASS | Mock-based: findings matching allowed patterns are absent when patterns configured |

### Live Run

A live comparison run requires API credentials and is tracked separately.
The mechanism is verified: patterns are injected into every agent's system prompt
before the first LLM call, instructing the model to skip findings for those patterns.
