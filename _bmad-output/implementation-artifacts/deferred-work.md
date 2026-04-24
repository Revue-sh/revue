# Deferred Work

Items surfaced during review but not caused by the current story. Collect here for future focused attention.

---

## Deferred from: code review of revue-170-ai-assisted-routing (2026-04-23)

- **W1** — `_agent_matches_ai_suggestion` false-positive risk on short canonical names (e.g. "leo" matches "paleontologist"); deliberate design, low practical risk with structured LLM output but worth revisiting if custom agents are added.
- **W2** — `record_routing()` not atomic with routing decision: exception between `route()` and `record_routing()` leaves metrics unrecorded. Window is a single print statement; non-blocking.
- **W3** — `routing_source` field in `RoutingMetricsData` is a plain `str` with no enum constraint; a typed `Literal["ai_assisted", "algorithm_fallback"]` or `StrEnum` would prevent invalid values silently persisting to JSONL.
- **W4** — `test_ac6_state5_shared_none_falls_back_to_algorithm` uses `@pytest.mark.parametrize` with a single case; implies unfulfilled intent to parametrise all AC4 bail-out conditions in one test.

---

## Deferred from REVUE-134 (2026-04-13)

### D1 — Feature flags have no env-var override path
`src/revue/core/ai_config.py` / `from_env()`. No feature flag (`preserve_comment_threads`, `show_reviewed_files`) is settable via an environment variable, so CI pipelines cannot toggle them without a `.revue.yml` file present. Pattern would be e.g. `REVUE_SHOW_REVIEWED_FILES=false`. Pre-existing gap, not introduced by REVUE-134.

### D2 — `rr.file_path` can be `None` or empty string
`src/revue/cli.py` / reviewed-files dedup block. If a `ReviewResult` has `file_path=None` and a truthy `response`, it passes the `not rr.error and rr.response` filter and renders as `` `None` `` in the published comment. Pre-existing issue (old code had the same behaviour). Should be guarded with `if rr.file_path`.
