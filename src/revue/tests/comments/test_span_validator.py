"""Tests for the replacement-span validator.

Each test follows AAA structure (Arrange / Act / Assert) and asserts on the
returned line count — never on internal helper state.
"""
from __future__ import annotations

from revue.comments._span_validator import (
    is_anchor_coherent,
    validate_replacement_span,
)


_GITLAB_DIFF = """\
@@ -0,0 +47,8 @@
+def _gitlab(path: str) -> list | dict:
+    token = os.environ["GITLAB_TOKEN"]
+    url = f"https://gitlab.com/api/v4{path}"
+    req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token})
+    with urllib.request.urlopen(req) as resp:
+        return json.load(resp)
+
+def _bitbucket(path: str) -> dict:
"""


def test_caps_span_to_six_when_declared_eight_crosses_into_next_function() -> None:
    """PR #19 r3202849011 regression: declared_rlc=8 covers _gitlab + blank + def _bitbucket."""
    # Arrange
    code_replacement = [
        "def _gitlab(path: str) -> list | dict:",
        "    token = os.environ.get(\"GITLAB_TOKEN\")",
        "    if not token:",
        "        raise RuntimeError(\"...\")",
        "    url = f\"https://gitlab.com/api/v4{path}\"",
        "    req = urllib.request.Request(url, headers={\"PRIVATE-TOKEN\": token})",
        "    with urllib.request.urlopen(req) as resp:",
        "        return json.load(resp)",
    ]

    # Act
    rlc = validate_replacement_span(
        diff=_GITLAB_DIFF,
        line_number=47,
        code_replacement=code_replacement,
        declared_rlc=8,
    )

    # Assert — capped to actual _gitlab() body length (lines 47..52)
    assert rlc == 6


def test_returns_declared_six_when_span_stays_within_function_body() -> None:
    """A correctly-scoped 6-line replacement of _gitlab() must not be capped."""
    # Arrange
    code_replacement = [
        "def _gitlab(path: str) -> list | dict:",
        "    token = os.environ.get(\"GITLAB_TOKEN\")",
        "    url = f\"https://gitlab.com/api/v4{path}\"",
        "    req = urllib.request.Request(url, headers={\"PRIVATE-TOKEN\": token})",
        "    with urllib.request.urlopen(req) as resp:",
        "        return json.load(resp)",
    ]

    # Act
    rlc = validate_replacement_span(
        diff=_GITLAB_DIFF,
        line_number=47,
        code_replacement=code_replacement,
        declared_rlc=6,
    )

    # Assert
    assert rlc == 6


def test_returns_one_when_declared_rlc_is_one() -> None:
    """rlc=1 short-circuits — no validation work needed for single-line replacements."""
    # Arrange
    code_replacement = ["    token = os.environ.get(\"GITLAB_TOKEN\")"]

    # Act
    rlc = validate_replacement_span(
        diff=_GITLAB_DIFF,
        line_number=48,
        code_replacement=code_replacement,
        declared_rlc=1,
    )

    # Assert
    assert rlc == 1


def test_returns_declared_rlc_when_diff_is_empty() -> None:
    """Fail-open when no diff is available — never produce a smaller span than declared."""
    # Arrange
    code_replacement = ["a", "b", "c"]

    # Act
    rlc = validate_replacement_span(
        diff="",
        line_number=47,
        code_replacement=code_replacement,
        declared_rlc=3,
    )

    # Assert
    assert rlc == 3


def test_returns_declared_rlc_when_anchor_line_is_outside_diff() -> None:
    """If the diff doesn't contain line_number, validator has no basis to cap — pass through."""
    # Arrange
    code_replacement = ["x", "y"]

    # Act
    rlc = validate_replacement_span(
        diff=_GITLAB_DIFF,
        line_number=999,
        code_replacement=code_replacement,
        declared_rlc=2,
    )

    # Assert
    assert rlc == 2


def test_caps_indented_span_to_three_when_sibling_elif_appears_at_same_indent() -> None:
    """Span crossing an `elif` at the same indent as the original `if` is detected."""
    # Arrange
    diff = """\
@@ -0,0 +10,5 @@
+    if x > 0:
+        do_thing()
+        do_other()
+    elif x < 0:
+        do_negative()
"""
    code_replacement = [
        "    if x > 0:",
        "        do_thing()",
        "        do_other_safe()",
        "        log_it()",
    ]

    # Act
    rlc = validate_replacement_span(
        diff=diff,
        line_number=10,
        code_replacement=code_replacement,
        declared_rlc=4,
    )

    # Assert — capped to lines 10..12 (before the sibling elif at line 13)
    assert rlc == 3


def test_walks_back_over_trailing_blanks_to_last_content_line() -> None:
    """When boundary is preceded by blanks, cap lands on the last non-blank content line."""
    # Arrange
    diff = """\
@@ -0,0 +47,8 @@
+def _gitlab():
+    token = TOKEN
+    return token
+
+
+
+def _next():
+    pass
"""
    code_replacement = ["def _gitlab():", "    new_body()", "    return new()"]

    # Act
    rlc = validate_replacement_span(
        diff=diff,
        line_number=47,
        code_replacement=code_replacement,
        declared_rlc=8,
    )

    # Assert — 47 def, 48 token, 49 return; trailing blanks 50-52 stripped
    assert rlc == 3


def test_returns_declared_rlc_when_code_replacement_is_empty_list() -> None:
    """Empty replacement array short-circuits validation."""
    # Arrange + Act
    rlc = validate_replacement_span(
        diff=_GITLAB_DIFF,
        line_number=47,
        code_replacement=[],
        declared_rlc=8,
    )

    # Assert
    assert rlc == 8


def test_returns_declared_rlc_when_code_replacement_is_none() -> None:
    """None replacement short-circuits validation."""
    # Arrange + Act
    rlc = validate_replacement_span(
        diff=_GITLAB_DIFF,
        line_number=47,
        code_replacement=None,
        declared_rlc=8,
    )

    # Assert
    assert rlc == 8


# ─────────────────────────────────────────────────────────────────────────────
# is_anchor_coherent — PR #20 wrong-anchor regression coverage
# ─────────────────────────────────────────────────────────────────────────────


def test_anchor_coherent_when_indents_match_at_def_line() -> None:
    """def replacement at line 47 (def line, indent 0): coherent."""
    # Arrange
    code_replacement = ["def _gitlab(path: str) -> list | dict:", "    pass"]

    # Act
    result = is_anchor_coherent(
        diff=_GITLAB_DIFF,
        line_number=47,
        code_replacement=code_replacement,
    )

    # Assert
    assert result is True


def test_anchor_incoherent_when_def_replacement_is_anchored_at_indented_body_line() -> None:
    """PR #20 r3203256869: def _gitlab() suggested as fix, anchor is mid-body (line 51).

    Line 51 in _GITLAB_DIFF is `    with urllib.request.urlopen…` (indent 4).
    code_replacement[0] is `def _gitlab(...)` (indent 0). Indent mismatch → incoherent.
    """
    # Arrange
    code_replacement = [
        "def _gitlab(path: str) -> list | dict:",
        "    token = os.environ.get(\"GITLAB_TOKEN\")",
        "    with urllib.request.urlopen(req) as resp:",
        "        return json.load(resp)",
    ]

    # Act
    result = is_anchor_coherent(
        diff=_GITLAB_DIFF,
        line_number=51,
        code_replacement=code_replacement,
    )

    # Assert — the def at indent 0 cannot land at line 51 (indent 4)
    assert result is False


def test_anchor_incoherent_when_method_replacement_is_anchored_at_import_line() -> None:
    """PR #20 r3203256731: synthesis_client = (...) (indent 8) anchored at line 25 of pipeline.py.

    The replacement is a constructor body line (8-space indent); anchoring it at an
    import-section line (indent 0) would corrupt unrelated code.
    """
    # Arrange
    diff = """\
@@ -0,0 +20,10 @@
+import time
+from dataclasses import dataclass
+from typing import NamedTuple, Optional
+from uuid import uuid4
+
+from .agent_names import ORCHESTRATOR
+from .ai_config import AIConfig
+from .ai_client import AIClient, create_ai_client
+from .cleo_router import _INFRASTRUCTURE_AGENTS
+from .metrics import (
"""
    code_replacement = [
        "        self.synthesis_client: AIClient = (",
        "            synthesis_client if synthesis_client is not None",
        "            else resolve_synthesis_client(config, self._client, metrics=self._metrics)",
        "        )",
    ]

    # Act
    result = is_anchor_coherent(
        diff=diff,
        line_number=25,
        code_replacement=code_replacement,
    )

    # Assert — line 25 is `from .ai_config import AIConfig` (indent 0); replacement is indent 8
    assert result is False


def test_anchor_coherent_when_diff_is_empty() -> None:
    """Fail-open when no diff is available — never produce a false negative on coherence."""
    # Arrange + Act
    result = is_anchor_coherent(
        diff="",
        line_number=47,
        code_replacement=["def f():", "    pass"],
    )

    # Assert
    assert result is True


def test_anchor_coherent_when_anchor_line_is_outside_diff() -> None:
    """If the diff doesn't contain line_number, fail open."""
    # Arrange + Act
    result = is_anchor_coherent(
        diff=_GITLAB_DIFF,
        line_number=999,
        code_replacement=["def f():", "    pass"],
    )

    # Assert
    assert result is True


def test_anchor_coherent_when_code_replacement_is_none_or_empty() -> None:
    """No code_replacement = nothing to check; coherent by definition."""
    # Arrange + Act + Assert
    assert is_anchor_coherent(_GITLAB_DIFF, 47, None) is True
    assert is_anchor_coherent(_GITLAB_DIFF, 47, []) is True


def test_anchor_coherent_when_first_replacement_line_is_blank_uses_first_non_blank() -> None:
    """A leading blank line in code_replacement shouldn't dictate indent — use first content line."""
    # Arrange
    code_replacement = [
        "",
        "def _gitlab(path: str) -> list | dict:",
        "    pass",
    ]

    # Act
    result = is_anchor_coherent(
        diff=_GITLAB_DIFF,
        line_number=47,
        code_replacement=code_replacement,
    )

    # Assert
    assert result is True
