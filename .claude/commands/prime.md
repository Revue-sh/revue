# Revue — Session Primer

## Key references

- **Architecture diagram + agent roles**: `docs/planning/prd.md` §4.3 (Cleo → Zara/Kai/Maya/Leo → Nova → Comments)
- **Nova consolidation pipeline**: `docs/architecture/consolidation.md`
- **Post-MVP agentic loop**: `docs/architecture/agentic-review-loop.md`
- **Pipeline code**: `src/revue/core/pipeline.py`, `core/cleo_router.py`
- **Comment posting / threading**: `src/revue/comments/service.py`, `comments/platform_adapter.py`
- **VCS integration**: `src/revue/core/vcs_adapter.py`
- **Domain types**: `src/revue/core/models.py`
- **AI provider**: `src/revue/core/ai_client.py`
- **Config schema**: `docs/guides/revue-yml-reference.md`
- **Testing commands + conventions**: `docs/guides/testing.md`
- **Sprint context**: `docs/team/HANDOFF.md`

## Internal flags

**`REVUE_METRICS_ENABLED`** (default: off)

Enables `JsonlMetricsCollector`; writes per-run token usage to `.revue/metrics.jsonl`. Never document on
any public surface — see `docs/architecture/pipeline-metrics.md` ADR D6.
