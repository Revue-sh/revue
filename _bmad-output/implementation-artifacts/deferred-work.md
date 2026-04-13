# Deferred Work

Items surfaced during review but not caused by the current story. Collect here for future focused attention.

---

## Deferred from REVUE-134 (2026-04-13)

### D1 — Feature flags have no env-var override path
`src/revue/core/ai_config.py` / `from_env()`. No feature flag (`preserve_comment_threads`, `show_reviewed_files`) is settable via an environment variable, so CI pipelines cannot toggle them without a `.revue.yml` file present. Pattern would be e.g. `REVUE_SHOW_REVIEWED_FILES=false`. Pre-existing gap, not introduced by REVUE-134.

### D2 — `rr.file_path` can be `None` or empty string
`src/revue/cli.py` / reviewed-files dedup block. If a `ReviewResult` has `file_path=None` and a truthy `response`, it passes the `not rr.error and rr.response` filter and renders as `` `None` `` in the published comment. Pre-existing issue (old code had the same behaviour). Should be guarded with `if rr.file_path`.
