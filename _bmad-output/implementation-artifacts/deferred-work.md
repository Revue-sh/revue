# Deferred Work

Items surfaced during review but not caused by the current story. Collect here for future focused attention.

---

## Deferred from: code review of revue-170-ai-assisted-routing (2026-04-23)

- **W1** — `_agent_matches_ai_suggestion` false-positive risk on short canonical names (e.g. "leo" matches "paleontologist"); deliberate design, low practical risk with structured LLM output but worth revisiting if custom agents are added.
- **W2** — `record_routing()` not atomic with routing decision: exception between `route()` and `record_routing()` leaves metrics unrecorded. Window is a single print statement; non-blocking.
- **W3** — `routing_source` field in `RoutingMetricsData` is a plain `str` with no enum constraint; a typed `Literal["ai_assisted", "algorithm_fallback"]` or `StrEnum` would prevent invalid values silently persisting to JSONL.
- **W4** — `test_ac6_state5_shared_none_falls_back_to_algorithm` uses `@pytest.mark.parametrize` with a single case; implies unfulfilled intent to parametrise all AC4 bail-out conditions in one test.

---

## Deferred from: code review of revue-179-nova-contradiction-synthesis (2026-04-27)

- **W1** — AI exception path makes metrics ambiguous: `synthesised_count=0` is indistinguishable from "no contradictions found" vs "synthesis failed". Would need an explicit `synthesis_failed` flag on `ConsolidationResult` or a separate metrics event type to distinguish. Post-MVP observability.
- **W2** — Synthesis data silently discarded when `flush()` has no token events: `self._synthesis = None` is cleared before the `if not self.events: return` guard in `metrics_writer.py`, so a run that produces synthesis but no token events (e.g. mocked client in some test paths) loses the synthesis record. Inherited flush design; low practical risk in production.
- **W3** — `_build_synthesis_prompt` docstring says "TOML-encoded" but produces non-standard structured text (`[[` prefix, `key = "value"` syntax). Misleading label; LLMs handle it correctly in practice. Rename docstring and clarify format post-MVP.
- **W4** — `synthesised_from` carries `list[tuple[str, str]]` but serialises to `list[list[str]]` through JSON round-trip. Currently handled correctly by `(c[0], c[1])` repacking in cli.py:481. Type annotation is a lie across the service boundary; add a `to_dict()` / `from_dict()` normalisation method on `AIReview` post-MVP.
- **W5** — `group_by_key` dict comprehension in `_synthesise_contradictions` silently overwrites if two contradiction groups share the same `(file, line)` key. Cannot occur under current `_detect_contradiction_groups` call path; add an assertion guard when refactoring dedup_consolidator.
- **W6** — Old `.revue/` store entries persisted with hardcoded `"bitbucket"` platform key (pre-REVUE-179) will not match lookups using the correct platform string. Won't-fix decisions made before the upgrade will reappear as unresolved. Add a one-time migration utility or store-version check post-MVP.
- **W7** — No page limit on `fetch_review_thread_ids` pagination loop. On very large PRs (500+ review threads, many bot reviewers), this makes unbounded sequential GraphQL calls with no progress logging. Add `max_pages` guard and per-page log line post-MVP.
- **W8** — No user-visible warning when synthesis silently falls back to original findings (LLM returns non-JSON). Users see normal output with no indication synthesis was attempted and failed. Add a `print("[revue] ⚠ Synthesis failed — keeping original findings")` warning on fallback.

---

## Deferred from REVUE-134 (2026-04-13)

### D1 — Feature flags have no env-var override path
`src/revue/core/ai_config.py` / `from_env()`. No feature flag (`preserve_comment_threads`, `show_reviewed_files`) is settable via an environment variable, so CI pipelines cannot toggle them without a `.revue.yml` file present. Pattern would be e.g. `REVUE_SHOW_REVIEWED_FILES=false`. Pre-existing gap, not introduced by REVUE-134.

---

## Deferred from: code review of REVUE-185 (2026-04-29)

- **W1** — No boundary test for `min_confidence=1.0`: synthesised finding inherits `confidence=max(group)`; if all contributors are below 1.0 the synthesised result silently drops. Pre-existing test coverage gap for this boundary.
- **W2** — Synthesis tests call `consolidate()` without an explicit `strategies=` kwarg, relying on `_DEFAULT_STRATEGIES`. If default strategies change, these tests silently change behaviour without a test signal.
- **W3** — No negative test for `synthesised.agent_name` when mock response omits the field. Positive assertion (`== "nova"`) is sufficient for the fix but the absent-name path is untested.

---

### D2 — `rr.file_path` can be `None` or empty string
`src/revue/cli.py` / reviewed-files dedup block. If a `ReviewResult` has `file_path=None` and a truthy `response`, it passes the `not rr.error and rr.response` filter and renders as `` `None` `` in the published comment. Pre-existing issue (old code had the same behaviour). Should be guarded with `if rr.file_path`.
