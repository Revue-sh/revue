"""Unit tests for WontFixReplyService and NovaConsolidator.analyse_reply_threads.

TC1a–TC8 cover the AI analysis path via NovaConsolidator.
TC12–TC17 cover WontFixReplyService orchestration logic.

All external calls are mocked — no real API calls.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from revue.comments.models import CommentState
from revue.comments.json_store import PerPRCommentStore
from revue.core.dedup_consolidator import NovaConsolidator, _parse_thread_decisions
from revue.core.ai_client import CompletionResult, TokenUsage


def _cr(text: str) -> CompletionResult:
    return CompletionResult(text=text, usage=TokenUsage())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_threads(n: int = 1) -> list[dict]:
    return [
        {
            "fingerprint": f"fp{i:04d}",
            "file_path": f"src/file{i}.py",
            "line": i * 10,
            "issue_type": "security",
            "severity": "high",
            "original_finding_summary": f"Finding {i}",
            "replies": [f"This is fine because of reason {i}"],
        }
        for i in range(1, n + 1)
    ]


def _ai_client_returning(payload: Any) -> MagicMock:
    """Return a mock AI client that returns the JSON-serialised payload."""
    client = MagicMock()
    client.complete.return_value = _cr(json.dumps(payload))
    return client


# ---------------------------------------------------------------------------
# TC1a: empty threads → return []
# ---------------------------------------------------------------------------

def test_analyse_reply_threads_empty_input_returns_empty() -> None:
    """TC1a: analyse_reply_threads([]) returns [] without calling AI."""
    client = MagicMock()
    nova = NovaConsolidator(client)

    result = nova.analyse_reply_threads([])

    assert result == []
    client.complete.assert_not_called()


# ---------------------------------------------------------------------------
# TC1b: single AI call for all threads (batching — AC11)
# ---------------------------------------------------------------------------

def test_analyse_reply_threads_batches_into_one_call() -> None:
    """TC1b: All threads are sent in a single AI call, not one per thread."""
    decisions = [
        {"fingerprint": "fp0001", "decision": "allowed_pattern",
         "reply_draft": "Noted.", "pattern": "X", "rationale": "Y"},
        {"fingerprint": "fp0002", "decision": "reason_missing",
         "reply_draft": "Please provide a reason."},
    ]
    client = _ai_client_returning(decisions)
    nova = NovaConsolidator(client)

    nova.analyse_reply_threads(_make_threads(2))

    assert client.complete.call_count == 1


# ---------------------------------------------------------------------------
# TC1c: large thread list is split into multiple batches
# ---------------------------------------------------------------------------

def test_analyse_reply_threads_splits_large_list_into_batches() -> None:
    """TC1c: 20 threads (> batch size of 15) → 2 AI calls; all decisions returned."""
    from revue.core.dedup_consolidator import _THREAD_BATCH_SIZE

    batch_size = _THREAD_BATCH_SIZE  # 15
    total = batch_size + 5           # 20 — needs exactly 2 calls

    def _batch_decisions(threads: list[dict]) -> str:
        return json.dumps(
            [{"fingerprint": t["fingerprint"], "decision": "reason_missing", "reply_draft": "x"}
             for t in threads]
        )

    all_threads = _make_threads(total)
    client = MagicMock()
    # Return decisions for whatever batch the AI receives
    client.complete.side_effect = lambda msgs, **kw: _cr(
        _batch_decisions(json.loads(msgs[0]["content"].split("\n\n", 1)[1]))
    )
    nova = NovaConsolidator(client)

    result = nova.analyse_reply_threads(all_threads)

    assert client.complete.call_count == 2
    assert len(result) == total
    returned_fps = {d["fingerprint"] for d in result}
    assert returned_fps == {t["fingerprint"] for t in all_threads}


# ---------------------------------------------------------------------------
# TC2: AI response parsed correctly
# ---------------------------------------------------------------------------

def test_analyse_reply_threads_parses_decisions() -> None:
    """TC2: Returned decisions match AI response."""
    decisions = [
        {"fingerprint": "fp0001", "decision": "allowed_pattern",
         "reply_draft": "Acknowledged. A lessons PR will be opened.",
         "pattern": "Skip auth check", "rationale": "Internal network only"},
    ]
    client = _ai_client_returning(decisions)
    nova = NovaConsolidator(client)

    result = nova.analyse_reply_threads(_make_threads(1))

    assert len(result) == 1
    assert result[0]["fingerprint"] == "fp0001"
    assert result[0]["decision"] == "allowed_pattern"
    assert result[0]["pattern"] == "Skip auth check"


# ---------------------------------------------------------------------------
# TC3: Markdown code fence stripped from AI response
# ---------------------------------------------------------------------------

def test_parse_thread_decisions_strips_code_fences() -> None:
    """TC3: ```json ... ``` wrapping is stripped before JSON parse."""
    raw = '```json\n[{"fingerprint":"fp","decision":"reason_missing","reply_draft":"Please explain."}]\n```'
    result = _parse_thread_decisions(raw)
    assert len(result) == 1
    assert result[0]["fingerprint"] == "fp"


def test_parse_thread_decisions_handles_plain_code_fence() -> None:
    """TC3 variant: ``` without language label also stripped."""
    raw = '```\n[{"fingerprint":"fp2","decision":"not_acknowledged","reply_draft":"Ack."}]\n```'
    result = _parse_thread_decisions(raw)
    assert len(result) == 1
    assert result[0]["decision"] == "not_acknowledged"


def test_parse_thread_decisions_strips_trailing_commas() -> None:
    """TC3b: AI sometimes emits trailing commas — parser must tolerate them."""
    raw = (
        '```json\n'
        '[\n'
        '  {\n'
        '    "fingerprint": "fp1",\n'
        '    "decision": "reason_missing",\n'
        '    "reply_draft": "",\n'
        '  },\n'
        '  {\n'
        '    "fingerprint": "fp2",\n'
        '    "decision": "acknowledged_fixed",\n'
        '    "reply_draft": "Fix confirmed.",\n'
        '  },\n'
        ']\n'
        '```'
    )
    result = _parse_thread_decisions(raw)
    assert len(result) == 2
    assert result[0]["fingerprint"] == "fp1"
    assert result[1]["decision"] == "acknowledged_fixed"


# ---------------------------------------------------------------------------
# TC4: Invalid JSON from AI → batch skipped, returns [] (EC-2 per-batch isolation)
# ---------------------------------------------------------------------------

def test_analyse_reply_threads_invalid_json_returns_empty() -> None:
    """TC4: Malformed AI response in a batch → that batch is skipped, [] returned.
    Per-batch isolation means a single bad response does not raise to the caller."""
    client = MagicMock()
    client.complete.return_value = _cr("this is not json at all")
    nova = NovaConsolidator(client)

    result = nova.analyse_reply_threads(_make_threads(1))
    assert result == []


# ---------------------------------------------------------------------------
# TC5: AI returns non-list JSON → batch skipped, returns []
# ---------------------------------------------------------------------------

def test_analyse_reply_threads_non_list_json_returns_empty() -> None:
    """TC5: AI returns object instead of array → batch skipped, [] returned."""
    client = MagicMock()
    client.complete.return_value = _cr('{"error": "unexpected"}')
    nova = NovaConsolidator(client)

    result = nova.analyse_reply_threads(_make_threads(1))
    assert result == []


# ---------------------------------------------------------------------------
# TC6: AI call raises → batch skipped, returns []
# ---------------------------------------------------------------------------

def test_analyse_reply_threads_ai_exception_returns_empty() -> None:
    """TC6: AI call raises → batch skipped, [] returned. Exception must not propagate."""
    client = MagicMock()
    client.complete.side_effect = RuntimeError("API unavailable")
    nova = NovaConsolidator(client)

    result = nova.analyse_reply_threads(_make_threads(1))
    assert result == []


# ---------------------------------------------------------------------------
# EC-2: Partial batch failure must not discard prior successful results
# ---------------------------------------------------------------------------

def test_analyse_reply_threads_partial_batch_failure_returns_prior_results() -> None:
    """EC-2: When batch N fails, results from batches 1..N-1 are preserved.
    Prior to the fix, any batch failure discarded all accumulated decisions."""
    from revue.core.dedup_consolidator import _THREAD_BATCH_SIZE

    batch_size = _THREAD_BATCH_SIZE  # 15
    total = batch_size + 1           # 16 threads → 2 batches

    batch1_decisions = [
        {"fingerprint": f"fp{i:04d}", "decision": "reason_missing", "reply_draft": "x"}
        for i in range(batch_size)
    ]
    batch1_json = json.dumps(batch1_decisions)

    call_count = [0]

    def side_effect(msgs, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            return _cr(batch1_json)        # batch 1: succeeds
        raise RuntimeError("transient error")  # batch 2: fails

    client = MagicMock()
    client.complete.side_effect = side_effect
    nova = NovaConsolidator(client)

    result = nova.analyse_reply_threads(_make_threads(total))

    assert len(result) == batch_size, (
        "Batch 1 results must be returned even when batch 2 fails"
    )


# ---------------------------------------------------------------------------
# TC7: System prompt is separate from findings analysis (AC11)
# ---------------------------------------------------------------------------

def test_analyse_reply_threads_uses_system_param() -> None:
    """TC7: AC11 — the call uses the system= parameter, not merged content."""
    client = MagicMock()
    client.complete.return_value = _cr("[]")
    nova = NovaConsolidator(client)

    nova.analyse_reply_threads(_make_threads(1))

    _, kwargs = client.complete.call_args
    assert "system" in kwargs
    assert kwargs["system"]  # non-empty system prompt


# ---------------------------------------------------------------------------
# TC8: allowed_pattern decision includes pattern and rationale fields
# ---------------------------------------------------------------------------

def test_allowed_pattern_decision_has_pattern_and_rationale() -> None:
    """TC8: allowed_pattern decisions must carry pattern and rationale."""
    decisions = [
        {
            "fingerprint": "fp0001",
            "decision": "allowed_pattern",
            "reply_draft": "Got it. Lessons PR: [LESSONS_PR_URL]",
            "pattern": "Raw SQL in reporting queries",
            "rationale": "Reporting module bypasses ORM intentionally for performance",
        }
    ]
    client = _ai_client_returning(decisions)
    nova = NovaConsolidator(client)

    result = nova.analyse_reply_threads(_make_threads(1))

    assert result[0]["pattern"] == "Raw SQL in reporting queries"
    assert "intentionally" in result[0]["rationale"]


# ---------------------------------------------------------------------------
# TC12: WontFixReplyService — no threads with replies → skips AI call
# ---------------------------------------------------------------------------

def test_wont_fix_service_skips_ai_when_no_replies(tmp_path) -> None:
    """TC12: process_wont_fix_replies does nothing if no threads have replies."""
    from revue.comments.service import WontFixReplyService

    client = MagicMock()

    mock_adapter = MagicMock()
    # Adapter returns no replies for any comment
    mock_adapter.get_comment_replies.return_value = []

    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=client,
        repo_owner="ws",
        repo_name="repo",
        adapter=mock_adapter,
    )
    svc.process_wont_fix_replies(pr_number=42)

    client.complete.assert_not_called()


# ---------------------------------------------------------------------------
# TC13: WontFixReplyService — allowed_pattern → state updated to wont_fix
# ---------------------------------------------------------------------------

def test_wont_fix_service_updates_state_to_wont_fix(tmp_path) -> None:
    """TC13: allowed_pattern decision transitions store entry to wont_fix."""
    from revue.comments.service import WontFixReplyService

    # Pre-populate the store with an unresolved finding
    store = PerPRCommentStore(tmp_path)
    store.save_finding(
        platform="bitbucket",
        pr_number=1,
        file_path="src/main.py",
        fingerprint="aabbccdd",
        platform_comment_id="100",
        line_number=42,
        comment_body="SQL injection risk",
    )

    decisions = [
        {
            "fingerprint": "aabbccdd",
            "decision": "allowed_pattern",
            "reply_draft": "Noted. Lessons PR: [LESSONS_PR_URL]",
            "pattern": "Raw SQL for reporting",
            "rationale": "Performance requirement",
        }
    ]
    client = _ai_client_returning(decisions)

    mock_adapter = MagicMock()
    with patch("revue.comments.service.WontFixReplyService._ensure_lessons_pr",
               return_value="https://bitbucket.org/ws/repo/pull-requests/99"):
        mock_adapter.get_all_pr_comments.return_value = [
            {
                "id": 100,
                "inline": {"path": "src/main.py", "to": 42},
                "content": {"raw": "**🟡 [MEDIUM] SQL injection risk\n*Security*"},
            },
            {
                "id": 201,
                "parent": {"id": 100},
                "content": {"raw": "This is intentional for performance"},
            },
        ]

        svc = WontFixReplyService(
            repo_path=str(tmp_path),
            ai_client=client,
            repo_owner="ws",
            repo_name="repo",
            platform="bitbucket",
            adapter=mock_adapter,
        )
        svc.process_wont_fix_replies(pr_number=1)

    # Check state was updated (fingerprint "aabbccdd" resolved via reverse map)
    unresolved = store.get_unresolved_fingerprints("bitbucket", 1)
    assert "aabbccdd" not in unresolved


# ---------------------------------------------------------------------------
# TC14: WontFixReplyService — reason_missing → reply posted, state unchanged
# ---------------------------------------------------------------------------

def test_wont_fix_service_reason_missing_posts_reply_no_state_change(tmp_path) -> None:
    """TC14: AC8 — reason_missing posts reply but does NOT update state."""
    from revue.comments.service import WontFixReplyService

    store = PerPRCommentStore(tmp_path)
    store.save_finding(
        platform="bitbucket",
        pr_number=2,
        file_path="src/foo.py",
        fingerprint="deadbeef",
        platform_comment_id="200",
        line_number=10,
        comment_body="Security issue",
    )

    decisions = [
        {
            "fingerprint": "deadbeef",
            "decision": "reason_missing",
            "reply_draft": "Thanks for your reply! Could you explain why this is acceptable?",
        }
    ]
    client = _ai_client_returning(decisions)

    mock_adapter = MagicMock()
    mock_adapter.get_all_pr_comments.return_value = [
        {
            "id": 200,
            "inline": {"path": "src/foo.py", "to": 10},
            "content": {"raw": "**🔴 [HIGH] Security issue\n*Security*"},
        },
        {
            "id": 201,
            "parent": {"id": 200},
            "content": {"raw": "I don't want to fix this"},
        },
    ]

    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=client,
        repo_owner="ws",
        repo_name="repo",
        platform="bitbucket",
        adapter=mock_adapter,
    )
    svc.process_wont_fix_replies(pr_number=2)

    # State must remain unresolved (reason_missing does not mark resolved)
    unresolved = store.get_unresolved_fingerprints("bitbucket", 2)
    assert "deadbeef" in unresolved

    # Reply must have been posted
    mock_adapter.post_reply.assert_called_once()
    reply_body = mock_adapter.post_reply.call_args[0][5]
    assert "explain" in reply_body.lower() or "reason" in reply_body.lower()

    # Thread must NOT be resolved — reason_missing is an open question, not a closed decision
    mock_adapter.resolve_comment.assert_not_called()


# ---------------------------------------------------------------------------
# TC15: WontFixReplyService — not_acknowledged → reply posted, state unchanged
# ---------------------------------------------------------------------------

def test_wont_fix_service_not_acknowledged_posts_reply_no_state_change(tmp_path) -> None:
    """TC15: AC9 — not_acknowledged posts reaffirmation, state unchanged."""
    from revue.comments.service import WontFixReplyService

    store = PerPRCommentStore(tmp_path)
    store.save_finding(
        platform="bitbucket",
        pr_number=3,
        file_path="src/bar.py",
        fingerprint="cafebabe",
        platform_comment_id="300",
        line_number=5,
        comment_body="XSS vulnerability",
    )

    decisions = [
        {
            "fingerprint": "cafebabe",
            "decision": "not_acknowledged",
            "reply_draft": "Just flagging this again — this XSS risk needs attention.",
        }
    ]
    client = _ai_client_returning(decisions)

    mock_adapter = MagicMock()
    mock_adapter.get_all_pr_comments.return_value = [
        {
            "id": 300,
            "inline": {"path": "src/bar.py", "to": 5},
            "content": {"raw": "**🔴 [HIGH] XSS vulnerability\n*Security*"},
        },
        {
            "id": 301,
            "parent": {"id": 300},
            "content": {"raw": "lgtm"},
        },
    ]

    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=client,
        repo_owner="ws",
        repo_name="repo",
        platform="bitbucket",
        adapter=mock_adapter,
    )
    svc.process_wont_fix_replies(pr_number=3)

    # State unchanged (not_acknowledged does not mark resolved)
    unresolved = store.get_unresolved_fingerprints("bitbucket", 3)
    assert "cafebabe" in unresolved

    # Thread must NOT be resolved — not_acknowledged keeps the finding open
    mock_adapter.resolve_comment.assert_not_called()

    # Reply posted
    mock_adapter.post_reply.assert_called_once()


# ---------------------------------------------------------------------------
# TC16: WontFixReplyService — lessons PR URL injected into reply_draft (AC7)
# ---------------------------------------------------------------------------

def test_wont_fix_service_injects_lessons_pr_url_into_reply(tmp_path) -> None:
    """TC16: AC7 — lessons PR is created before reply is posted."""
    from revue.comments.service import WontFixReplyService

    store = PerPRCommentStore(tmp_path)
    store.save_finding(
        platform="bitbucket",
        pr_number=10,
        file_path="src/x.py",
        fingerprint="11223344",
        platform_comment_id="400",
        line_number=1,
        comment_body="Pattern found",
    )

    pr_url = "https://bitbucket.org/ws/repo/pull-requests/55"
    decisions = [
        {
            "fingerprint": "11223344",
            "decision": "allowed_pattern",
            "reply_draft": "Noted. Lessons PR: [LESSONS_PR_URL]",
            "pattern": "Foo pattern",
            "rationale": "Bar rationale",
        }
    ]
    client = _ai_client_returning(decisions)

    call_order: list[str] = []

    def mock_ensure_pr(*args, **kwargs) -> str:
        call_order.append("lessons_pr")
        return pr_url

    mock_adapter = MagicMock()
    with patch("revue.comments.service.WontFixReplyService._ensure_lessons_pr",
               side_effect=mock_ensure_pr):
        mock_adapter.get_all_pr_comments.return_value = [
            {
                "id": 400,
                "inline": {"path": "src/x.py", "to": 1},
                "content": {"raw": "**🟡 [MEDIUM] Pattern found\n*Code Quality*"},
            },
            {
                "id": 401,
                "parent": {"id": 400},
                "content": {"raw": "We decided this is fine because of legacy constraints"},
            },
        ]

        def record_post_reply(*args, **kwargs):
            call_order.append("post_reply")

        mock_adapter.post_reply.side_effect = record_post_reply

        svc = WontFixReplyService(
            repo_path=str(tmp_path),
            ai_client=client,
            repo_owner="ws",
            repo_name="repo",
            platform="bitbucket",
            adapter=mock_adapter,
        )
        svc.process_wont_fix_replies(pr_number=10)

    # AC7: lessons PR must be created before reply is posted
    assert call_order.index("lessons_pr") < call_order.index("post_reply")

    # URL must be injected into the reply
    posted_body = mock_adapter.post_reply.call_args[0][5]
    assert pr_url in posted_body
    assert "[LESSONS_PR_URL]" not in posted_body


# ---------------------------------------------------------------------------
# TC17: WontFixReplyService — AI exception degrades gracefully (AC10)
# ---------------------------------------------------------------------------

def test_wont_fix_service_degrades_gracefully_on_ai_exception(tmp_path) -> None:
    """TC17: AI failure in classify() returns empty ClassificationResult — pipeline continues."""
    from revue.comments.service import WontFixReplyService

    store = PerPRCommentStore(tmp_path)
    store.save_finding(
        platform="bitbucket",
        pr_number=20,
        file_path="src/z.py",
        fingerprint="99aabbcc",
        platform_comment_id="500",
        line_number=1,
        comment_body="Issue",
    )

    client = MagicMock()
    client.complete.side_effect = RuntimeError("AI service down")

    mock_adapter = MagicMock()
    mock_adapter.get_all_pr_comments.return_value = [
        {
            "id": 500,
            "inline": {"path": "src/z.py", "to": 1},
            "content": {"raw": "**🔵 [LOW] Issue\n*Code Quality*"},
        },
        {
            "id": 501,
            "parent": {"id": 500},
            "content": {"raw": "Won't fix — not applicable here"},
        },
    ]

    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=client,
        repo_owner="ws",
        repo_name="repo",
        adapter=mock_adapter,
    )

    # classify() must not raise — it degrades gracefully so the pipeline continues
    result = svc.classify(pr_number=20)
    assert result.decisions == []
    assert result.patterns_to_allow == []

    # process_wont_fix_replies (thin wrapper) must also complete without raising
    svc.process_wont_fix_replies(pr_number=20)


# ---------------------------------------------------------------------------
# TC14: Lessons PR creation fails → YAML block posted, pipeline continues
# ---------------------------------------------------------------------------

def test_wont_fix_service_lessons_pr_failure_posts_yaml_block(tmp_path) -> None:
    """TC14: Lessons PR creation fails → warning logged, YAML block posted, pipeline continues."""
    from revue.comments.service import WontFixReplyService

    store = PerPRCommentStore(tmp_path)
    store.save_finding(
        platform="bitbucket",
        pr_number=30,
        file_path="src/auth.py",
        fingerprint="feedc0de",
        platform_comment_id="600",
        line_number=15,
        comment_body="Hardcoded credential",
    )

    decisions = [
        {
            "fingerprint": "feedc0de",
            "decision": "allowed_pattern",
            "reply_draft": "Noted. Lessons PR: [LESSONS_PR_URL]",
            "pattern": "Hardcoded test credentials",
            "rationale": "These are non-secret test fixtures only",
        }
    ]
    client = _ai_client_returning(decisions)

    mock_adapter = MagicMock()
    with patch(
             "revue.comments.service.WontFixReplyService._ensure_lessons_pr",
             side_effect=RuntimeError("Bitbucket API unavailable"),
         ):
        mock_adapter.get_all_pr_comments.return_value = [
            {
                "id": 600,
                "inline": {"path": "src/auth.py", "to": 15},
                "content": {"raw": "**🔴 [HIGH] Hardcoded credential\n*Security*"},
            },
            {
                "id": 601,
                "parent": {"id": 600},
                "content": {"raw": "These are test-only credentials, intentional"},
            },
        ]

        svc = WontFixReplyService(
            repo_path=str(tmp_path),
            ai_client=client,
            repo_owner="ws",
            repo_name="repo",
            platform="bitbucket",
            adapter=mock_adapter,
        )
        # Must NOT raise — pipeline continues on lessons PR failure
        svc.process_wont_fix_replies(pr_number=30)

    # Reply must have been posted with a YAML block for manual application
    mock_adapter.post_reply.assert_called_once()
    posted_body = mock_adapter.post_reply.call_args[0][5]
    assert "```yaml" in posted_body

    # State must still be updated to wont_fix (finding resolved)
    unresolved = store.get_unresolved_fingerprints("bitbucket", 30)
    assert "feedc0de" not in unresolved


# ---------------------------------------------------------------------------
# TC18: classify() returns correct ClassificationResult (REVUE-112 Phase 2)
# ---------------------------------------------------------------------------

def test_classify_returns_classification_result(tmp_path) -> None:
    """TC18: API-based discovery — two finding comments, two replies.

    classify() fetches all PR comments via get_all_pr_comments(), identifies
    Revue findings by format, groups replies in memory, then calls AI.

    ClassificationResult must have:
    - patterns_to_allow len 1 (allowed_pattern only)
    - patterns_to_disallow len 0
    - state_updates len 1 (only confirmed decisions create state updates)
    - decisions len 2 (all raw AI decisions preserved)
    Fingerprint = comment_id string (stable, no content hashing).
    """
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult
    from revue.core.dedup_consolidator import NovaConsolidator

    api_comments = [
        {
            "id": 101,
            "inline": {"path": "a.py", "to": 5},
            "content": {"raw": "**🟡 [MEDIUM] Finding A\n*Code Quality*"},
        },
        {
            "id": 102,
            "inline": {"path": "b.py", "to": 9},
            "content": {"raw": "**🔵 [LOW] Finding B\n*Performance*"},
        },
        {
            "id": 201,
            "parent": {"id": 101},
            "content": {"raw": "won't fix — legacy code"},
        },
        {
            "id": 202,
            "parent": {"id": 102},
            "content": {"raw": "won't fix — accepted for MVP"},
        },
    ]
    ai_decisions = [
        {
            "fingerprint": "101",
            "decision": "allowed_pattern",
            "pattern": "legacy null-deref in payment handler",
            "rationale": "Developer confirmed intentional — exception handled upstream",
            "reply_draft": "Noted — lessons PR: [LESSONS_PR_URL]",
        },
        {
            "fingerprint": "102",
            "decision": "reason_missing",
            "reply_draft": "Could you explain why this is acceptable?",
        },
    ]

    mock_adapter = MagicMock()
    with patch.object(NovaConsolidator, "analyse_reply_threads", return_value=ai_decisions):
        mock_adapter.get_all_pr_comments.return_value = api_comments
        svc = WontFixReplyService(
            repo_path=str(tmp_path),
            ai_client=MagicMock(),
            repo_owner="ws",
            repo_name="repo",
            adapter=mock_adapter,
        )
        result = svc.classify(42)

    assert isinstance(result, ClassificationResult)
    assert len(result.patterns_to_allow) == 1
    assert result.patterns_to_allow[0]["pattern"] == "legacy null-deref in payment handler"
    assert len(result.patterns_to_disallow) == 0
    assert len(result.state_updates) == 1
    assert result.state_updates[0]["fingerprint"] == "101"
    assert result.state_updates[0]["decision"] == "allowed_pattern"
    assert len(result.decisions) == 2


# ---------------------------------------------------------------------------
# TC19: classify() performs zero side-effects (REVUE-112 Phase 2, AC21)
# ---------------------------------------------------------------------------

def test_classify_performs_no_writes(tmp_path) -> None:
    """TC19: classify() must not call _append_pattern_to_config, post_reply,
    or _ensure_lessons_pr — it is provably side-effect free (AC21)."""
    from revue.comments.service import WontFixReplyService
    from revue.core.dedup_consolidator import NovaConsolidator

    store = PerPRCommentStore(tmp_path)
    store.save_finding("bitbucket", 42, "a.py", "fp0001", "101", 5, "Finding A")

    ai_decisions = [
        {
            "fingerprint": "fp0001",
            "decision": "allowed_pattern",
            "pattern": "legacy bypass",
            "rationale": "intentional",
            "reply_draft": "Noted.",
        }
    ]

    api_comments = [
        {
            "id": 101,
            "inline": {"path": "a.py", "to": 5},
            "content": {"raw": "**🟡 [MEDIUM] Finding A\n*Code Quality*"},
        },
        {
            "id": 201,
            "parent": {"id": 101},
            "content": {"raw": "won't fix — intentional"},
        },
    ]

    mock_adapter = MagicMock()
    with patch.object(NovaConsolidator, "analyse_reply_threads", return_value=ai_decisions):
        mock_adapter.get_all_pr_comments.return_value = api_comments
        svc = WontFixReplyService(
            repo_path=str(tmp_path),
            ai_client=MagicMock(),
            repo_owner="ws",
            repo_name="repo",
            adapter=mock_adapter,
        )
        with patch.object(svc, "_append_pattern_to_config") as mock_append, \
             patch.object(svc, "_ensure_lessons_pr") as mock_pr, \
             patch.object(mock_adapter, "post_reply") as mock_reply:
            svc.classify(42)

    mock_append.assert_not_called()
    mock_pr.assert_not_called()
    mock_reply.assert_not_called()


# ---------------------------------------------------------------------------
# TC20: classify() with no threads → empty ClassificationResult, AI not called
# ---------------------------------------------------------------------------

def test_classify_empty_threads_returns_empty_result(tmp_path) -> None:
    """TC20: No threads with replies → empty ClassificationResult, AI never called."""
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult
    from revue.core.dedup_consolidator import NovaConsolidator

    mock_adapter = MagicMock()
    with patch.object(NovaConsolidator, "analyse_reply_threads") as mock_ai:
        # No finding-format comments → empty threads → AI never called
        mock_adapter.get_all_pr_comments.return_value = []
        svc = WontFixReplyService(
            repo_path=str(tmp_path),
            ai_client=MagicMock(),
            repo_owner="ws",
            repo_name="repo",
            adapter=mock_adapter,
        )
        result = svc.classify(42)

    mock_ai.assert_not_called()
    assert result == ClassificationResult([], [], [], [])


# ---------------------------------------------------------------------------
# TC27: classify() is API-driven — no store reads, no N+1 (REVUE-112)
# ---------------------------------------------------------------------------

def test_classify_api_driven_no_store_no_n_plus_one(tmp_path) -> None:
    """TC27: classify() must use get_all_pr_comments (one call) and never
    call get_comment_replies or read the local store.

    Verifies:
    - Empty store → classify() still finds threads (pure API)
    - get_all_pr_comments called exactly once
    - get_comment_replies never called (N+1 eliminated)
    """
    from revue.comments.service import WontFixReplyService
    from revue.core.dedup_consolidator import NovaConsolidator

    api_comments = [
        {
            "id": 555,
            "inline": {"path": "src/foo.py", "to": 10},
            "content": {"raw": "**🔴 [HIGH] SQL injection risk\n*Security*"},
        },
        {
            "id": 556,
            "parent": {"id": 555},
            "content": {"raw": "won't fix — test DB only, never exposed"},
        },
    ]
    ai_decisions = [
        {
            "fingerprint": "555",
            "decision": "allowed_pattern",
            "pattern": "raw SQL in test harness",
            "rationale": "Test-only, no production exposure",
            "reply_draft": "Noted.",
        }
    ]

    mock_adapter = MagicMock()
    with patch.object(NovaConsolidator, "analyse_reply_threads", return_value=ai_decisions):
        mock_adapter.get_all_pr_comments.return_value = api_comments
        # Empty store — no save_finding calls
        svc = WontFixReplyService(
            repo_path=str(tmp_path),
            ai_client=MagicMock(),
            repo_owner="ws",
            repo_name="repo",
            adapter=mock_adapter,
        )
        result = svc.classify(42)

    # One API call, no per-comment calls
    mock_adapter.get_all_pr_comments.assert_called_once_with("ws", "repo", 42)
    mock_adapter.get_comment_replies.assert_not_called()
    # Found the thread despite empty store
    assert len(result.decisions) == 1
    assert result.decisions[0]["fingerprint"] == "555"


# ---------------------------------------------------------------------------
# TC21: respond() posts replies and creates lessons PR (REVUE-112 Phase 2)
# ---------------------------------------------------------------------------

def test_respond_posts_replies_and_creates_lessons_pr(tmp_path) -> None:
    """TC21: respond() with one allowed_pattern decision calls post_reply and
    _ensure_lessons_pr — all I/O delegated correctly."""
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult

    store = PerPRCommentStore(tmp_path)
    store.save_finding("bitbucket", 42, "a.py", "fp0001", "101", 5, "Finding A")

    result = ClassificationResult(
        patterns_to_allow=[{"pattern": "legacy bypass", "rationale": "intentional"}],
        patterns_to_disallow=[],
        state_updates=[{"fingerprint": "fp0001", "file_path": "a.py", "decision": "allowed_pattern"}],
        decisions=[{
            "fingerprint": "fp0001",
            "decision": "allowed_pattern",
            "pattern": "legacy bypass",
            "rationale": "intentional",
            "reply_draft": "Noted — lessons PR: [LESSONS_PR_URL]",
        }],
    )

    mock_adapter = MagicMock()
    with patch("revue.comments.service.WontFixReplyService._append_pattern_to_config") as mock_append, \
         patch("revue.comments.service.WontFixReplyService._ensure_lessons_pr",
               return_value="https://bitbucket.org/ws/repo/pull-requests/10") as mock_pr:
        # respond() re-fetches threads via get_all_pr_comments to resolve comment IDs.
        # id=101 matches platform_comment_id in store → fingerprint resolved to "fp0001".
        mock_adapter.get_all_pr_comments.return_value = [
            {
                "id": 101,
                "inline": {"path": "a.py", "to": 5},
                "content": {"raw": "**🟡 [MEDIUM] Finding A\n*Code Quality*"},
            },
            {
                "id": 201,
                "parent": {"id": 101},
                "content": {"raw": "won't fix — legacy"},
            },
        ]
        svc = WontFixReplyService(
            repo_path=str(tmp_path),
            ai_client=MagicMock(),
            repo_owner="ws",
            repo_name="repo",
            platform="bitbucket",
            adapter=mock_adapter,
        )
        svc.respond(result, 42)

    mock_append.assert_called_once()
    mock_pr.assert_called_once()
    mock_adapter.post_reply.assert_called_once()
    # Sentinel appended so subsequent runs skip this thread
    posted_body = mock_adapter.post_reply.call_args[0][5]
    assert "[//]: # (revue:ack)" in posted_body
    # Thread resolved in Bitbucket — won't-fix is a closed decision
    mock_adapter.resolve_comment.assert_called_once_with("ws", "repo", 42, "101", None)
    # State updated to wont_fix
    unresolved = store.get_unresolved_fingerprints("bitbucket", 42)
    assert "fp0001" not in unresolved


# ---------------------------------------------------------------------------
# TC23: respond() is idempotent — skips threads where bot already acknowledged
# ---------------------------------------------------------------------------

def test_respond_skips_thread_where_bot_already_acknowledged(tmp_path) -> None:
    """TC23: respond() must not post a second reply if _BOT_ACK_SENTINEL is
    present in the thread's existing replies (idempotency guard)."""
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult

    store = PerPRCommentStore(tmp_path)
    store.save_finding("bitbucket", 42, "a.py", "fp0001", "101", 5, "Finding A")

    result = ClassificationResult(
        patterns_to_allow=[],
        patterns_to_disallow=[],
        state_updates=[],
        decisions=[{
            "fingerprint": "fp0001",
            "decision": "not_acknowledged",
            "reply_draft": "This finding still stands.",
        }],
    )

    mock_adapter = MagicMock()
    mock_adapter.get_all_pr_comments.return_value = [
        {
            "id": 101,
            "inline": {"path": "a.py", "to": 5},
            "content": {"raw": "**🟡 [MEDIUM] Finding A\n*Code Quality*"},
        },
        {
            "id": 201,
            "parent": {"id": 101},
            "content": {"raw": "won't fix — legacy"},
        },
        {
            "id": 202,
            "parent": {"id": 101},
            # Bot already replied in a previous run — sentinel present
            "content": {"raw": "This finding still stands.\n\n[//]: # (revue:ack)"},
        },
    ]
    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="ws",
        repo_name="repo",
        adapter=mock_adapter,
    )
    svc.respond(result, 42)

    # Bot must not post again — idempotency
    mock_adapter.post_reply.assert_not_called()
    mock_adapter.resolve_comment.assert_not_called()


# ---------------------------------------------------------------------------
# TC23b: respond() is idempotent — skips threads Nova classified as already_handled
# ---------------------------------------------------------------------------

def test_respond_skips_already_handled_decision(tmp_path) -> None:
    """TC23b: respond() must not post a second reply when Nova returns
    already_handled — the AI detected the bot already replied in a prior cycle."""
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult

    store = PerPRCommentStore(tmp_path)
    store.save_finding("bitbucket", 42, "a.py", "fp0001", "101", 5, "Finding A")

    # Nova classified this as already_handled — bot already acknowledged
    result = ClassificationResult(
        patterns_to_allow=[],
        patterns_to_disallow=[],
        state_updates=[],
        decisions=[{
            "fingerprint": "fp0001",
            "decision": "already_handled",
            "reply_draft": "",
        }],
    )

    mock_adapter = MagicMock()
    mock_adapter.get_all_pr_comments.return_value = [
        {
            "id": 101,
            "inline": {"path": "a.py", "to": 5},
            "content": {"raw": "**🟡 [MEDIUM] Finding A\n*Code Quality*"},
        },
        {
            "id": 201,
            "parent": {"id": 101},
            "content": {"raw": "won't fix — legacy"},
        },
        {
            "id": 202,
            "parent": {"id": 101},
            # Bot already replied in a previous run — sentinel present
            "content": {"raw": "This finding still stands.\n\n[//]: # (revue:ack)"},
        },
    ]
    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="ws",
        repo_name="repo",
        adapter=mock_adapter,
    )
    svc.respond(result, 42)

    # already_handled → no new reply, no resolve
    mock_adapter.post_reply.assert_not_called()
    mock_adapter.resolve_comment.assert_not_called()


# ---------------------------------------------------------------------------
# TC25: respond() posts reply and resolves thread for acknowledged_fixed
# ---------------------------------------------------------------------------

def test_respond_acknowledged_fixed_posts_reply_and_resolves(tmp_path) -> None:
    """TC25: when Nova classifies a reply as acknowledged_fixed, respond() must
    post a reply and resolve the thread — the developer indicated they fixed it."""
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult

    store = PerPRCommentStore(tmp_path)
    store.save_finding("bitbucket", 42, "a.py", "fp0001", "101", 5, "Finding A")

    result = ClassificationResult(
        patterns_to_allow=[],
        patterns_to_disallow=[],
        state_updates=[{"fingerprint": "fp0001", "file_path": "a.py", "decision": "acknowledged_fixed"}],
        decisions=[{
            "fingerprint": "fp0001",
            "decision": "acknowledged_fixed",
            "reply_draft": "Thanks — fix confirmed. Resolving this thread.",
        }],
    )

    mock_adapter = MagicMock()
    mock_adapter.get_all_pr_comments.return_value = [
        {
            "id": 101,
            "inline": {"path": "a.py", "to": 5},
            "content": {"raw": "**🟡 [MEDIUM] Finding A\n*Code Quality*"},
        },
        {
            "id": 201,
            "parent": {"id": 101},
            "content": {"raw": "Fixed in d4668d3"},
        },
    ]
    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="ws",
        repo_name="repo",
        platform="bitbucket",
        adapter=mock_adapter,
    )
    svc.respond(result, 42)

    mock_adapter.post_reply.assert_called_once()
    mock_adapter.resolve_comment.assert_called_once_with("ws", "repo", 42, "101", None)
    unresolved = store.get_unresolved_fingerprints("bitbucket", 42)
    assert "fp0001" not in unresolved


# ---------------------------------------------------------------------------
# TC24: classify() skips already-resolved Bitbucket threads
# ---------------------------------------------------------------------------

def test_classify_skips_resolved_threads(tmp_path) -> None:
    """TC24: threads with a non-null 'resolution' field must be excluded from
    classify() — they are already closed and need no further action."""
    from revue.comments.service import WontFixReplyService

    decisions = [{"fingerprint": "200", "decision": "not_acknowledged", "reply_draft": "Still stands."}]
    client = _ai_client_returning(decisions)

    mock_adapter = MagicMock()
    mock_adapter.get_all_pr_comments.return_value = [
        # Thread A — already resolved, must be skipped
        {
            "id": 100,
            "inline": {"path": "a.py", "to": 5},
            "content": {"raw": "**🟡 [MEDIUM] Resolved finding\n*Code Quality*"},
            "resolution": {"type": "comment_resolution"},  # resolved
        },
        {
            "id": 101,
            "parent": {"id": 100},
            "content": {"raw": "Fixed."},
        },
        # Thread B — still open, must be processed
        {
            "id": 200,
            "inline": {"path": "b.py", "to": 10},
            "content": {"raw": "**🔴 [HIGH] Open finding\n*Security*"},
            # no resolution field
        },
        {
            "id": 201,
            "parent": {"id": 200},
            "content": {"raw": "Won't fix — acceptable risk."},
        },
    ]
    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=client,
        repo_owner="ws",
        repo_name="repo",
        adapter=mock_adapter,
    )
    result = svc.classify(pr_number=1)

    # Only thread B (id=200) should have reached the AI — thread A was resolved
    threads_passed = client.complete.call_args[0][0]
    prompt_text = str(threads_passed)
    assert "Open finding" in prompt_text
    assert "Resolved finding" not in prompt_text
    assert len(result.decisions) == 1


# ---------------------------------------------------------------------------
# TC22: process_wont_fix_replies is a thin wrapper (REVUE-112 Phase 2, AC14)
# ---------------------------------------------------------------------------

def test_process_wont_fix_replies_is_thin_wrapper(tmp_path) -> None:
    """TC22: process_wont_fix_replies must call classify then respond in sequence."""
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult

    empty = ClassificationResult([], [], [], [])
    call_order: list[str] = []

    mock_adapter = MagicMock()
    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="ws",
        repo_name="repo",
        adapter=mock_adapter,
    )

    with patch.object(svc, "classify",
                      side_effect=lambda n: (call_order.append("classify"), empty)[1]) as mock_classify, \
         patch.object(svc, "respond",
                      side_effect=lambda r, n: call_order.append("respond")) as mock_respond:
        svc.process_wont_fix_replies(42)

    mock_classify.assert_called_once_with(42)
    mock_respond.assert_called_once_with(empty, 42)
    assert call_order == ["classify", "respond"]


# ---------------------------------------------------------------------------
# TC26: respond() — acknowledged_deferred posts reply, resolves thread
# ---------------------------------------------------------------------------

def test_respond_acknowledged_deferred_posts_reply_and_resolves(tmp_path) -> None:
    """TC26: acknowledged_deferred → post confirmation reply + resolve thread.
    No lessons PR created. State remains unresolved in store (not a permanent decision)."""
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult

    store = PerPRCommentStore(tmp_path)
    store.save_finding("bitbucket", 42, "src/service.py", "fp0099", "101", 10, "N+1 query")

    result = ClassificationResult(
        patterns_to_allow=[],
        patterns_to_disallow=[],
        state_updates=[],
        decisions=[{
            "fingerprint": "fp0099",
            "decision": "acknowledged_deferred",
            "reply_draft": "Got it — tracked as a deferred fix.",
        }],
    )

    mock_adapter = MagicMock()
    mock_adapter.get_all_pr_comments.return_value = [
        {
            "id": 101,
            "inline": {"path": "src/service.py", "to": 10},
            "content": {"raw": "**🟡 [MEDIUM] N+1 query"},
        },
        {
            "id": 201,
            "parent": {"id": 101},
            "content": {"raw": "Not intentional — tracked, will fix after this PR."},
        },
    ]
    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="ws",
        repo_name="repo",
        platform="bitbucket",
        adapter=mock_adapter,
    )
    svc.respond(result, 42)

    # Must post the confirmation reply with sentinel appended
    mock_adapter.post_reply.assert_called_once()
    posted_body = mock_adapter.post_reply.call_args[0][5]
    assert "Got it" in posted_body
    assert "[//]: # (revue:ack)" in posted_body

    # Must resolve the thread — deferred acknowledgement closes the discussion
    mock_adapter.resolve_comment.assert_called_once()

    # No lessons PR created — acknowledged_deferred is not a permanent decision
    unresolved = store.get_unresolved_fingerprints("bitbucket", 42)
    assert "fp0099" in unresolved  # state remains unresolved in store (no wont_fix)


# ---------------------------------------------------------------------------
# REVUE-119 T3: WontFixReplyService platform param
# ---------------------------------------------------------------------------

def test_wont_fix_service_platform_default_is_github(tmp_path) -> None:
    """T3.1: WontFixReplyService without explicit platform= defaults to 'github'."""
    from revue.comments.service import WontFixReplyService

    mock_adapter = MagicMock()
    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="ws",
        repo_name="repo",
        adapter=mock_adapter,
    )
    assert svc._platform == "github"


def test_wont_fix_service_platform_param_stored(tmp_path) -> None:
    """T3.1: platform kwarg is stored on the mock_adapter."""
    from revue.comments.service import WontFixReplyService
    from revue.comments.platform_adapter import GitHubAdapter

    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="owner",
        repo_name="repo",
        platform="github",
        adapter=GitHubAdapter("ghp_tok"),
    )
    assert svc._platform == "github"


def test_collect_threads_uses_platform_for_store_lookup(tmp_path) -> None:
    """T3.2: _collect_threads_with_replies calls get_unresolved_fingerprints
    with the service's platform, not hardcoded 'bitbucket'."""
    from revue.comments.service import WontFixReplyService
    from revue.comments.platform_adapter import GitHubAdapter

    # Provide one Revue finding comment so the method reaches the store lookup
    finding_comment = {
        "id": 101,
        "inline": {"path": "src/db.py", "to": 5},
        "parent": None,
        "content": {"raw": "**🔴 [HIGH] SQL injection risk in query builder"},
        "resolution": None,
    }
    mock_adapter = MagicMock(spec=GitHubAdapter)
    mock_adapter.get_all_pr_comments.return_value = [finding_comment]

    mock_store = MagicMock()
    mock_store.get_unresolved_fingerprints.return_value = {}

    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="owner",
        repo_name="repo",
        platform="github",
        adapter=mock_adapter,
    )
    svc._store = mock_store

    svc._collect_threads_with_replies(pr_number=4)

    # Store must be queried with "github", not "bitbucket"
    mock_store.get_unresolved_fingerprints.assert_called_once_with("github", 4)


# ---------------------------------------------------------------------------
# REVUE-119 bugfix: respond() must use self._platform in mark_resolved
# ---------------------------------------------------------------------------

def test_respond_marks_resolved_with_correct_platform(tmp_path) -> None:
    """respond() must call mark_resolved with self._platform, not hardcoded 'bitbucket'.

    Regression test for the bug found in code review: service.py:599 was
    hardcoded to 'bitbucket', causing GitHub decisions to target the wrong
    platform bucket in the store.
    """
    from revue.comments.service import WontFixReplyService
    from revue.comments.platform_adapter import GitHubAdapter

    store = PerPRCommentStore(tmp_path)
    store.save_finding(
        platform="github",
        pr_number=7,
        file_path="src/auth.py",
        fingerprint="ghfp001",
        platform_comment_id="701",
        line_number=12,
        comment_body="Hardcoded secret",
    )

    decisions = [
        {
            "fingerprint": "ghfp001",
            "decision": "allowed_pattern",
            "reply_draft": "Noted. Lessons PR: [LESSONS_PR_URL]",
            "pattern": "Test credentials only",
            "rationale": "Non-production fixture",
        }
    ]
    client = _ai_client_returning(decisions)

    mock_adapter = MagicMock(spec=GitHubAdapter)
    mock_adapter.get_all_pr_comments.return_value = [
        {
            "id": 701,
            "inline": {"path": "src/auth.py", "to": 12},
            "parent": None,
            "content": {"raw": "**🔴 [HIGH] Hardcoded secret\n*Security*"},
            "resolution": None,
        },
        {
            "id": 702,
            "inline": {"path": "src/auth.py", "to": 12},
            "parent": {"id": 701},
            "content": {"raw": "Won't fix — these are test-only credentials"},
            "resolution": None,
        },
    ]

    with patch(
        "revue.comments.service.WontFixReplyService._ensure_lessons_pr",
        return_value="https://github.com/owner/repo/pull/99",
    ):
        svc = WontFixReplyService(
            repo_path=str(tmp_path),
            ai_client=client,
            repo_owner="owner",
            repo_name="repo",
            platform="github",
            adapter=mock_adapter,
        )
        svc.process_wont_fix_replies(pr_number=7)

    # The store entry must be resolved under "github", not "bitbucket"
    unresolved_github = store.get_unresolved_fingerprints("github", 7)
    assert "ghfp001" not in unresolved_github, (
        "respond() must use self._platform ('github') not 'bitbucket' in mark_resolved"
    )


# ---------------------------------------------------------------------------
# Bugfix: apply_state_updates() must use self._platform, not hardcoded 'bitbucket'
# ---------------------------------------------------------------------------

def test_apply_state_updates_uses_correct_platform(tmp_path) -> None:
    """apply_state_updates() must write to self._platform bucket, not 'bitbucket'.

    Regression test: the bug caused GitHub/GitLab decisions to be stored under
    the 'bitbucket' key so the dedup layer never saw them as resolved on the next run.
    """
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult
    from revue.comments.platform_adapter import GitHubAdapter

    store = PerPRCommentStore(tmp_path)
    store.save_finding(
        platform="github",
        pr_number=5,
        file_path="app.py",
        fingerprint="fp_gh_001",
        platform_comment_id="501",
        line_number=10,
        comment_body="some finding",
    )

    result = ClassificationResult(
        patterns_to_allow=[{"pattern": "intentional", "rationale": "by design"}],
        patterns_to_disallow=[],
        state_updates=[{"fingerprint": "fp_gh_001", "file_path": "app.py", "decision": "allowed_pattern"}],
        decisions=[],
    )

    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="owner",
        repo_name="repo",
        platform="github",
        adapter=MagicMock(spec=GitHubAdapter),
    )
    svc.apply_state_updates(result, pr_number=5)

    unresolved_github = store.get_unresolved_fingerprints("github", 5)
    assert "fp_gh_001" not in unresolved_github, (
        "apply_state_updates() must use self._platform ('github'), not hardcoded 'bitbucket'"
    )
    # Confirm it was NOT written under 'bitbucket' either
    unresolved_bitbucket = store.get_unresolved_fingerprints("bitbucket", 5)
    assert "fp_gh_001" not in unresolved_bitbucket


# ---------------------------------------------------------------------------
# Bug fix: thread_id forwarded to post_reply / resolve_comment (GitLab)
# ---------------------------------------------------------------------------

def test_collect_threads_includes_thread_id(tmp_path) -> None:
    """_collect_threads_with_replies must include thread_id from the comment dict
    so that GitLab's discussion ID is available for post_reply / resolve_comment."""
    from revue.comments.service import WontFixReplyService
    from revue.comments.platform_adapter import GitLabAdapter

    mock_adapter = MagicMock(spec=GitLabAdapter)
    mock_adapter.get_all_pr_comments.return_value = [
        {
            "id": 101,
            "thread_id": "disc-abc123",
            "inline": {"path": "src/foo.py", "to": 5},
            "parent": None,
            "content": {"raw": "**🟡 [MEDIUM] Some finding\n*Code Quality*"},
            "resolution": None,
        },
        {
            "id": 201,
            "thread_id": "disc-abc123",
            "parent": {"id": 101},
            "content": {"raw": "Fixed in this PR"},
            "resolution": None,
        },
    ]

    mock_store = MagicMock()
    mock_store.get_unresolved_fingerprints.return_value = {}

    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="owner",
        repo_name="repo",
        platform="gitlab",
        adapter=mock_adapter,
    )
    svc._store = mock_store

    threads = svc._collect_threads_with_replies(pr_number=4)

    assert len(threads) == 1
    assert threads[0]["thread_id"] == "disc-abc123"


def test_respond_passes_thread_id_to_gitlab_post_reply(tmp_path) -> None:
    """respond() must forward thread_id (discussion ID) to post_reply — not None."""
    from revue.comments.service import WontFixReplyService
    from revue.comments.platform_adapter import GitLabAdapter
    from revue.core.models import ClassificationResult

    result = ClassificationResult(
        patterns_to_allow=[],
        patterns_to_disallow=[],
        state_updates=[],
        decisions=[{
            "fingerprint": "101",
            "decision": "acknowledged_fixed",
            "reply_draft": "Fixed — resolving.",
        }],
    )

    mock_adapter = MagicMock(spec=GitLabAdapter)
    mock_adapter.get_all_pr_comments.return_value = [
        {
            "id": 101,
            "thread_id": "disc-abc123",
            "inline": {"path": "src/foo.py", "to": 5},
            "parent": None,
            "content": {"raw": "**🟡 [MEDIUM] Some finding\n*Code Quality*"},
            "resolution": None,
        },
        {
            "id": 201,
            "thread_id": "disc-abc123",
            "parent": {"id": 101},
            "content": {"raw": "Fixed in this PR"},
            "resolution": None,
        },
    ]
    mock_adapter.resolve_comment.return_value = True

    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="owner",
        repo_name="repo",
        platform="gitlab",
        adapter=mock_adapter,
    )

    svc.respond(result, 4)

    mock_adapter.post_reply.assert_called_once()
    # thread_id is the 5th positional arg (index 4)
    call_args = mock_adapter.post_reply.call_args[0]
    assert call_args[4] == "disc-abc123", f"Expected thread_id='disc-abc123', got {call_args[4]!r}"


def test_respond_passes_thread_id_to_gitlab_resolve_comment(tmp_path) -> None:
    """respond() must forward thread_id (discussion ID) to resolve_comment — not omit it."""
    from revue.comments.service import WontFixReplyService
    from revue.comments.platform_adapter import GitLabAdapter
    from revue.core.models import ClassificationResult

    result = ClassificationResult(
        patterns_to_allow=[],
        patterns_to_disallow=[],
        state_updates=[],
        decisions=[{
            "fingerprint": "101",
            "decision": "acknowledged_fixed",
            "reply_draft": "Fixed — resolving.",
        }],
    )

    mock_adapter = MagicMock(spec=GitLabAdapter)
    mock_adapter.get_all_pr_comments.return_value = [
        {
            "id": 101,
            "thread_id": "disc-abc123",
            "inline": {"path": "src/foo.py", "to": 5},
            "parent": None,
            "content": {"raw": "**🟡 [MEDIUM] Some finding\n*Code Quality*"},
            "resolution": None,
        },
        {
            "id": 201,
            "thread_id": "disc-abc123",
            "parent": {"id": 101},
            "content": {"raw": "Fixed in this PR"},
            "resolution": None,
        },
    ]
    mock_adapter.resolve_comment.return_value = True

    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="owner",
        repo_name="repo",
        platform="gitlab",
        adapter=mock_adapter,
    )

    svc.respond(result, 4)

    mock_adapter.resolve_comment.assert_called_once()
    # thread_id is the 5th positional arg (index 4)
    call_args = mock_adapter.resolve_comment.call_args[0]
    assert call_args[4] == "disc-abc123", f"Expected thread_id='disc-abc123', got {call_args[4]!r}"


# ---------------------------------------------------------------------------
# _ensure_lessons_pr delegates to adapter.ensure_lessons_pr (REVUE-112/120)
# ---------------------------------------------------------------------------

def test_ensure_lessons_pr_delegates_to_adapter(tmp_path) -> None:
    """_ensure_lessons_pr must call adapter.ensure_lessons_pr with correct args."""
    from revue.comments.service import WontFixReplyService
    from revue.comments.platform_adapter import GitLabAdapter

    mock_adapter = MagicMock(spec=GitLabAdapter)
    mock_adapter.ensure_lessons_pr.return_value = "https://gitlab.com/o/r/-/merge_requests/5"
    mock_adapter.get_pr_template.return_value = None

    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="owner",
        repo_name="repo",
        platform="gitlab",
        adapter=mock_adapter,
    )

    result = svc._ensure_lessons_pr(4, "some pattern", "some rationale", "disallowed_pattern")

    assert result == "https://gitlab.com/o/r/-/merge_requests/5"
    mock_adapter.ensure_lessons_pr.assert_called_once()
    call_kwargs = mock_adapter.ensure_lessons_pr.call_args.kwargs
    assert call_kwargs["repo_owner"] == "owner"
    assert call_kwargs["repo_name"] == "repo"
    assert call_kwargs["pr_number"] == 4
    assert call_kwargs["branch"] == "chore/revue-lessons-4"


def test_respond_gitlab_disallowed_pattern_calls_adapter_ensure_lessons_pr(tmp_path) -> None:
    """respond() with GitLab + disallowed_pattern must call adapter.ensure_lessons_pr and
    include the returned MR URL in the reply — not fall back to YAML block."""
    from revue.comments.service import WontFixReplyService
    from revue.comments.platform_adapter import GitLabAdapter
    from revue.core.models import ClassificationResult

    result = ClassificationResult(
        patterns_to_allow=[],
        patterns_to_disallow=[{"pattern": "bad-pattern", "rationale": "disallow this"}],
        state_updates=[],
        decisions=[{
            "fingerprint": "101",
            "decision": "disallowed_pattern",
            "pattern": "bad-pattern",
            "rationale": "disallow this",
            "reply_draft": "Noted. PR: [LESSONS_PR_URL]",
        }],
    )

    mock_adapter = MagicMock(spec=GitLabAdapter)
    mock_adapter.get_all_pr_comments.return_value = [
        {
            "id": 101,
            "thread_id": "disc-xyz",
            "inline": {"path": "src/foo.py", "to": 5},
            "parent": None,
            "content": {"raw": "**🟡 [MEDIUM] Some finding\n*Code Quality*"},
            "resolution": None,
        },
        {
            "id": 201,
            "thread_id": "disc-xyz",
            "parent": {"id": 101},
            "content": {"raw": "won't fix — internal use only"},
            "resolution": None,
        },
    ]
    mock_adapter.resolve_comment.return_value = False
    mock_adapter.get_pr_template.return_value = None
    mock_adapter.ensure_lessons_pr.return_value = "https://gitlab.com/o/r/-/merge_requests/5"

    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="owner",
        repo_name="repo",
        platform="gitlab",
        adapter=mock_adapter,
    )

    svc.respond(result, 4)

    mock_adapter.ensure_lessons_pr.assert_called_once()
    mock_adapter.post_reply.assert_called_once()
    posted_body = mock_adapter.post_reply.call_args[0][5]
    # Reply should contain the MR URL, not a YAML fallback block
    assert "https://gitlab.com/o/r/-/merge_requests/5" in posted_body
    assert "```yaml" not in posted_body


# ---------------------------------------------------------------------------
# Bug fix: two allowed_pattern decisions in one respond() call (REVUE-112)
# _commit_pattern_to_lessons_branch was removed; both patterns must delegate
# to adapter.ensure_lessons_pr — the adapter handles idempotency.
# ---------------------------------------------------------------------------

def test_respond_two_allowed_patterns_both_delegate_to_adapter(tmp_path) -> None:
    """Two allowed_pattern decisions in one respond() call must each call
    adapter.ensure_lessons_pr — no AttributeError from removed method."""
    from revue.comments.service import WontFixReplyService
    from revue.comments.platform_adapter import GitLabAdapter
    from revue.core.models import ClassificationResult

    result = ClassificationResult(
        patterns_to_allow=[
            {"pattern": "pattern-a", "rationale": "reason a"},
            {"pattern": "pattern-b", "rationale": "reason b"},
        ],
        patterns_to_disallow=[],
        state_updates=[],
        decisions=[
            {
                "fingerprint": "101",
                "decision": "allowed_pattern",
                "pattern": "pattern-a",
                "rationale": "reason a",
                "reply_draft": "Noted. PR: [LESSONS_PR_URL]",
            },
            {
                "fingerprint": "102",
                "decision": "allowed_pattern",
                "pattern": "pattern-b",
                "rationale": "reason b",
                "reply_draft": "Noted. PR: [LESSONS_PR_URL]",
            },
        ],
    )

    mock_adapter = MagicMock(spec=GitLabAdapter)
    mock_adapter.get_all_pr_comments.return_value = [
        {
            "id": 101, "thread_id": "disc-a", "inline": {"path": "a.py", "to": 1},
            "parent": None, "content": {"raw": "**🟡 [MEDIUM] Finding A\n*Code Quality*"},
            "resolution": None,
        },
        {
            "id": 201, "thread_id": "disc-a", "parent": {"id": 101},
            "content": {"raw": "fine, won't fix"}, "resolution": None,
        },
        {
            "id": 102, "thread_id": "disc-b", "inline": {"path": "b.py", "to": 2},
            "parent": None, "content": {"raw": "**🔵 [LOW] Finding B\n*Code Quality*"},
            "resolution": None,
        },
        {
            "id": 202, "thread_id": "disc-b", "parent": {"id": 102},
            "content": {"raw": "also fine"}, "resolution": None,
        },
    ]
    mock_adapter.resolve_comment.return_value = True
    mock_adapter.get_pr_template.return_value = None
    mock_adapter.ensure_lessons_pr.return_value = "https://gitlab.com/o/r/-/merge_requests/5"

    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="owner",
        repo_name="repo",
        platform="gitlab",
        adapter=mock_adapter,
    )

    svc.respond(result, 4)

    # Both patterns must reach the adapter — no AttributeError from dead method
    assert mock_adapter.ensure_lessons_pr.call_count == 2
    assert mock_adapter.post_reply.call_count == 2


# ---------------------------------------------------------------------------
# Bug fix: sentinel detection uses last-reply only (REVUE-112)
# ---------------------------------------------------------------------------

_SENTINEL = "[//]: # (revue:ack)"
_FINDING_BODY_S = "**🟡 [MEDIUM] Finding A\n*Code Quality*"


def _c(cid, parent_id=None, body=""):
    """Build a minimal comment dict for sentinel tests."""
    return {
        "id": cid,
        "thread_id": f"disc-{cid}",
        "inline": {"path": "a.py", "to": 5},
        "parent": {"id": parent_id} if parent_id else None,
        "content": {"raw": body},
        "resolution": None,
    }


def _sentinel_svc(tmp_path, adapter, platform="bitbucket"):
    from revue.comments.service import WontFixReplyService
    return WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="owner",
        repo_name="repo",
        platform=platform,
        adapter=adapter,
    )


def test_collect_threads_already_handled_when_sentinel_is_last_reply(tmp_path) -> None:
    """Sentinel in last reply → already_handled=True. classify() must not send to AI."""
    from revue.comments.platform_adapter import BitbucketAdapter

    mock_adapter = MagicMock(spec=BitbucketAdapter)
    mock_adapter.get_all_pr_comments.return_value = [
        _c(101, body=_FINDING_BODY_S),
        _c(201, parent_id=101, body="won't fix — pre-existing"),
        _c(202, parent_id=101, body=f"Could you clarify?\n\n{_SENTINEL}"),
    ]

    svc = _sentinel_svc(tmp_path, mock_adapter)
    threads = svc._collect_threads_with_replies(1)

    assert len(threads) == 1
    assert threads[0]["already_handled"] is True


def test_collect_threads_not_already_handled_when_dev_reply_after_sentinel(tmp_path) -> None:
    """Developer replies after sentinel → already_handled=False. Thread needs re-evaluation."""
    from revue.comments.platform_adapter import BitbucketAdapter

    mock_adapter = MagicMock(spec=BitbucketAdapter)
    mock_adapter.get_all_pr_comments.return_value = [
        _c(101, body=_FINDING_BODY_S),
        _c(201, parent_id=101, body="won't fix — unclear"),
        _c(202, parent_id=101, body=f"Please clarify.\n\n{_SENTINEL}"),
        _c(203, parent_id=101, body="It's a rate-limit delay — pre-existing, tracked in REVUE-140."),
    ]

    svc = _sentinel_svc(tmp_path, mock_adapter)
    threads = svc._collect_threads_with_replies(1)

    assert len(threads) == 1
    assert threads[0]["already_handled"] is False


def test_classify_skips_ai_for_sentinel_last_threads(tmp_path) -> None:
    """classify() must not call analyse_reply_threads for already-handled threads.
    Returns already_handled decisions without an AI call."""
    from revue.comments.platform_adapter import BitbucketAdapter

    mock_adapter = MagicMock(spec=BitbucketAdapter)
    mock_adapter.get_all_pr_comments.return_value = [
        _c(101, body=_FINDING_BODY_S),
        _c(201, parent_id=101, body="won't fix"),
        _c(202, parent_id=101, body=f"Please clarify.\n\n{_SENTINEL}"),
    ]

    svc = _sentinel_svc(tmp_path, mock_adapter)

    with patch("revue.core.dedup_consolidator.NovaConsolidator.analyse_reply_threads") as mock_ai_call:
        result = svc.classify(1)

    mock_ai_call.assert_not_called()
    assert len(result.decisions) == 1
    assert result.decisions[0]["decision"] == "already_handled"
    assert result.decisions[0]["fingerprint"] == "101"


def test_respond_processes_thread_when_dev_reply_after_sentinel(tmp_path) -> None:
    """When developer replies after the bot sentinel, respond() must process the
    decision instead of skipping due to the old sentinel."""
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult

    result = ClassificationResult(
        patterns_to_allow=[{"pattern": "rate-limit sleep", "rationale": "pre-existing"}],
        patterns_to_disallow=[],
        state_updates=[{"fingerprint": "101", "file_path": "a.py", "decision": "allowed_pattern"}],
        decisions=[{
            "fingerprint": "101",
            "decision": "allowed_pattern",
            "pattern": "rate-limit sleep",
            "rationale": "pre-existing, tracked",
            "reply_draft": "Noted — lessons PR: [LESSONS_PR_URL]",
        }],
    )

    mock_adapter = MagicMock()
    mock_adapter.get_all_pr_comments.return_value = [
        _c(101, body=_FINDING_BODY_S),
        _c(201, parent_id=101, body="won't fix — unclear"),
        _c(202, parent_id=101, body=f"Please clarify.\n\n{_SENTINEL}"),
        # Developer provides reason AFTER the bot's sentinel reply
        _c(203, parent_id=101, body="It's a rate-limit delay — pre-existing, tracked in REVUE-140."),
    ]
    mock_adapter.ensure_lessons_pr.return_value = "https://bitbucket.org/ws/repo/pull-requests/9"
    mock_adapter.get_pr_template.return_value = None

    with patch("revue.comments.service.WontFixReplyService._append_pattern_to_config"):
        svc = WontFixReplyService(
            repo_path=str(tmp_path),
            ai_client=MagicMock(),
            repo_owner="owner",
            repo_name="repo",
            adapter=mock_adapter,
        )
        svc.respond(result, 1)

    # Must have posted — old sentinel does not block when it's not the last reply
    mock_adapter.post_reply.assert_called_once()
    posted_body = mock_adapter.post_reply.call_args[0][5]
    assert "https://bitbucket.org/ws/repo/pull-requests/9" in posted_body


def test_respond_reason_missing_posts_fallback_when_reply_draft_empty(tmp_path) -> None:
    """reason_missing with empty reply_draft must post a human-readable message,
    not just the invisible sentinel comment."""
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult

    result = ClassificationResult(
        patterns_to_allow=[],
        patterns_to_disallow=[],
        state_updates=[],
        decisions=[{
            "fingerprint": "101",
            "decision": "reason_missing",
            "reply_draft": "",
        }],
    )

    mock_adapter = MagicMock()
    mock_adapter.get_all_pr_comments.return_value = [
        _c(101, body=_FINDING_BODY_S),
        _c(201, parent_id=101, body="won't fix"),
    ]

    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="owner",
        repo_name="repo",
        adapter=mock_adapter,
    )
    svc.respond(result, 1)

    mock_adapter.post_reply.assert_called_once()
    posted_body = mock_adapter.post_reply.call_args[0][5]
    # Must contain a human-readable message, not just the sentinel.
    # reason_missing now uses revue:rm sentinel (not revue:ack).
    assert _RM_SENTINEL in posted_body
    assert posted_body.strip() != _RM_SENTINEL  # more than just the sentinel


# ---------------------------------------------------------------------------
# Resolved-sentinel (revue:resolved) — terminal decision recovery
# ---------------------------------------------------------------------------

_RESOLVED_SENTINEL = "[//]: # (revue:resolved)"


def test_collect_threads_already_terminal_when_resolved_sentinel_is_last(tmp_path) -> None:
    """revue:resolved in last reply → already_terminal=True (terminal decision, thread should be resolved)."""
    mock_adapter = MagicMock()
    mock_adapter.get_all_pr_comments.return_value = [
        _c(101, body=_FINDING_BODY_S),
        _c(201, parent_id=101, body="won't fix — pre-existing"),
        _c(202, parent_id=101, body=f"Won't fix: pre-existing pattern.\n\n{_SENTINEL}\n{_RESOLVED_SENTINEL}"),
    ]

    svc = _sentinel_svc(tmp_path, mock_adapter)
    threads = svc._collect_threads_with_replies(1)

    assert len(threads) == 1
    assert threads[0]["already_handled"] is True
    assert threads[0]["already_terminal"] is True


def test_collect_threads_not_terminal_for_ack_only_last_reply(tmp_path) -> None:
    """revue:ack only (no revue:resolved) → already_terminal=False (still awaiting developer)."""
    mock_adapter = MagicMock()
    mock_adapter.get_all_pr_comments.return_value = [
        _c(101, body=_FINDING_BODY_S),
        _c(201, parent_id=101, body="won't fix"),
        _c(202, parent_id=101, body=f"Please provide a reason.\n\n{_SENTINEL}"),
    ]

    svc = _sentinel_svc(tmp_path, mock_adapter)
    threads = svc._collect_threads_with_replies(1)

    assert len(threads) == 1
    assert threads[0]["already_handled"] is True
    assert threads[0]["already_terminal"] is False


def test_respond_resolves_thread_when_already_terminal(tmp_path) -> None:
    """already_handled + already_terminal → resolve_comment called (recovery for terminal decisions)."""
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult

    result = ClassificationResult(
        patterns_to_allow=[],
        patterns_to_disallow=[],
        state_updates=[],
        decisions=[{
            "fingerprint": "101",
            "decision": "already_handled",
            "reply_draft": "",
        }],
    )

    mock_adapter = MagicMock()
    mock_adapter.get_all_pr_comments.return_value = [
        _c(101, body=_FINDING_BODY_S),
        _c(201, parent_id=101, body="won't fix — pre-existing"),
        _c(202, parent_id=101, body=f"Won't fix: tracked.\n\n{_SENTINEL}\n{_RESOLVED_SENTINEL}"),
    ]

    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="owner",
        repo_name="repo",
        adapter=mock_adapter,
    )
    svc.respond(result, 1)

    mock_adapter.resolve_comment.assert_called_once()
    mock_adapter.post_reply.assert_not_called()


def test_respond_does_not_resolve_ack_only_already_handled(tmp_path) -> None:
    """already_handled without revue:resolved → no resolve_comment (still awaiting reason)."""
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult

    result = ClassificationResult(
        patterns_to_allow=[],
        patterns_to_disallow=[],
        state_updates=[],
        decisions=[{
            "fingerprint": "101",
            "decision": "already_handled",
            "reply_draft": "",
        }],
    )

    mock_adapter = MagicMock()
    mock_adapter.get_all_pr_comments.return_value = [
        _c(101, body=_FINDING_BODY_S),
        _c(201, parent_id=101, body="won't fix"),
        _c(202, parent_id=101, body=f"Please provide a reason.\n\n{_SENTINEL}"),
    ]

    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="owner",
        repo_name="repo",
        adapter=mock_adapter,
    )
    svc.respond(result, 1)

    mock_adapter.resolve_comment.assert_not_called()
    mock_adapter.post_reply.assert_not_called()


# ---------------------------------------------------------------------------
# REVUE-173: Sentinel registry (SentinelKind enum + _SENTINEL_MAP)
# ---------------------------------------------------------------------------

_RM_SENTINEL = "[//]: # (revue:rm)"
_NOT_ACK_SENTINEL = "[//]: # (revue:not_ack)"


def test_sentinel_map_contains_exactly_four_sentinels() -> None:
    """_SENTINEL_MAP must contain exactly the four expected sentinel strings."""
    from revue.comments.service import SentinelKind, _SENTINEL_MAP
    assert set(_SENTINEL_MAP.keys()) == {
        "[//]: # (revue:ack)",
        "[//]: # (revue:resolved)",
        "[//]: # (revue:rm)",
        "[//]: # (revue:not_ack)",
    }
    assert _SENTINEL_MAP["[//]: # (revue:ack)"] is SentinelKind.TERMINAL
    assert _SENTINEL_MAP["[//]: # (revue:resolved)"] is SentinelKind.TERMINAL
    assert _SENTINEL_MAP["[//]: # (revue:rm)"] is SentinelKind.NON_TERMINAL
    assert _SENTINEL_MAP["[//]: # (revue:not_ack)"] is SentinelKind.NON_TERMINAL


def test_sentinel_kind_composite_reply_returns_first_match() -> None:
    """When both revue:ack and revue:resolved appear (terminal decision format), TERMINAL is returned."""
    from revue.comments.service import SentinelKind, get_sentinel_kind
    composite = f"Won't fix.\n\n{_SENTINEL}\n{_RESOLVED_SENTINEL}"
    assert get_sentinel_kind(composite) is SentinelKind.TERMINAL


def test_sentinel_kind_terminal_for_revue_ack() -> None:
    """get_sentinel_kind() returns TERMINAL for text containing revue:ack."""
    from revue.comments.service import SentinelKind, get_sentinel_kind
    assert get_sentinel_kind(f"Some reply.\n\n{_SENTINEL}") is SentinelKind.TERMINAL


def test_sentinel_kind_terminal_for_revue_resolved() -> None:
    """get_sentinel_kind() returns TERMINAL for text containing revue:resolved."""
    from revue.comments.service import SentinelKind, get_sentinel_kind
    assert get_sentinel_kind(f"Won't fix.\n\n{_SENTINEL}\n{_RESOLVED_SENTINEL}") is SentinelKind.TERMINAL


def test_sentinel_kind_non_terminal_for_revue_rm() -> None:
    """get_sentinel_kind() returns NON_TERMINAL for text containing revue:rm."""
    from revue.comments.service import SentinelKind, get_sentinel_kind
    assert get_sentinel_kind(f"Please give a reason.\n\n{_RM_SENTINEL}") is SentinelKind.NON_TERMINAL


def test_sentinel_kind_non_terminal_for_revue_not_ack() -> None:
    """get_sentinel_kind() returns NON_TERMINAL for text containing revue:not_ack."""
    from revue.comments.service import SentinelKind, get_sentinel_kind
    assert get_sentinel_kind(f"Finding still applies.\n\n{_NOT_ACK_SENTINEL}") is SentinelKind.NON_TERMINAL


def test_sentinel_kind_none_for_plain_text() -> None:
    """get_sentinel_kind() returns None for text with no sentinel."""
    from revue.comments.service import get_sentinel_kind
    assert get_sentinel_kind("Just a normal developer reply.") is None


def test_sentinel_is_last_true_for_any_sentinel_kind() -> None:
    """_sentinel_is_last() returns True for any text containing any sentinel in _SENTINEL_MAP."""
    from revue.comments.service import _sentinel_is_last
    assert _sentinel_is_last(f"text\n\n{_SENTINEL}") is True
    assert _sentinel_is_last(f"text\n\n{_RESOLVED_SENTINEL}") is True
    assert _sentinel_is_last(f"text\n\n{_RM_SENTINEL}") is True
    assert _sentinel_is_last(f"text\n\n{_NOT_ACK_SENTINEL}") is True
    assert _sentinel_is_last("plain reply — no sentinel") is False


def test_terminal_is_last_only_for_terminal_kind() -> None:
    """_terminal_is_last() returns True only for terminal sentinels."""
    from revue.comments.service import _terminal_is_last
    assert _terminal_is_last(f"text\n\n{_SENTINEL}") is True
    assert _terminal_is_last(f"text\n\n{_RESOLVED_SENTINEL}") is True
    assert _terminal_is_last(f"text\n\n{_RM_SENTINEL}") is False
    assert _terminal_is_last(f"text\n\n{_NOT_ACK_SENTINEL}") is False
    assert _terminal_is_last("no sentinel") is False


# ---------------------------------------------------------------------------
# REVUE-173: reason_missing and not_acknowledged use distinct sentinels
# ---------------------------------------------------------------------------

def test_reason_missing_posts_revue_rm_sentinel(tmp_path) -> None:
    """reason_missing reply must end with revue:rm, not revue:ack."""
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult

    result = ClassificationResult(
        patterns_to_allow=[], patterns_to_disallow=[], state_updates=[],
        decisions=[{"fingerprint": "101", "decision": "reason_missing", "reply_draft": "Please clarify."}],
    )

    mock_adapter = MagicMock()
    mock_adapter.get_all_pr_comments.return_value = [
        _c(101, body=_FINDING_BODY_S),
        _c(201, parent_id=101, body="won't fix"),
    ]
    svc = WontFixReplyService(
        repo_path=str(tmp_path), ai_client=MagicMock(),
        repo_owner="owner", repo_name="repo",
        adapter=mock_adapter,
    )
    svc.respond(result, 1)

    mock_adapter.post_reply.assert_called_once()
    posted = mock_adapter.post_reply.call_args[0][5]
    assert _RM_SENTINEL in posted
    assert _SENTINEL not in posted


def test_not_acknowledged_posts_revue_not_ack_sentinel(tmp_path) -> None:
    """not_acknowledged reply must end with revue:not_ack, not revue:ack."""
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult

    result = ClassificationResult(
        patterns_to_allow=[], patterns_to_disallow=[], state_updates=[],
        decisions=[{"fingerprint": "101", "decision": "not_acknowledged", "reply_draft": "This finding still applies."}],
    )

    mock_adapter = MagicMock()
    mock_adapter.get_all_pr_comments.return_value = [
        _c(101, body=_FINDING_BODY_S),
        _c(201, parent_id=101, body="(no dev reply)"),
    ]
    svc = WontFixReplyService(
        repo_path=str(tmp_path), ai_client=MagicMock(),
        repo_owner="owner", repo_name="repo",
        adapter=mock_adapter,
    )
    svc.respond(result, 1)

    mock_adapter.post_reply.assert_called_once()
    posted = mock_adapter.post_reply.call_args[0][5]
    assert _NOT_ACK_SENTINEL in posted
    assert _SENTINEL not in posted


# ---------------------------------------------------------------------------
# REVUE-173: Idempotency guard covers all sentinels (AC8)
# ---------------------------------------------------------------------------

def test_idempotency_guard_recognises_all_sentinels(tmp_path) -> None:
    """respond() must skip re-posting when the last bot reply contains any sentinel."""
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult

    for sentinel in (_SENTINEL, _RESOLVED_SENTINEL, _RM_SENTINEL, _NOT_ACK_SENTINEL):
        result = ClassificationResult(
            patterns_to_allow=[], patterns_to_disallow=[], state_updates=[],
            decisions=[{"fingerprint": "101", "decision": "reason_missing", "reply_draft": "Please clarify."}],
        )
        mock_adapter = MagicMock()
        mock_adapter.get_all_pr_comments.return_value = [
            _c(101, body=_FINDING_BODY_S),
            _c(201, parent_id=101, body="won't fix"),
            _c(202, parent_id=101, body=f"Bot reply.\n\n{sentinel}"),
        ]
        svc = WontFixReplyService(
            repo_path=str(tmp_path), ai_client=MagicMock(),
            repo_owner="owner", repo_name="repo",
            adapter=mock_adapter,
        )
        svc.respond(result, 1)
        if mock_adapter.post_reply.called:
            pytest.fail(f"Should not post when last reply has {sentinel!r}")


def test_no_duplicate_reply_when_sentinel_is_last(tmp_path) -> None:
    """When last bot reply has revue:rm or revue:not_ack, no new reply posted (AC8)."""
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult

    for sentinel in (_RM_SENTINEL, _NOT_ACK_SENTINEL):
        result = ClassificationResult(
            patterns_to_allow=[], patterns_to_disallow=[], state_updates=[],
            decisions=[{"fingerprint": "101", "decision": "reason_missing", "reply_draft": "..."}],
        )
        mock_adapter = MagicMock()
        mock_adapter.get_all_pr_comments.return_value = [
            _c(101, body=_FINDING_BODY_S),
            _c(201, parent_id=101, body="won't fix"),
            _c(202, parent_id=101, body=f"Please clarify.\n\n{sentinel}"),
        ]
        svc = WontFixReplyService(
            repo_path=str(tmp_path), ai_client=MagicMock(),
            repo_owner="owner", repo_name="repo",
            adapter=mock_adapter,
        )
        svc.respond(result, 1)
        if mock_adapter.post_reply.called:
            pytest.fail(f"Duplicate reply guard failed for {sentinel!r}")


def test_dev_reply_after_revue_rm_resets_already_handled(tmp_path) -> None:
    """Developer reply after revue:rm → already_handled=False on next cycle (AC5)."""
    from revue.comments.platform_adapter import BitbucketAdapter

    mock_adapter = MagicMock(spec=BitbucketAdapter)
    mock_adapter.get_all_pr_comments.return_value = [
        _c(101, body=_FINDING_BODY_S),
        _c(201, parent_id=101, body="won't fix"),
        _c(202, parent_id=101, body=f"Please give a reason.\n\n{_RM_SENTINEL}"),
        _c(203, parent_id=101, body="Because it is a pre-existing constraint in the framework."),
    ]

    svc = _sentinel_svc(tmp_path, mock_adapter)
    threads = svc._collect_threads_with_replies(1)

    assert len(threads) == 1
    assert threads[0]["already_handled"] is False


def test_backward_compat_revue_ack_still_terminal(tmp_path) -> None:
    """revue:ack from an old reason_missing post is still recognised as sentinel-last (AC4)."""
    from revue.comments.platform_adapter import BitbucketAdapter

    mock_adapter = MagicMock(spec=BitbucketAdapter)
    mock_adapter.get_all_pr_comments.return_value = [
        _c(101, body=_FINDING_BODY_S),
        _c(201, parent_id=101, body="won't fix"),
        _c(202, parent_id=101, body=f"Please give a reason.\n\n{_SENTINEL}"),
    ]

    svc = _sentinel_svc(tmp_path, mock_adapter)
    threads = svc._collect_threads_with_replies(1)

    assert len(threads) == 1
    assert threads[0]["already_handled"] is True


def test_terminal_decisions_still_use_revue_ack(tmp_path) -> None:
    """allowed_pattern reply contains revue:ack; revue:rm and revue:not_ack are absent (AC6)."""
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult

    result = ClassificationResult(
        patterns_to_allow=[{"pattern": "rate-limit sleep", "rationale": "pre-existing"}],
        patterns_to_disallow=[],
        state_updates=[{"fingerprint": "101", "file_path": "a.py", "decision": "allowed_pattern"}],
        decisions=[{
            "fingerprint": "101", "decision": "allowed_pattern",
            "pattern": "rate-limit sleep", "rationale": "pre-existing",
            "reply_draft": "Noted — lessons recorded.",
        }],
    )

    mock_adapter = MagicMock()
    mock_adapter.get_all_pr_comments.return_value = [
        _c(101, body=_FINDING_BODY_S),
        _c(201, parent_id=101, body="won't fix — pre-existing"),
    ]
    mock_adapter.ensure_lessons_pr.return_value = "https://bitbucket.org/ws/repo/pull-requests/9"
    mock_adapter.get_pr_template.return_value = None

    with patch("revue.comments.service.WontFixReplyService._append_pattern_to_config"):
        svc = WontFixReplyService(
            repo_path=str(tmp_path), ai_client=MagicMock(),
            repo_owner="owner", repo_name="repo",
            adapter=mock_adapter,
        )
        svc.respond(result, 1)

    mock_adapter.post_reply.assert_called_once()
    posted = mock_adapter.post_reply.call_args[0][5]
    assert _SENTINEL in posted
    assert _RM_SENTINEL not in posted
    assert _NOT_ACK_SENTINEL not in posted


# ---------------------------------------------------------------------------
# REVUE-161: resolve_conversation — PR-level comments are resolved
# ---------------------------------------------------------------------------

def test_post_acknowledgement_reply_resolves_conversation(tmp_path) -> None:
    """REVUE-161 T3.1: post_reply is followed by resolve_conversation for acknowledged decisions."""
    from revue.comments.service import WontFixReplyService
    from revue.core.models import ClassificationResult
    from revue.comments.json_store import PerPRCommentStore

    # Pre-populate store with a finding
    store = PerPRCommentStore(tmp_path)
    store.save_finding(
        platform="bitbucket",
        pr_number=1,
        file_path="src/sec.py",
        fingerprint="101",
        platform_comment_id="101",
        line_number=10,
        comment_body="Security issue",
    )

    result = ClassificationResult(
        patterns_to_allow=[],
        patterns_to_disallow=[],
        state_updates=[{"fingerprint": "101", "file_path": "src/sec.py", "decision": "acknowledged_fixed"}],
        decisions=[{
            "fingerprint": "101",
            "decision": "acknowledged_fixed",
            "reply_draft": "Thanks for fixing this!",
        }],
    )

    mock_adapter = MagicMock()
    mock_adapter.get_all_pr_comments.return_value = [
        {
            "id": 101,
            "inline": {"path": "src/sec.py", "to": 10},
            "content": {"raw": "**🔴 [HIGH] Security issue"},
        },
        {
            "id": 201,
            "parent": {"id": 101},
            "content": {"raw": "Fixed in latest commit"},
        },
    ]
    mock_adapter.get_pr_template.return_value = None

    svc = WontFixReplyService(
        repo_path=str(tmp_path),
        ai_client=MagicMock(),
        repo_owner="owner",
        repo_name="repo",
        adapter=mock_adapter,
    )
    svc.respond(result, 1)

    # Both post_reply and resolve_conversation should be called
    mock_adapter.post_reply.assert_called_once()
    mock_adapter.resolve_conversation.assert_called_once()
    call_args = mock_adapter.resolve_conversation.call_args[0]
    assert call_args[0] == "owner"   # repo_owner
    assert call_args[1] == "repo"    # repo_name
    assert call_args[2] == 1         # pr_number as int
    assert call_args[3] == "101"     # comment_id matches finding's platform_comment_id
