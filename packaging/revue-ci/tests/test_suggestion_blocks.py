"""
Tests for REVUE-187: Nova inline suggestions as platform-native suggestion blocks.

TC1: GitHub inline comment with replacement code → body contains ``suggestion`` block
TC2: GitLab inline comment with replacement code → body contains ``suggestion:-0+0`` block
TC3: GitHub inline comment with text-only suggestion → falls back to blockquote
TC4: GitLab inline comment with text-only suggestion → falls back to blockquote
TC5: Bitbucket inline comment unchanged regardless of suggestion content

Also covers:
- AIReview.code_replacement field (AC contract test — every field asserted by name)
- _format_recommendation helper
"""
import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

# These helpers access cli.py internals (_run_per_issue_dedup, _format_recommendation)
# intentionally: the ACs require verifying that the *wiring* from finding JSON through
# the dedup path to the posted comment body is correct, which demands internal access.
# A public API boundary doesn't exist at this level — cli.py is the integration point.

@dataclass
class _FakeReviewResult:
    file_path: str
    response: str
    error: str = ""


def _make_review_response(findings: list[dict]) -> str:
    return json.dumps({"findings": findings, "summary": "ok"})


def _run_dedup(platform: str, finding: dict, tmp_path):
    """Run _run_per_issue_dedup for a single finding on the given platform."""
    from revue_ci.cli import _run_per_issue_dedup
    from revue_core.comments.json_store import PerPRCommentStore

    review_results = [_FakeReviewResult("app.py", _make_review_response([finding]))]
    mock_adapter = MagicMock()
    mock_adapter.get_existing_comments.return_value = []
    mock_adapter.post_review_comment.return_value = "cmt-1"

    store = PerPRCommentStore(tmp_path)
    _run_per_issue_dedup(mock_adapter, 1, platform, review_results, {}, store)
    return mock_adapter


# ---------------------------------------------------------------------------
# AIReview.code_replacement model field tests (AC contract)
# ---------------------------------------------------------------------------

def test_ai_review_code_replacement_field_defaults_none() -> None:
    """AIReview.code_replacement defaults to None (AC5 schema, all fields asserted)."""
    from revue_core.core.models import AIReview

    review = AIReview(
        file_path="app.py",
        line_number=10,
        severity="high",
        issue="SQL injection",
        suggestion="Use parameterised queries",
        confidence=0.9,
    )
    # Assert every field by name — CLAUDE.md AC contract testing requirement
    assert review.file_path == "app.py"
    assert review.line_number == 10
    assert review.severity == "high"
    assert review.issue == "SQL injection"
    assert review.suggestion == "Use parameterised queries"
    assert review.confidence == 0.9
    assert review.category == "general"
    assert review.agent_name == ""
    assert review.synthesised_from is None
    assert review.code_replacement is None


def test_ai_review_code_replacement_field_accepts_lines() -> None:
    """AIReview.code_replacement accepts a list of replacement code lines."""
    from revue_core.core.models import AIReview

    replacement = ["    return sanitize(user_input)", "    validate(user_input)"]
    review = AIReview(
        file_path="db.py",
        line_number=42,
        severity="high",
        issue="SQL injection",
        suggestion="Use parameterised queries",
        confidence=0.95,
        code_replacement=replacement,
    )
    assert review.code_replacement == replacement


# ---------------------------------------------------------------------------
# TC1 — GitHub: replacement code → suggestion block
# ---------------------------------------------------------------------------

def test_github_inline_with_replacement_uses_suggestion_block(tmp_path) -> None:
    """TC1: GitHub inline comment with code_replacement uses ``` suggestion block."""
    finding = {
        "severity": "high",
        "issue": "SQL injection",
        "line": 10,
        "recommendation": "Use parameterised queries",
        "code_replacement": ["    cursor.execute(query, (user_id,))"],
    }
    mock_adapter = _run_dedup("github", finding, tmp_path)

    mock_adapter.post_review_comment.assert_called_once()
    body = mock_adapter.post_review_comment.call_args[1]["body"]
    assert "```suggestion" in body
    assert "cursor.execute(query, (user_id,))" in body
    assert "> 💡 **Action:** Use parameterised queries" in body


def test_github_inline_with_multiline_replacement(tmp_path) -> None:
    """TC1 multi-line: all replacement lines appear inside the suggestion block."""
    finding = {
        "severity": "medium",
        "issue": "Unsafe concatenation",
        "line": 5,
        "recommendation": "Escape input",
        "code_replacement": [
            "    safe = html.escape(user_input)",
            "    return safe",
        ],
    }
    mock_adapter = _run_dedup("github", finding, tmp_path)

    body = mock_adapter.post_review_comment.call_args[1]["body"]
    assert "```suggestion" in body
    assert "html.escape(user_input)" in body
    assert "return safe" in body


# ---------------------------------------------------------------------------
# TC2 — GitLab: replacement code → suggestion:-0+0 block
# ---------------------------------------------------------------------------

def test_gitlab_inline_with_replacement_uses_suggestion_block(tmp_path) -> None:
    """TC2: GitLab inline comment with code_replacement uses ``` suggestion:-0+0 block."""
    finding = {
        "severity": "high",
        "issue": "XSS vulnerability",
        "line": 15,
        "recommendation": "Escape output",
        "code_replacement": ["    return html.escape(value)"],
    }
    mock_adapter = _run_dedup("gitlab", finding, tmp_path)

    mock_adapter.post_review_comment.assert_called_once()
    body = mock_adapter.post_review_comment.call_args[1]["body"]
    assert "```suggestion:-0+0" in body
    assert "html.escape(value)" in body
    assert "> 💡 **Action:** Escape output" in body


# ---------------------------------------------------------------------------
# TC3 — GitHub: text-only suggestion → blockquote fallback
# ---------------------------------------------------------------------------

def test_github_inline_without_replacement_uses_blockquote(tmp_path) -> None:
    """TC3: GitHub inline comment without code_replacement falls back to blockquote."""
    finding = {
        "severity": "medium",
        "issue": "Unused import",
        "line": 3,
        "recommendation": "Remove the import statement",
    }
    mock_adapter = _run_dedup("github", finding, tmp_path)

    body = mock_adapter.post_review_comment.call_args[1]["body"]
    assert "> 💡 **Suggest:** Remove the import statement" in body
    assert "```suggestion" not in body


def test_github_inline_with_empty_replacement_uses_blockquote(tmp_path) -> None:
    """TC3 edge: empty code_replacement list falls back to blockquote."""
    finding = {
        "severity": "low",
        "issue": "Magic number",
        "line": 7,
        "recommendation": "Extract to named constant",
        "code_replacement": [],
    }
    mock_adapter = _run_dedup("github", finding, tmp_path)

    body = mock_adapter.post_review_comment.call_args[1]["body"]
    assert "> 💡 **Suggest:** Extract to named constant" in body
    assert "```suggestion" not in body


# ---------------------------------------------------------------------------
# TC4 — GitLab: text-only suggestion → blockquote fallback
# ---------------------------------------------------------------------------

def test_gitlab_inline_without_replacement_uses_blockquote(tmp_path) -> None:
    """TC4: GitLab inline comment without code_replacement falls back to blockquote."""
    finding = {
        "severity": "info",
        "issue": "Verbose logging",
        "line": 22,
        "recommendation": "Reduce log verbosity in production",
    }
    mock_adapter = _run_dedup("gitlab", finding, tmp_path)

    body = mock_adapter.post_review_comment.call_args[1]["body"]
    assert "> ℹ️ **Note:** Reduce log verbosity in production" in body
    assert "```suggestion" not in body


# ---------------------------------------------------------------------------
# TC5 — Bitbucket: unchanged regardless of code_replacement
# ---------------------------------------------------------------------------

def test_bitbucket_inline_with_replacement_still_uses_blockquote(tmp_path) -> None:
    """TC5: Bitbucket inline comment uses blockquote even when code_replacement is set."""
    finding = {
        "severity": "high",
        "issue": "Hardcoded credential",
        "line": 8,
        "recommendation": "Move to environment variable",
        "code_replacement": ["    password = os.getenv('DB_PASSWORD')"],
    }
    mock_adapter = _run_dedup("bitbucket", finding, tmp_path)

    body = mock_adapter.post_review_comment.call_args[1]["body"]
    assert "> 💡 **Action:** Move to environment variable" in body
    assert "```suggestion" not in body


def test_bitbucket_inline_without_replacement_unchanged(tmp_path) -> None:
    """TC5 baseline: Bitbucket without code_replacement behaves as before."""
    finding = {
        "severity": "medium",
        "issue": "Missing type hint",
        "line": 14,
        "recommendation": "Add return type annotation",
    }
    mock_adapter = _run_dedup("bitbucket", finding, tmp_path)

    body = mock_adapter.post_review_comment.call_args[1]["body"]
    assert "> 💡 **Suggest:** Add return type annotation" in body
    assert "```suggestion" not in body


# ---------------------------------------------------------------------------
# _format_recommendation unit tests (isolated helper)
# ---------------------------------------------------------------------------

def test_format_recommendation_github_with_replacement() -> None:
    """_format_recommendation returns GitHub suggestion block when code_replacement set."""
    from revue_ci.cli import _format_recommendation

    result = _format_recommendation(
        rec="Use parameterised queries",
        code_replacement=["    cursor.execute(query, (user_id,))"],
        platform_str="github",
    )
    assert "```suggestion" in result
    assert "cursor.execute(query, (user_id,))" in result
    assert "> 💡 **Recommendation:** Use parameterised queries" in result


def test_format_recommendation_gitlab_with_replacement() -> None:
    """_format_recommendation returns GitLab suggestion:-0+0 block when code_replacement set."""
    from revue_ci.cli import _format_recommendation

    result = _format_recommendation(
        rec="Escape output",
        code_replacement=["    return html.escape(value)"],
        platform_str="gitlab",
    )
    assert "```suggestion:-0+0" in result
    assert "html.escape(value)" in result
    assert "> 💡 **Recommendation:** Escape output" in result


def test_format_recommendation_bitbucket_with_replacement_falls_back() -> None:
    """_format_recommendation falls back to blockquote on Bitbucket even with replacement."""
    from revue_ci.cli import _format_recommendation

    result = _format_recommendation(
        rec="Move to env var",
        code_replacement=["    pw = os.getenv('PW')"],
        platform_str="bitbucket",
    )
    assert "> 💡 **Recommendation:** Move to env var" in result
    assert "```suggestion" not in result


def test_format_recommendation_no_replacement_any_platform() -> None:
    """_format_recommendation falls back to blockquote when code_replacement is None."""
    from revue_ci.cli import _format_recommendation

    for platform in ("github", "gitlab", "bitbucket"):
        result = _format_recommendation(
            rec="Fix the issue",
            code_replacement=None,
            platform_str=platform,
        )
        assert "> 💡 **Recommendation:** Fix the issue" in result, f"failed for {platform}"
        assert "```suggestion" not in result, f"unexpected suggestion block for {platform}"


# ---------------------------------------------------------------------------
# AC5 — _REVIEW_INSTRUCTIONS includes code_replacement schema field
# ---------------------------------------------------------------------------

def test_review_instructions_include_code_replacement() -> None:
    """AC5: Agent review instructions schema includes code_replacement field."""
    from revue_core.core.agent_loader import _REVIEW_INSTRUCTIONS

    assert "code_replacement" in _REVIEW_INSTRUCTIONS, (
        "Agent prompt schema must include code_replacement so agents can populate it"
    )


def test_review_instructions_example_shows_array_not_null() -> None:
    """Schema example must show an array value for code_replacement, not null.

    Showing null as the example value biases the AI toward always returning null,
    which silently disables all inline suggestion blocks in every review.
    The null path belongs in the field rules, not in the example object.
    """
    from revue_core.core.agent_loader import _REVIEW_INSTRUCTIONS

    # The example JSON object should demonstrate an array so the AI learns to produce one
    assert '"code_replacement": [' in _REVIEW_INSTRUCTIONS, (
        "Schema example must show an array for code_replacement — "
        "a null example biases the AI toward never producing suggestions"
    )


def test_review_instructions_field_rules_explain_null_fallback() -> None:
    """Field rules must explain when to use null — not an inline JS comment.

    JS-style comments (// or null) are invalid JSON and can leak into AI output,
    causing JSONDecodeError in filter_code_replacement. The null guidance belongs
    in the prose field rules section.
    """
    from revue_core.core.agent_loader import _REVIEW_INSTRUCTIONS

    assert "Leave it null" in _REVIEW_INSTRUCTIONS, (
        "Field rules must explain the null fallback for code_replacement"
    )
    assert "// or null" not in _REVIEW_INSTRUCTIONS, (
        "JS-style inline comments are invalid JSON and must not appear in the schema example"
    )


# ---------------------------------------------------------------------------
# Wiring test: agent_loader.LoadedAgent.analyse() propagates code_replacement
# into AIReview objects (per CLAUDE.md AC contract testing mandate)
# ---------------------------------------------------------------------------

def test_agent_loader_propagates_code_replacement_to_ai_review() -> None:
    """End-to-end wiring: LoadedAgent.analyse() must parse code_replacement from
    the AI response JSON and set it on the returned AIReview objects.

    Per CLAUDE.md: 'confirm the end-to-end wiring is tested — not just that the
    writer accepts a value, but that the caller passes it.'
    """
    from unittest.mock import MagicMock
    from revue_core.core.agent_loader import LoadedAgent, AgentDefinition
    from revue_core.core.models import FileChange

    ai_response = json.dumps({"status": "findings", "findings": [{
        "file_path": "app.py",
        "line_number": 5,
        "severity": "high",
        "issue": "Missing null check",
        "suggestion": "Add null guard",
        "confidence": 0.9,
        "category": "code-quality",
        "code_replacement": ["if value is None:", "    return"],
    }]})

    mock_result = MagicMock()
    mock_result.text = ai_response
    mock_client = MagicMock()
    mock_client.complete.return_value = mock_result

    agent_def = AgentDefinition(
        name="kai",
        display_name="Kai (Code Quality)",
        role="Code quality reviewer",
        system_prompt="You are a code reviewer.",
        severity_default="low",
    )
    changes = [FileChange(file_path="app.py", change_type="modified", additions=1, deletions=1, diff="- old\n+ new")]
    agent = LoadedAgent(agent_def, mock_client, 4096)
    reviews = agent.analyse(changes)

    assert len(reviews) == 1
    assert reviews[0].code_replacement == ["if value is None:", "    return"], (
        "code_replacement must be propagated from AI JSON response into AIReview"
    )


def test_agent_loader_rejects_non_string_items_in_code_replacement() -> None:
    """agent_loader must silently filter non-string items from code_replacement.

    An AI returning integers or nulls in code_replacement must not produce
    junk lines in the suggestion block.
    """
    from unittest.mock import MagicMock
    from revue_core.core.agent_loader import LoadedAgent, AgentDefinition
    from revue_core.core.models import FileChange

    ai_response = json.dumps({"status": "findings", "findings": [{
        "file_path": "app.py",
        "line_number": 1,
        "severity": "low",
        "issue": "issue",
        "suggestion": "fix",
        "confidence": 0.5,
        "category": "code-quality",
        "code_replacement": ["valid line", 42, None, "another valid line"],
    }]})

    mock_result = MagicMock()
    mock_result.text = ai_response
    mock_client = MagicMock()
    mock_client.complete.return_value = mock_result

    agent_def = AgentDefinition(name="kai", display_name="Kai (Code Quality)", role="Code quality reviewer", system_prompt="reviewer", severity_default="low")
    changes = [FileChange(file_path="app.py", change_type="modified", additions=1, deletions=1, diff="- old\n+ new")]
    agent = LoadedAgent(agent_def, mock_client, 4096)
    reviews = agent.analyse(changes)

    assert reviews[0].code_replacement == ["valid line", "another valid line"]
