# Session Handoff - 2026-05-17
**Duration:** ~10h GMT | **Agent:** Claude Opus 4.7 (1M context)

## Session Summary

Closed Phase A (per-model registry: REVUE-262/263/264/265) and Phase B (`/revue-local` parity carryover: REVUE-259/260/261) - seven Bitbucket PRs (#149-#155) all merged to main in a single working window. Filed REVUE-266 as the natural post-promotion follow-up (DeepSeek production A/B dogfood), then revised it with eight readiness gaps closed after a party-mode review (Quinn/Winston/John). Formal Jira issue-link relationships wired for REVUE-266 lineage; standing rule for that saved to memory.

## Project Status

| Metric | Value |
|--------|-------|
| Tickets shipped this session | 7 (REVUE-259/260/261/262/263/264/265) |
| Tests passing | 1789/1789 on main |
| Main HEAD | `5c4bb02` |
| Open PRs (this session) | 0 - all merged |
| Open PRs (stale, pre-session) | #114, #120 - "Revue lessons learned" chore branches |
| Tickets filed | REVUE-266 (To Do, ready to pick post-2026-06-01) |

## Completed this session

- **REVUE-259** (commit `1e093b6`, PR #149) - three-state envelope enforcement in `/revue-local`
- **REVUE-260** (commit `08a0d1f`, PR #150) - reviewer-tools constraint + soft out-of-diff audit (cherry-picked from stale GitHub PR onto fresh branch off main; ran `/bmad-code-review` before merge)
- **REVUE-261** (commit `a59aa83`, PR #151) - Vex in-loop via split Phase 3 (cherry-picked from stale GitHub PR; two `/bmad-code-review` passes closed 18 findings before merge)
- **REVUE-265** (commit `db0c810`, PR #152) - DeepSeek-V4-Pro spike with PROMOTE recommendation; harness at `scripts/smoke_openrouter_deepseek_test.py`, evaluation doc at `docs/research/deepseek-v4-pro-evaluation.md`
- **REVUE-262** (commit `5d177b6`, PR #153) - per-model registry + dispatcher gate; `ModelConfig` dataclass, `models_registry.yml` with 4 entries (Sonnet, Haiku, Qwen, DeepSeek - DeepSeek added per REVUE-265 PROMOTE)
- **REVUE-264** (commit `305bb94`, PR #154) - `revue list-models` CLI (human/JSON/Markdown), `docs/configuration/per-model-knobs.md`, README "Supported Models" section
- **REVUE-263** (commit `5c4bb02`, PR #155) - per-model knobs applied in `ai_client.py` + `tool_loop.py`; `tool_choice_first_turn` and `max_tokens_default` flow from registry
- **REVUE-266** filed (Task, epic REVUE-87, label `mvp`) - DeepSeek production A/B dogfood; revised with 8 gap fixes
- Formal Jira issue-links wired for REVUE-266: `Relates` to REVUE-265/262/263/264/241

## What We Built (Session Highlights)

### Phase A - Per-model registry (REVUE-262/263/264/265)

Data-driven model dispatch replaces what would otherwise have become a provider if/elif chain. `src/revue/core/models_registry.yml` is the source of truth; `_revue/models_registry.yml` is a symlink so the `/revue-local` skill reads the same file. `ModelConfig` is a frozen dataclass with `MappingProxyType` extras for forward-compat. The dispatcher gate validates both `ai_config.model` and `ai_config.synthesis_model` at config-load time - runs unconditionally, no opt-in. Tier `supported` entries are hard-gated on `schema_strict: true`; customer-added `tier: unsupported` entries pass silently.

`tool_choice_first_turn` ("auto" / "required") closes the Qwen/DeepSeek auto-skip gap without affecting Anthropic's loop. Anthropic path untouched.

CLI surface: `revue list-models` prints both built-in registry AND user overrides (with inline annotation), with `--json` and `--markdown` flags.

### Phase B - `/revue-local` parity (REVUE-259/260/261)

`/revue-local` Mode 2 now produces output qualitatively equivalent to the production review pipeline at zero Anthropic spend.
- Three-state envelope contract enforced via `_classify_agent_output()` wrapping `classify_terminal_state` (REVUE-259).
- Phase 1 prompt injects an explicit reviewer-tools constraint; Phase 3 audits out-of-diff file references and warns via stderr (REVUE-260).
- Vex verdicts + post-processor chain run via subprocess Python (3a + 3c); LLM step externalised to orchestrator Agent forks (3b). Byte-equivalence test pins Vex prompt to production `_DEFAULT_SYSTEM_PROMPT`. OCP hook in `pipeline.py:build_consolidation_postprocessors()` means a future REVUE-245 grounding filter is auto-surfaced as a divergence (REVUE-261).

### REVUE-266 - DeepSeek production A/B follow-up

Filed and sharpened. Registry/docs promotion already shipped in REVUE-262/264; this ticket closes the explicit out-of-scope items from REVUE-265 (production A/B with real users, comparison vs Qwen/Sonnet on Revue's internal corpus). Revised description now contains:
- Five `(PR, parent_sha, head_sha)` corpus triples pinned
- `git checkout <parent_sha>` replay procedure
- Bash `trap` wrapper for drop counting (no Revue code changes)
- Six-metric extraction approach from stdout/stderr/usage_tracker
- Four-label TP/FP/HC/HW triage rubric with blind-triage discipline
- Quantitative DEFAULT/ALTERNATIVE/DEMOTE thresholds in AC5
- One-calendar-week time cap post-2026-06-01
- ~10-11 hour effort estimate flagged

## Remaining Work - Next Steps

1. **REVUE-266 execution** - blocked on Anthropic spend-cap recovery (2026-06-01) so the Sonnet baseline runs are funded. First action when picked up: `git checkout 0324bd66` (REVUE-259 parent SHA) and replay with `claude-sonnet-4-5-20250929` configured in `.revue.yml`. See ticket's `Replay Procedure` section.
2. **Stale chore PRs #114 + #120** - "Revue lessons learned from PR #X" chore branches sitting open from earlier sessions. Triage decision needed: merge, close, or update. Not blocking.
3. **Plan artifact** - `/Users/langostin/.claude/plans/composed-humming-clock.md` should be marked complete (Phase A + B both done). Not a code change; housekeeping only.

## Key Architectural Decisions (Session)

1. **Hard gate on schema_strict for `tier: supported`** - all built-in registry entries must support strict JSON schema. Customer-added `tier: unsupported` entries pass silently. Rationale: prevents shipping a "supported" model that drops findings under contract violations.
2. **`tool_choice_first_turn` as a per-model knob, not a per-call argument** - Qwen/DeepSeek need `required` on turn 1; Anthropic's loop never touches it. Encoded in the registry so dispatch is data-driven; closes the OCP gap (no provider if/elif).
3. **`/revue-local` Phase 3 split into 3a/3b/3c** - LLM work externalised to orchestrator Agent forks. Subprocess Python builds prompts (3a) and applies verdicts (3c); orchestrator runs the LLM step (3b) at zero Anthropic spend. Byte-equivalence test against production `_DEFAULT_SYSTEM_PROMPT` is the contract anchor.
4. **`build_consolidation_postprocessors()` registry hook in `pipeline.py`** - closes OCP gap; future post-processors (REVUE-245 grounding filter) auto-divergence-checked by local skill.
5. **DeepSeek added to registry as `tier: supported` in REVUE-262** based on REVUE-265's empirical PROMOTE recommendation (not deferred to a follow-up). Production A/B is REVUE-266 - the registry promotion is the cheap part; the production verdict is the expensive part.
6. **Formal Jira issue-link relationships are mandatory** - textual Dependencies section is not enough. Saved as `feedback_jira_formal_links.md`. POST `/rest/api/2/issueLink` for every referenced ticket; prefer `Relates` over `is blocked by` when blockers are already Done.

## Session Stats
- Duration: ~10h
- Stories: 7 merged (REVUE-259/260/261/262/263/264/265), 1 filed (REVUE-266)
- Commits to main: 7 (squash merges, all via `/bitbucket-merge-pr`)
- Tests: 1789 passing (1751 baseline + 38 new across the 7 tickets)
- PRs opened+merged: #149, #150, #151, #152, #153, #154, #155
- Jira tickets created: REVUE-262, REVUE-263, REVUE-264, REVUE-265, REVUE-266
- Memory rules saved: `feedback_jira_formal_links.md`
- Party mode agents used: Quinn (QA), Winston (Architect), John (PM), Paige (Tech Writer)

## Continuation Prompt (Next Session)

```
Read docs/team/HANDOFF.md first. The 7-ticket per-model registry + /revue-local parity work
is fully merged to main (HEAD 5c4bb02, 1789 tests). REVUE-266 (DeepSeek production A/B
dogfood) is filed with 8-gap-closed acceptance criteria; ready to pick when Anthropic
spend cap resets on 2026-06-01. First execution step is `git checkout 0324bd66` then
replay with claude-sonnet-4-5-20250929 - see REVUE-266 "Replay Procedure" section.
Stale chore PRs #114 + #120 need triage. Plan at /Users/langostin/.claude/plans/
composed-humming-clock.md needs marking complete.
```
