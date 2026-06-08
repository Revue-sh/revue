# Deferred Work

Items surfaced during review but not caused by the current story. Collect here for future focused attention.

---

## Deferred from: code review of REVUE-341 (2026-06-08)

- **DF1 — Only Bitbucket CI env var checked for surface detection** `surface_defaults.py:27` — `BITBUCKET_BUILD_NUMBER` is the only CI indicator; GitHub Actions (`GITHUB_ACTIONS`) and GitLab CI (`GITLAB_CI`) fall through to the `cli` surface. Current numerical value is the same (600s) but if CI and CLI defaults diverge in future, misclassification will be silent.
- **DF2 — Unknown surface key names in `review.surface_defaults` not warned** `config_loader.py:208` — A user who writes `surface_defaults: { production: 300 }` will pass validation with no warning; the key is stored but never matched by `resolve_surface_timeout`. A validation pass over recognised surface names would improve UX.
- **DF3 — `surface_defaults` YAML values parsed without TypeError guard** `config_loader.py:211` — `{str(k): int(v) for k, v in sd.items()}` raises an unhandled `TypeError` if a value is YAML `null`. Consistent with the project's existing `int(review["field"])` pattern but slightly worse in a dict comprehension context.

---

## Deferred from: code review of revue-374-version-manifest-endpoint (2026-06-02)

- **Multi-platform wheel selection** — `manifest_builder._map_pypi_to_manifest` picks an arbitrary first `.whl`; revue publishes one wheel per platform (macOS arm64 + Linux x86_64). The schema exposes a single `wheel` slot and "multi-platform artefact representation" is explicitly out of scope for REVUE-374; the MVP `install-skill` client does version-equality only and never downloads from the manifest URL, so installs are not broken today. Revisit when the client consumes the artefact URL. **Now owned by REVUE-378** (Option A — per-platform manifest is the hard prerequisite for sha256 verification). [src/web/services/manifest_builder.py:114]
- **Cache stampede** — no async lock around the cache read-modify-write in `build_manifest`; concurrent requests on a cold/expired cache both fetch PyPI. Idempotent, last-write-wins, low severity at current traffic. **Ticketed: REVUE-379** (Low, related-to REVUE-374). [src/web/services/manifest_builder.py:65]
- **Yanked wheels not skipped** — a withdrawn release could be served as `current_version`. Low likelihood under the current single-maintainer release flow. **Ticketed: REVUE-380** (Low, related-to REVUE-374). [src/web/services/manifest_builder.py:114]

---

## Deferred from: code review of spec-revue-314-revue-sh-domain (2026-05-21)

- **D1** — `VALIDATE_URL` resolved at module import time in `packaging/revue_core/src/revue_core/core/license_validator.py:26`. Late env-var mutations are ignored. Pattern matches `usage_tracker._HOST` (project-wide convention); consider unifying on call-time resolution if any subsystem ever needs runtime overrides.
- **D2** — `importlib.reload` test pattern in `packaging/revue-ci/tests/core/test_license_validator.py:71-75` has no teardown — leaves the module in its last-reloaded state. Low risk today (all tests in the class reload before assertion); convert to a pytest fixture if the file grows.
- **D3** — RESOLVED in cycle 2 of REVUE-314: both `REVUE_VALIDATE_URL` (license validator) and `REVUE_APP_HOST` (usage tracker → TRACK_URL/UPGRADE_URL) are removed. The threat-model rule: env-var URL overrides are forbidden for endpoints Revue controls (license, usage, billing) because they create license-bypass / key-exfiltration surface; they remain allowed for endpoints the *operator* controls (LLM gateway, SCM). Do not reintroduce either env var without renegotiating that rule.
- **D4** — TDD audit-trail: REVUE-314 Commit 1 (`c1d7e2e`) co-commits the new test class and the `os.getenv` change rather than splitting into a red-then-green pair. Acceptance contract is met; the workflow audit-trail is not. Cosmetic; flag if a stricter TDD policy ever lands.

---

## Deferred from: code review of revue-237-revuelogger-named-channels (2026-05-08)

- **D1** — `Channel._log()` holds a reference to `inspect.currentframe()` without `del frame` in a `finally` block — creates reference cycles in CPython; GC handles it but PyPy/Jython runtimes may not. Add `del frame` / `del caller_frame` in finally.
- **D2** — `cli.py` retains `logging.basicConfig()` alongside the new channel system — duplicate configuration that will conflict once the remaining 11 AC8 `logging.getLogger()` call sites are migrated. Remove once AC8 migration is complete.
- **D3** — `Channel._log()` frame depth `f_back.f_back` is hardcoded — any helper or wrapper between the channel method and `_log` reports the wrong call-site. Expose a `stacklevel` parameter (like `logging.Logger`) for callers that wrap channel methods.
- **D4** — `FileLogger` holds an open file handle with no `__del__`, `atexit`, or `contextlib` finalizer — on SIGKILL or non-CPython runtimes the handle is never closed. Low risk for CLI (flush() called per write), but add `atexit.register(file_logger.close)` for correctness.

## Deferred from: code review of REVUE-211-migrate-posting Round 2 (2026-05-04)

- **D1** — `get_platform_adapter` uses if/elif chain for platform dispatch (`platform_adapter.py`) — pre-existing OCP violation; adding a new platform requires a code change. Replace with a `{Platform: factory_fn}` registry dict. Track in a future adapter-hardening story.
- **D2** — Nova `.format()` with user-controlled content — already logged in Round 1 (D2); `_call_nova` catches all exceptions as NOVA_ERROR so impact is bounded. Part of a broader prompt-security hardening effort.

---

## Deferred from: code review of REVUE-211-migrate-posting (2026-05-04)

- **D1** — `get_issue_comments` not declared on VCSAdapter Protocol — works correctly via duck-typing/getattr; only GitHub needs it; Bitbucket/GitLab gracefully fall back to `get_existing_comments`. Add to Protocol for structural completeness in a future adapter-hardening pass.
- **D2** — Nova prompt uses `.format()` with user-controlled text — LLM input sanitisation is a broader concern across all AI-facing prompts. `_call_nova` catches all exceptions as NOVA_ERROR so the blast radius is bounded. Address in a dedicated prompt-security hardening ticket.
- **D3** — Sentinel `ts` regex group can be truncated by platform-inserted newlines — extremely rare; HTML comments are not normally line-wrapped; `_most_recent_sentinel` would select a different sentinel rather than corrupt state. Monitor if sentinel issues surface in dogfood.
- **D4** — `_SENTINEL_RE` fp group accepts uppercase hex and hyphens that never appear in real fingerprints — permissive but harmless; non-canonical fps never match store keys and are silently ignored. Tighten regex when hardening sentinel format spec.

---

## Deferred from: code review of revue-209-body-builder (2026-05-03)

- **D1** — `cli.py` dead code: `_format_recommendation`, `_SUGGESTION_BLOCK_FORMATTERS`, `_get_highest_severity` are no longer called from any production path after BodyBuilder integration. Four unit tests in `test_suggestion_blocks.py:259–311` keep `_format_recommendation` alive. Clean up in PR 5 (explicitly out of scope for REVUE-209).
- **D2** — `build_summary()` not wired to any call site in `cli.py`. PR-level summary posting path not yet migrated to BodyBuilder. Scope: REVUE-214 (Poster / position resolution).
- **D3** — `_highest_severity()` in `body_builder.py` returns `"info"` for non-canonical severity strings (e.g. `"critical"`). Pre-existing upstream normalization gap — `_extract_finding_fields` also doesn't normalize these. Add severity normalization in `_extract_finding_fields` or add a canonical severity set to `ConsolidatedFinding.__post_init__`.
- **D4** — `build_grouped()` has no guard against being called with an empty list or single item. Would produce `"ℹ️ [INFO] 0 findings on this line"` or `"1 findings on this line"` ghost comments. Add `if len(items) < 2: raise ValueError(...)` guard. Not currently reachable from `_run_per_issue_dedup`.
- **D5** — Grammar: "1 findings on this line" in `build_grouped()` header when `n=1`. Add singular/plural: `f"{n} {'finding' if n == 1 else 'findings'} on this line"`. Not currently reachable from `_run_per_issue_dedup` which routes single items to `build()`.

---

## Deferred from: code review of REVUE-208-implementation-readiness (2026-05-02)

- **D1** — Singleton `AIReview → ConsolidatedFinding` migration crash path: `AIReview.synthesised_from=None` for singleton findings; any REVUE-212 migration code that constructs `ConsolidatedFinding` without manually synthesising `[Attribution(agent_name, category)]` will raise `ValueError`. `AIReview.agent_name` defaults to `""` — migration must validate non-empty before constructing `Attribution`. Scope: REVUE-212 implementation.
- **D2** — Stub classes (`ProximityAndCountGroupingStrategy`, `NovaSingleShotStrategy`, etc.) do not inherit Protocol interfaces (`GroupingStrategy`, `SynthesisStrategy`). mypy will not catch method-name typos (e.g. `synthesize` vs `synthesise`) until the class is used at a call site. Consider adding Protocol inheritance in REVUE-212/213 when implementations are written.

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

---

## Deferred from: code review of REVUE-201 patch review findings (2026-04-30)

- **D1 (FBH-3)** — `_run_dedup_201` mock returns `DiffPosition(position=5)` unconditionally; tests don't verify that `resolve_position` was called with the snapped line (not the original). Existing `TestSuggestionAnchorIntegration` covers snap correctness directly so this gap is low priority.
- **D2 (FBH-4)** — `DiffPositionResolver.line_in_diff` calls `_map_diff_lines` independently of `snap()`, parsing the same diff string twice per posting cycle. Negligible for typical diff sizes; refactor if large-diff performance becomes a concern.
- **D3 (FEH-A)** — Conservative trade-off: when an agent miscounts the anchor by even 1 line and snap relocates it, `replacement_line_count` is reset to 1 even though the intended span might be entirely within the diff. Posts correctly but as single-line rather than multi-line. Intentional per spec; document in agent prompt guidance post-MVP.
- **D4 (FEH-C)** — `_run_dedup_201` tests don't assert the snapped line was forwarded to `resolve_position`, only that the posted rlc is correct. Add `assert mock_adapter.resolve_position.call_args.args[1] == expected_snapped_line` when adding snapping end-to-end coverage.

---

## Deferred from: dogfood run on fix/REVUE-201-suggestion-anchor-range (2026-04-30)

- **ARCH-1** — `DiffPositionResolver` is a stateless utility class with all `@staticmethod` — namespace anti-pattern (Leo, medium). Convert to module-level functions (`snap()`, `line_in_diff()`, `_map_diff_lines()`). Requires updating all call sites in `cli.py` and all test references. Defer to a dedicated refactor story post-REVUE-201 merge.
- **WIN-1** — Windows drive-relative paths (e.g. `C:relative`) return `False` from `Path.is_absolute()`, bypassing the absolute-path guard in `DiffPositionResolver.snap()`. Zero risk on macOS/Linux. Revisit if Windows support is added.

---

### D2 — `rr.file_path` can be `None` or empty string
`src/revue/cli.py` / reviewed-files dedup block. If a `ReviewResult` has `file_path=None` and a truthy `response`, it passes the `not rr.error and rr.response` filter and renders as `` `None` `` in the published comment. Pre-existing issue (old code had the same behaviour). Should be guarded with `if rr.file_path`.

---

## Deferred from: code review of REVUE-249 (2026-05-15)

- **DF1 — Vex prompt heuristic inconsistency** `src/revue/comments/_verifier.py` — The block-completeness subsection tells Vex: "if the trailing line is at or deeper than the *deepest* indent inside the range, the block continues." The deterministic guard correctly uses `min_indent` (shallowest/outermost) not `max_indent`. For same-level orphan cases like PR #29 Case 1 (orphan at indent 4 with deepest range indent ~16), Vex's heuristic (`4 >= 16 → False`) would pass the finding where the guard (`4 >= 4 → True`) correctly fires. The guard compensates as the hard backstop. The prompt wording should be corrected to "shallowest/outermost indent in the range" for full Vex-side effectiveness.
- **DF2 — No file-content cache in `OrphanLineGuardPostProcessor`** `src/revue/comments/_orphan_line_guard.py:79` — Same file is read from disk once per finding. Consistent with `VexVerifyPostProcessor`'s pattern. For reviews with many findings on the same large file this causes redundant I/O and inconsistent snapshots if the file changes mid-run (unlikely). A `dict[str, str]` read-cache would eliminate this.
- **DF3 — `_build_consolidator` positional 3-tuple expands each story** `src/revue/core/pipeline.py:1349` — Was 2-tuple (REVUE-248), is now 3-tuple (REVUE-249). A `NamedTuple` or lightweight result dataclass would remove positional coupling and make the next addition safe without sync-updating all unpack sites.
