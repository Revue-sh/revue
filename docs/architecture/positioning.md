# Positioning Architecture

**Status:** Accepted — AC7 complete as of 2026-05-09 (REVUE-236)
**Decision date:** 2026-05-09
**Context:** REVUE-236 per-platform PositionAdapter design

---

## Purpose

This document describes how Revue turns an agent-reported line number into a
platform-native comment anchor — the positioning pipeline. "Positioning" covers
everything from diff ingestion through agent review to the API call that places
an inline comment on the correct line of a PR or MR.

---

## Full Data Flow

```mermaid
flowchart TD
    CI([Platform CI trigger\nGitHub Actions · GitLab CI · Bitbucket Pipeline])
    CI --> FETCH[VCSAdapter.get_diff\nfetches raw unified diff]
    FETCH --> RAWDIFF[(diff file on disk)]

    RAWDIFF --> PARSE[DiffParser.parse_diff_file]
    PARSE --> FILECHANGES[list FileChange\nfile_path · diff text · language]
    FILECHANGES --> FILTER[filter_changes\nignore patterns · max_diff_lines]
    FILTER --> INCLUDED[included: list FileChange]

    INCLUDED --> CLEO{Premium tier?}
    CLEO -- yes --> CLEOROUTE[Cleo orchestrator\nassigns files to agents]
    CLEO -- no --> ALLFILES[all files → all agents]
    CLEOROUTE --> PARALLEL
    ALLFILES --> PARALLEL

    subgraph Agents [Parallel agent review]
        PARALLEL[run_agents_parallel] --> MAYA[maya\ncode quality]
        PARALLEL --> ZARA[zara\nsecurity]
        PARALLEL --> KAI[kai\nperformance]
        PARALLEL --> LEO[leo\narchitecture]
    end

    MAYA --> AIRLIST
    ZARA --> AIRLIST
    KAI --> AIRLIST
    LEO --> AIRLIST
    AIRLIST[flat list AIReview\nfile_path · line_number · issue · suggestion · confidence]

    AIRLIST --> GROUPA[Pass A — ProximityAndCountGroupingStrategy\nN ≤ 3 lines apart · K ≤ 3 per group]
    GROUPA --> GROUPS[list SynthesisGroup]

    subgraph Consolidation [Nova consolidation]
        GROUPS --> NOVA[NovaSingleShotStrategy\nNova AI call — one batch per run]
        NOVA --> CFLIST[list ConsolidatedFinding\nanchor · prose · code_replacement · attribution]
    end

    CFLIST --> DROPPER[NoOpSuggestionDropper\ndrops code_replacement that equals existing lines]
    DROPPER --> EXTRACTOR[UnanchoredFindingExtractor]
    EXTRACTOR -- anchored findings --> RESULTS[list ReviewResult\nfile_path · JSON payload]
    EXTRACTOR -- unanchored findings --> SINK1[(summary_sink)]

    RESULTS --> CLIBUILD[cli.py\ndiff_by_file = file_path → diff text\nbuilt from the same diff file]

    subgraph Positioning [Positioning — Poster.post]
        CLIBUILD --> POSTER[Poster.post\nreview_results · diff_by_file]
        POSTER --> PADAPTER{platform supported\nby PositionAdapter?}

        PADAPTER -- github · gitlab --> GETADAPTER[get_position_adapter\nGitHubPositionAdapter\nGitLabPositionAdapter]
        PADAPTER -- bitbucket · other --> SNAP[DiffPositionResolver.snap\nlegacy nearest-changed-line path]

        GETADAPTER --> RESOLVE

        subgraph Resolve [PositionAdapter.resolve — per finding group]
            RESOLVE[resolve\nreported_line · diff_content · file_path · rlc]
            RESOLVE --> PARSEDIFF[_parse_diff\nplus_new · context_new · minus_old · hunks]
            PARSEDIFF --> RULE{reported_line\nin plus_new?}
            RULE -- yes --> PP[PlatformPosition\nstart_line · end_line]
            RULE -- no context · removed · absent --> NONE[None]
        end

        PP --> APIPARAMS[PositionAdapter.to_api_params\nplatform-native dict]
        NONE --> SINK2[(summary_sink)]
        SNAP --> DIFFPOS[DiffPosition\nlegacy struct]
    end

    APIPARAMS --> VCSPOST[VCSAdapter.post_review_comment]
    DIFFPOS --> VCSPOST
    SINK1 --> SUMMARY[post_summary_comment\nPR-level summary block]
    SINK2 --> SUMMARY

    subgraph Platforms [Platform API calls]
        VCSPOST --> GH[GitHub\nPOST /pulls/N/reviews\npath · line · side · start_line]
        VCSPOST --> GL[GitLab\nPOST /merge_requests/N/discussions\nnew_path · new_line · base_sha · head_sha · start_sha]
        VCSPOST --> BB[Bitbucket\nPOST /pullrequests/N/comments\ninline.path · inline.to · inline.from]
    end
```

---

## Key design decisions

### Strict binary anchor rule (AC2)

`resolve()` classifies the reported line by parsing the per-file unified diff:

| Reported line maps to | Result |
|-----------------------|--------|
| `+` line (added or modified) | `PlatformPosition(start_line, end_line)` |
| context line (space-prefixed, unchanged) | `None` → `summary_sink` |
| `−` line (removed, no longer in new file) | `None` → `summary_sink` |
| not present in diff at all | `None` → `summary_sink` |

No snapping. No proximity heuristics. Positioning is deterministic once the diff
is frozen at CI trigger time.

### Per-platform adapters behind a shared Protocol

```
PositionAdapter (Protocol)
├── GitHubPositionAdapter   — path + line + side [+ start_line + start_side]
├── GitLabPositionAdapter   — new_path + new_line + base/head/start SHAs
└── BitbucketPositionAdapter — inline.path + inline.to [+ inline.from]
```

`get_position_adapter(platform, pr_context, vcs_adapter)` selects from a dict
registry — no `if/elif` chains (OCP maintained). For GitLab, the factory fetches
MR version SHAs once at construction time so each `to_api_params()` call is pure.

### Diff reuse

The diff file written by the CI step is parsed twice:

1. **Pipeline** — `parse_diff_file()` → `list[FileChange]` → agents receive
   `fc.diff` (per-file diff text) as their review context.
2. **Poster** — `_parse_diff_by_file()` → `{file_path: diff}` → passed to
   `Poster` so `resolve()` can classify agent-reported lines against the exact
   same diff the agent reviewed.

This identity guarantee is what makes the strict binary rule reliable.

### summary_sink

Two sources feed the PR-level summary comment:

- **`UnanchoredFindingExtractor`** — findings that reached the `Consolidator`
  but had no verifiable `snippet`/anchor evidence after consolidation.
- **`PositionAdapter.resolve() → None`** — findings whose agent-reported line
  is a context, removed, or absent line in the diff.

Both are routed to `summary_sink` and rendered with `(~line N)` notation to
signal an approximate location, not a verified anchor.

---

## Current gap — REVUE-236 AC7

`to_api_params()` output is not yet wired into the posting path. After `resolve()`
returns a `PlatformPosition`, `poster.py` discards the platform-specific dict
and wraps the line numbers back into a `DiffPosition` struct, which the legacy
adapter path then re-encodes independently.

Consequences until AC7 is fixed:

- `to_api_params()` is tested (unit + fixture level) but never executed in
  production.
- Bitbucket is excluded from `_POSITION_ADAPTER_PLATFORMS` and still uses
  `DiffPositionResolver.snap()`.
- GitLab's `post_review_comment()` re-fetches MR version SHAs on every comment
  instead of using the ones cached by `GitLabPositionAdapter`.

AC7 fix: replace the `DiffPosition` construction block with a direct call to
`to_api_params()` and pass the resulting dict straight to the platform API,
bypassing the legacy `DiffPosition` path.

---

## Fixture contract

Position fixtures live in `src/revue/tests/fixtures/positioning/{github,gitlab,bitbucket}/`.
Each fixture encodes:

- `diff_snippet` — the per-file unified diff
- `reported_line` — what an agent would emit
- `replacement_line_count` — multi-line span, default 1
- `expected_position` — expected `PlatformPosition` output, or `null` for None
- `expected_api_params` — expected `to_api_params()` output

Run all fixtures: `python scripts/local_run.py position --all`

---

## Open gaps — identified 2026-05-09 (REVUE-239 analysis)

The following gaps were identified during a local dry-run of the full pipeline and
a debug session on inline comment positioning. They are sequenced in dependency
order: Gap 1 is a prerequisite for Gaps 2–4.

---

### Gap 1 — Two classification code paths (resolve + calculate)

**Problem:**

`_BasePositionAdapter.resolve()` and the new `calculate()` function in
`position_adapter.py` implement the same diff-classification logic independently.
Both call `_parse_diff()` directly. Neither delegates to the other.

Consequences:
- A bug fix in one path does not fix the other.
- `Log.position` traces are only emitted by `resolve()`. `calculate()` is silent —
  the local sandbox and fixture tests have zero visibility into why a line was
  classified as `out_of_hunk`.
- The two paths can silently diverge in behaviour as either is modified.

**Decision:**

`resolve()` must delegate to `calculate()` internally. `calculate()` becomes the
single classification implementation. `resolve()` becomes a thin shell:

```
calculate(diff, line, file_path, rlc) → PositionResult   ← single logic path
    ↑
resolve(...)                                              ← logging + PlatformPosition mapping
```

`resolve()` cannot be removed in favour of routing everything through it because:
- `resolve()` returns `PlatformPosition | None` (no `status`, no `reason`).
- Tests and the local sandbox require `PositionResult` (typed status + reason) to
  assert correctness. The return types are incompatible.

`calculate()` cannot be removed in favour of routing through `resolve()` for the
same reason: callers that need `PositionResult` would need to instantiate an adapter
class and lose the structured failure information.

**Work required:** REVUE-239

---

### Gap 2 — Agent line-number coordinate system is undefined

**Problem:**

Agent system prompts specify `line_number: specific line number` with no further
guidance on coordinate system. A unified diff hunk header:

```
@@ -old_start,old_count +new_start,new_count @@
```

gives absolute new-file line numbers, but an agent counting naively from the top
of the diff snippet would report a small relative offset (1, 2, 3 …) instead of
the absolute new-file line number. Both behaviours are observed in practice.

When `old_start` equals `new_start` (no prior insertions/deletions), relative and
absolute line numbers coincide — the bug is silent. When they diverge (e.g.
`@@ -10,5 +50,5 @@`), `calculate()` looks for the reported number in `plus_new`
(which contains absolute new-file numbers) and returns `out_of_hunk` for a
correctly identified issue.

**Decision:**

Agent system prompts must explicitly specify:

> `line_number` must be the absolute line number in the **new version of the file**
> — derive it from the `+new_start` value in the hunk header (`@@ … +new_start,… @@`)
> plus the count of non-removed lines before the issue within that hunk.

This is the coordinate system the GitHub, GitLab, and Bitbucket APIs all expect
(`line`/`new_line`/`inline.to` respectively).

**Verification required:** Run the pipeline against a diff where `old_start` and
`new_start` diverge significantly and confirm all four agents report absolute
new-file line numbers consistently.

**Work required:** REVUE-239 (agent prompt update + fixture coverage for offset hunks)

---

### Gap 3 — PlatformPosition loses status and reason; Nova cannot reason about anchor quality

**Problem:**

`resolve()` maps `PositionResult → PlatformPosition | None`. The `status`
(`anchored`, `context_line`, `removed_line`, `out_of_hunk`) and `reason` fields
are discarded. By the time Nova synthesises a group:

- All non-anchored findings look identical — `None` — regardless of whether the
  line was a context line, a removed line, or simply absent from the diff.
- Nova cannot distinguish a stale finding (removed line) from a misreported
  coordinate (out of hunk) from an architectural comment with no specific anchor
  (line 1, file-level).

**Decision:**

Extend `PlatformPosition` to carry `status: PositionStatus` and `reason: str`,
populated from `PositionResult` inside `resolve()`. Platform adapters that gate on
`status == ANCHORED` to build API params are unaffected — they already do this.

With this information Nova can route by status:

| Status | Nova routing |
|--------|-------------|
| `anchored` | Post inline |
| `context_line` | Post inline with note that exact line is unchanged |
| `removed_line` | Flag as potentially stale finding; post to summary |
| `out_of_hunk` | Post to summary with `(~line N)` notation |

Nova can also be given the per-file diff for cases where the anchor is uncertain,
enabling it to reason about whether a re-classification is possible before
routing to the summary sink.

**Work required:** REVUE-239 + follow-on story

---

### Gap 4 — ANCHORED_INFERRED is indistinguishable from ANCHORED

**Problem:**

`calculate()` has two paths to `status=ANCHORED`:

1. **Direct** — `reported_line` found in `plus_new` (confirmed from diff body).
2. **Inferred** — truncation fallback: line confirmed only from the pure-addition
   hunk header (`@@ -0,0 +N,M @@`) because the diff body was truncated before
   reaching that line. Higher probability of being wrong.

Both return `status=PositionStatus.ANCHORED` today. Nothing downstream can
distinguish them.

**Decision:**

Add `ANCHORED_INFERRED` to `PositionStatus`:

```python
class PositionStatus(str, Enum):
    ANCHORED          = "anchored"           # confirmed from diff body
    ANCHORED_INFERRED = "anchored_inferred"  # inferred from hunk header only
    CONTEXT_LINE      = "context_line"
    REMOVED_LINE      = "removed_line"
    OUT_OF_HUNK       = "out_of_hunk"
```

Nova routing for `anchored_inferred`: post inline but include a note that the
position was inferred from a truncated diff — the developer should verify the
line before applying any suggestion.

This is deterministic and testable. It does not require Nova to inspect the diff.

**Work required:** follow-on story after REVUE-239

---

## References

- [Comment Posting Architecture](comment-posting.md) — Consolidator, BodyBuilder, Poster contracts
- [Consolidation Architecture](consolidation.md) — Nova batch prompt format, SynthesisStrategy
- [Anchor Correction Authority](anchor-correction-authority.md) — Proposed ADR (2026-05-14) extending this design: Vex becomes the single owner of anchor correction (post-REVUE-247 lessons); PositionAdapter remains a pure classifier
- REVUE-236 — per-platform PositionAdapter implementation ticket
- REVUE-239 — inline comment positioning bug; Gaps 1–2 above
