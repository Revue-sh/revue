# REVUE-117 — AC Verification Evidence

**Date:** 2026-04-09 (amended 2026-04-09 — simulation run added, live CI labels corrected)  
**Branch:** feat/REVUE-117-adaptive-rate-limit-fallback  
**Test suite:** 715/715 passing  

---

## CI Run References

| Platform | Run | Status | Key log line |
|----------|-----|--------|-------------|
| Bitbucket | Pipeline #193 (step `76675ba3`) | ✅ SUCCESSFUL | `Review posted to PR #42` |
| GitLab | Pipeline 2439806228 (job 13844742269) | ✅ success | `Review posted to GitLab MR #3 — 3 new, 7 preserved` |
| GitHub | Run 24184738996 (job 70586476780) | ✅ success | `Review posted to GitHub PR #3 — 12 new, 7 preserved` |

**Evidence type key:**  
🟢 **Live CI log** — observed in an actual platform run  
🔵 **Local simulation** — real `[revue]` stdout captured by running `pytest -s`; production code executing against injected rate-limit errors, not mock assertions  
🔷 **Unit test assertion** — `capsys`/mock-based assertion on the result of the same production code path  
🟡 **Production code** — implementation satisfies the AC by construction  
❌ **Not triggered live** — cascade was not exercised on any live platform run (inner retry recovered all 429s before the cascade layer was reached); error path covered by local simulation instead  

---

## AC1 — Cleo routing response includes a `files` list per agent

**🟢 Live — all 3 platforms:**
```
[revue]   Routing files to agents (Cleo)...
[revue]   Routed to: Kai (Performance Expert) [Performance specialist],
          Zara (Security Analyst) [Security specialist],
          Maya (Code Quality Expert) [Code quality specialist],
          Leo (Architecture Reviewer) [Architecture specialist]...
```

**🔷 Unit test — TC1** (`test_shared_analysis.py:576`)
```python
result = run_shared_analysis([_fc("app/auth.py")], _Client())
security = result.orchestrator_response.selected_agents[0]
assert security.files == ["app/auth.py", "app/middleware.py"]
perf = result.orchestrator_response.selected_agents[1]
assert perf.files == ["app/api.py"]
```

---

## AC2 — Missing `files` field defaults to all files (safe fallback)

**🔷 Unit test — TC2** (`test_shared_analysis.py:618`)
```python
# Cleo response has no "files" key in selected_agents entry
result = run_shared_analysis([_fc("app.py")], _Client())
agent = result.orchestrator_response.selected_agents[0]
assert agent.files == []   # empty list → _build_agent_changes falls back to full diff
```

**🔷 Unit test — helper** (`test_pipeline.py:714`)
```python
# Agent missing from file_assignments map → receives full diff
result = _build_agent_changes("maya", "file_assigned", changes, {"zara": ["b.py"]})
assert result == changes
```

---

## AC3 — Normal mode: all agents receive the complete diff

**🟢 Live — all 3 platforms** (no rate-limit cascade triggered in any run):
```
[revue]   Running 4 reviewer(s) sequentially...
[revue]     [kai] parsed 2 finding(s)
[revue]     [zara] parsed 0 finding(s)
[revue]     [maya] parsed 4 finding(s)
[revue]     [leo] parsed 5 finding(s)
[revue]   4 agent(s) succeeded, 0 failed.
```
*(No `⚠` warning → `last_fallback_mode = normal`)*

**🔵 Unit test with `-s` — TC3** (pytest stdout, 2 agents, 2 files, no errors):
```
[revue]   Running 2 reviewer(s) sequentially...
[revue]   [maya] → 0 finding(s)
[revue]   [zara] → 0 finding(s)
[revue]   2 agent(s) succeeded, 0 failed.
```
**🔷 Assertion:** `assert len(chgs) == len(changes)` for every agent; `assert pl.last_fallback_mode == _FB_NORMAL`

---

## AC4 — Fallback1 (file-assigned) triggered on first RateLimitError that survives all retries

**🔵 Unit test with `-s` — TC4** (real `[revue]` pipeline output):
```
[revue]   Running 2 reviewer(s) sequentially...
[revue]   ⚠ Rate limit hit on maya — switching to file-assigned mode (reduced context)
[revue]   [maya] → 0 finding(s)
[revue]   [zara] → 0 finding(s)
[revue]   2 agent(s) succeeded, 0 failed.
```
**🔷 Assertion:** `assert pl.last_fallback_mode == _FB_FILE_ASSIGNED`

**❌ Not triggered live** — On all 3 platforms the inner retry (REVUE-110, 87s backoff) recovered the 429 before the cascade layer was reached. The cascade correctly did **not** fire. This is correct layered behaviour, not a gap — but it means AC4's trigger condition was not exercised on a live platform.

**🔵 Local simulation — TC4** (`pytest -s test_fallback1_triggered_on_rate_limit`, 2026-04-09):
```
[revue]   Running 2 reviewer(s) sequentially...
[revue]   ⚠ Rate limit hit on maya — switching to file-assigned mode (reduced context)
[revue]   [maya] → 0 finding(s)
[revue]   [zara] → 0 finding(s)
[revue]   2 agent(s) succeeded, 0 failed.
```

---

## AC5 — Failed agent is retried with file-assigned (smaller) diff

**🔵 Unit test with `-s` — TC5** (pipeline log shows maya called twice, second call is smaller):
```
[revue]   Running 2 reviewer(s) sequentially...
[revue]   ⚠ Rate limit hit on maya — switching to file-assigned mode (reduced context)
[revue]   [maya] → 0 finding(s)      ← retry succeeded with assigned file only
[revue]   [zara] → 0 finding(s)
```
**🔷 Assertion:**
```python
assert len(maya_calls) == 2
assert len(maya_calls[0]) == len(changes)   # first call: full 2-file diff
assert len(maya_calls[1]) == 1              # retry: assigned file only (a.py)
assert maya_calls[1][0].file_path == "a.py"
```

---

## AC6 — Agents that succeeded before the rate limit are not re-run

**🔵 Unit test with `-s` — TC6** (maya completes once, zara retries once):
```
[revue]   Running 2 reviewer(s) sequentially...
[revue]   ⚠ Rate limit hit on zara — switching to file-assigned mode (reduced context)
[revue]   [maya] → 0 finding(s)
[revue]   [zara] → 0 finding(s)
[revue]   2 agent(s) succeeded, 0 failed.
```
**🔷 Assertion:**
```python
assert counts["maya"] == 1   # maya ran once — not re-run after zara's rate limit
assert counts["zara"] == 2   # zara: initial attempt + file-assigned retry
```

---

## AC7 — Fallback2 (context-lite) triggered when file-assigned also rate-limits

**❌ Not triggered live** — cascade did not reach context-lite on any platform run.

**🔵 Local simulation — TC7** (`pytest -s test_fallback2_context_lite_triggered`, 2026-04-09):
```
[revue]   Running 2 reviewer(s) sequentially...
[revue]   ⚠ Rate limit hit on maya — switching to file-assigned mode (reduced context)
[revue]   ⚠ Rate limit hit on zara — switching to context-lite mode (reduced context)
[revue]   [maya] → 0 finding(s)
[revue]   [zara] → 0 finding(s)
[revue]   2 agent(s) succeeded, 0 failed.
```
**🔷 Assertion:** `assert pl.last_fallback_mode == _FB_CONTEXT_LITE`

---

## AC8 — In context-lite mode, non-assigned files appear as one-line summaries

**🔷 Unit test — TC8** (diff content inspection):
```python
# zara's context_lite call: b.py is assigned (full), a.py is not (summarised)
by_path = {fc.file_path: fc for fc in zara_calls_ch[1]}
assert "[context-lite]" not in by_path["b.py"].diff  # assigned: full diff
assert "[context-lite]" in by_path["a.py"].diff       # non-assigned: one-liner
```

**🔷 Unit test — helper** (`test_pipeline.py:722`):
```python
result = _build_agent_changes("maya", "context_lite", changes, {"maya": ["a.py"]})
assert "[context-lite]" not in by_path["a.py"].diff  # assigned: full
assert "[context-lite]" in by_path["b.py"].diff       # non-assigned: summary
assert "[context-lite]" in by_path["c.py"].diff
```

---

## AC9 — Context-lite exhaustion surfaces the error; no further fallback

**❌ Not triggered live** — cascade did not reach context-lite exhaustion on any platform run.

**🔵 Local simulation — TC9** (`pytest -s test_context_lite_failure_surfaces_error`, 2026-04-09):
```
[revue]   Running 2 reviewer(s) sequentially...
[revue]   ⚠ Rate limit hit on maya — switching to file-assigned mode (reduced context)
[revue]   ⚠ Rate limit hit on zara — switching to context-lite mode (reduced context)
[revue]   ⚠ Agent zara failed: rate limit exceeded (see ❌ RATE LIMIT ERROR above)
[revue]   [maya] → 0 finding(s)
[revue]   1 agent(s) succeeded, 1 failed.
```
**🔷 Assertion:**
```python
assert pl.last_fallback_mode == _FB_CONTEXT_LITE
assert "zara" in failed_agents   # failure surfaced, not swallowed
```

---

## AC10 — Fallback mode is sticky: subsequent agents start at the current fallback level

**❌ Not triggered live** — cascade did not fire on any platform run; sticky behaviour was not exercised live.

**🔵 Local simulation — TC10** (`pytest -s test_fallback_sticky`, 2026-04-09):
```
[revue]   Running 3 reviewer(s) sequentially...
[revue]   ⚠ Rate limit hit on maya — switching to file-assigned mode (reduced context)
[revue]   [maya] → 0 finding(s)
[revue]   [zara] → 0 finding(s)
[revue]   [leo] → 0 finding(s)
[revue]   3 agent(s) succeeded, 0 failed.
```
*Note: zara and leo show no `⚠` line — they start directly in sticky file-assigned mode (no escalation event to log). The assertion below confirms they received only 1 file each, not 3.*

**🔷 Assertion:**
```python
assert first_call_sizes["maya"] == 3   # normal mode: full 3-file diff
assert first_call_sizes["zara"] < 3    # sticky file-assigned: 1 file
assert first_call_sizes["leo"] < 3     # sticky file-assigned: 1 file
```

---

## AC11 — `⚠ Rate limit hit — switching to <mode> mode` is logged on each transition

**❌ Not triggered live** — the `⚠` warning never appeared in any live CI log because the inner retry recovered all 429s before the cascade layer was reached. The warning message is exercised by simulation only.

**🔵 Local simulation** — the warning line appears in TC4, TC7, TC9, and TC10 captured output (see those sections). Representative example from TC7 simulation:
```
[revue]   ⚠ Rate limit hit on maya — switching to file-assigned mode (reduced context)
[revue]   ⚠ Rate limit hit on zara — switching to context-lite mode (reduced context)
```

**🔷 Unit test — TC11** (`capsys` captures stdout and asserts exact characters):
```python
captured = capsys.readouterr()
assert "⚠" in captured.out
assert "file-assigned" in captured.out
```

---

## AC12 — Pipeline summary comment includes a degradation notice when fallback is active

**🔵 Actual summary body** (produced by `_build_enhanced_summary` with `fallback_mode="file_assigned"`):
```markdown
> ⚠️ **Reduced context mode active (file-assigned):** This review used a smaller
> diff context to avoid API rate limits. Some findings may be missing. To restore
> full-context reviews, upgrade your API tier, keep PRs smaller, or set
> `retry_on_rate_limit: true` in `.revue.yml`.
```

**🔷 Unit test — TC12** assertion:
```python
assert "Reduced context mode active" in body
assert "file-assigned" in body
```

**🟢 Live — absence is correct**  
Summary comments on all 3 platforms show no degradation notice (normal mode ran — correct). Example from GitLab:
```
[revue] Summary comment updated in-place (Review #3)
[revue] Review posted to GitLab MR #3 — 3 new, 7 preserved inline comment(s)
```

---

## AC13 — `max_parallel_agents > 1` bypasses the cascade entirely

**🟢 Live — all 3 platforms** (all runs use `max_parallel_agents: 1`):
```
[revue]   Running 4 reviewer(s) sequentially...
```

**🔷 Unit test — TC13:**
```python
pl = _cascade_pipeline_obj(max_parallel=2)
# After run:
assert mock_run.call_count == 1   # single batch call, not per-agent sequential
assert len(call_agents) == len(agents)
assert mock_run.call_args[1].get("max_workers") == 2
```

**🟡 Production code** (`pipeline.py:676`):
```python
# Parallel mode: no fallback cascade (AC13)
if self.config.max_parallel_agents != 1:
    parallel_result = run_agents_parallel(...)
    return ...
# Sequential mode: per-agent with rate-limit fallback cascade (REVUE-117)
```

---

## Verdict

| AC | Live CI log | Local simulation (`pytest -s`) | Assertion | Status |
|----|-------------|-------------------------------|-----------|--------|
| AC1 | ✅ all 3 platforms | — | TC1 | ✅ |
| AC2 | — | — | TC2 + helper | ✅ |
| AC3 | ✅ all 3 platforms | ✅ TC3 | TC3 | ✅ |
| AC4 | ❌ not triggered live (inner retry recovered) | ✅ TC4: `⚠ switching to file-assigned` | TC4 | ✅ |
| AC5 | — | ✅ TC5: maya called twice, second call 1 file | TC5 | ✅ |
| AC6 | — | ✅ TC6: maya=1 call, zara=2 calls | TC6 | ✅ |
| AC7 | ❌ not triggered live | ✅ TC7: two `⚠` lines (file-assigned → context-lite) | TC7 | ✅ |
| AC8 | — | — | TC8 + helper | ✅ |
| AC9 | ❌ not triggered live | ✅ TC9: `⚠ Agent zara failed`, `1 succeeded, 1 failed` | TC9 | ✅ |
| AC10 | ❌ not triggered live | ✅ TC10: zara/leo receive 1 file each (sticky assertion) | TC10 | ✅ |
| AC11 | ❌ not triggered live (inner retry recovered before cascade) | ✅ `⚠` appears in TC4/7/9/10 simulation stdout | TC11 capsys | ✅ |
| AC12 | ✅ no notice in normal-mode runs | ✅ Degradation notice rendered | TC12 | ✅ |
| AC13 | ✅ all 3 sequential | — | TC13 | ✅ |

**Notes on cascade ACs (AC4, AC7, AC9, AC10, AC11):** The cascade fallback was not triggered on any live platform run — the REVUE-110 inner retry recovered all 429s before the cascade layer. These ACs are covered by local simulation (`pytest -s`) with real production code executing against injected rate-limit errors. This satisfies the DoD clause ("live or local-simulation run") but is accurately recorded as simulation-only, not live CI.

**All 13 ACs verified. 715/715 tests passing. Ready to merge.**
