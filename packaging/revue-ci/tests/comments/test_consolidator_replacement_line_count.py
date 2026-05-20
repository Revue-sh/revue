"""Tests for _build_consolidated handling of Nova's declared replacement_line_count.

Regression for the synthesis bug where Nova's `code_replacement` array length
was used as the source span size, producing wrong end_line on multi-line
replacements that change line count (e.g. 6 source lines → 8 replacement lines).

Each test follows AAA structure (Arrange / Act / Assert).
"""
from __future__ import annotations

from revue_core.comments.consolidator import NovaSingleShotStrategy
from revue_core.comments.models import (
    AgentFinding,
    SynthesisGroup,
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


def _make_two_finding_group() -> SynthesisGroup:
    """Two-finding proximity group for consolidation tests (no diff context)."""
    primary = AgentFinding(
        file_path="src/example.py",
        line_number=47,
        severity="medium",
        issue="missing token validation",
        suggestion="add an explicit RuntimeError",
        confidence=0.9,
        category="security",
        agent_name="zara",
        code_replacement=["def f():", "    pass"],
        replacement_line_count=2,
    )
    secondary = AgentFinding(
        file_path="src/example.py",
        line_number=48,
        severity="low",
        issue="missing timeout",
        suggestion="pass timeout=30",
        confidence=0.8,
        category="security",
        agent_name="maya",
        code_replacement=None,
        replacement_line_count=1,
    )
    return SynthesisGroup(
        findings=[primary, secondary],
        file_path="src/example.py",
        line_range=(47, 48),
        group_type="proximity",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Declared replacement_line_count handling
# ─────────────────────────────────────────────────────────────────────────────


def test_uses_declared_six_when_nova_provides_explicit_int_with_eight_replacement_lines() -> None:
    """Nova declares 6 source lines replaced by 8 new lines — declaration is authoritative."""
    # Arrange
    strategy = NovaSingleShotStrategy(ai_client=None)
    group = _make_two_finding_group()
    syn = {
        "file": "src/example.py",
        "line": 47,
        "issue": "missing validation and timeout",
        "suggestion": "add both",
        "severity": "medium",
        "code_replacement": [f"line{i}" for i in range(8)],
        "replacement_line_count": 6,
    }

    # Act
    result = strategy._build_consolidated(group, syn)

    # Assert
    assert result.replacement_line_count == 6
    assert len(result.code_replacement) == 8


def test_falls_back_to_array_length_three_when_declared_field_missing() -> None:
    """Old Nova output without replacement_line_count: fall back to len()."""
    # Arrange
    strategy = NovaSingleShotStrategy(ai_client=None)
    group = _make_two_finding_group()
    syn = {
        "file": "src/example.py",
        "line": 47,
        "issue": "missing validation",
        "suggestion": "add",
        "severity": "medium",
        "code_replacement": ["line1", "line2", "line3"],
    }

    # Act
    result = strategy._build_consolidated(group, syn)

    # Assert
    assert result.replacement_line_count == 3


def test_falls_back_to_array_length_when_declared_value_is_non_numeric_string() -> None:
    """Non-int declared value: fall back to len(code_replacement)."""
    # Arrange
    strategy = NovaSingleShotStrategy(ai_client=None)
    group = _make_two_finding_group()
    syn = {
        "file": "src/example.py",
        "line": 47,
        "issue": "x",
        "suggestion": "y",
        "severity": "medium",
        "code_replacement": ["a", "b"],
        "replacement_line_count": "six",
    }

    # Act
    result = strategy._build_consolidated(group, syn)

    # Assert
    assert result.replacement_line_count == 2


def test_falls_back_to_array_length_when_declared_value_is_zero() -> None:
    """Zero is invalid (need at least one source line): fall back to len()."""
    # Arrange
    strategy = NovaSingleShotStrategy(ai_client=None)
    group = _make_two_finding_group()
    syn = {
        "file": "src/example.py",
        "line": 47,
        "issue": "x",
        "suggestion": "y",
        "severity": "medium",
        "code_replacement": ["a", "b", "c"],
        "replacement_line_count": 0,
    }

    # Act
    result = strategy._build_consolidated(group, syn)

    # Assert
    assert result.replacement_line_count == 3


def test_accepts_declared_six_when_value_is_integer_valued_float() -> None:
    """JSON sometimes emits ints as floats (6.0): accept when value.is_integer()."""
    # Arrange
    strategy = NovaSingleShotStrategy(ai_client=None)
    group = _make_two_finding_group()
    syn = {
        "file": "src/example.py",
        "line": 47,
        "issue": "x",
        "suggestion": "y",
        "severity": "medium",
        "code_replacement": ["a", "b", "c", "d", "e", "f", "g", "h"],
        "replacement_line_count": 6.0,
    }

    # Act
    result = strategy._build_consolidated(group, syn)

    # Assert
    assert result.replacement_line_count == 6


# ─────────────────────────────────────────────────────────────────────────────
# Span-validator integration — PR #19 r3202849011 regression coverage
# ─────────────────────────────────────────────────────────────────────────────


def _gitlab_anchored_group() -> SynthesisGroup:
    """Two-finding group anchored at line 47 of the _gitlab() function in _GITLAB_DIFF."""
    zara_finding = AgentFinding(
        file_path="scripts/extract_positioning_fixtures.py",
        line_number=47,
        severity="medium",
        issue="missing token validation + timeout",
        suggestion="fix",
        confidence=0.95,
        category="security",
        agent_name="zara",
        code_replacement=["a", "b"],
        replacement_line_count=2,
    )
    maya_finding = AgentFinding(
        file_path="scripts/extract_positioning_fixtures.py",
        line_number=48,
        severity="medium",
        issue="missing timeout",
        suggestion="fix",
        confidence=0.9,
        category="code-quality",
        agent_name="maya",
        code_replacement=None,
        replacement_line_count=1,
    )
    return SynthesisGroup(
        findings=[zara_finding, maya_finding],
        file_path="scripts/extract_positioning_fixtures.py",
        line_range=(47, 48),
        group_type="same_line",
    )


_GITLAB_REPLACEMENT_EIGHT_LINES = [
    "def _gitlab(path: str) -> list | dict:",
    "    token = os.environ.get(\"GITLAB_TOKEN\")",
    "    if not token:",
    "        raise RuntimeError(\"...\")",
    "    url = f\"https://gitlab.com/api/v4{path}\"",
    "    req = urllib.request.Request(url, headers={\"PRIVATE-TOKEN\": token})",
    "    with urllib.request.urlopen(req) as resp:",
    "        return json.load(resp)",
]


def test_returns_declared_eight_when_no_diff_is_provided_to_strategy() -> None:
    """No diff_by_file means the validator has nothing to check — declared value stands."""
    # Arrange
    strategy = NovaSingleShotStrategy(ai_client=None)  # no diff_by_file
    group = _gitlab_anchored_group()
    syn = {
        "file": "scripts/extract_positioning_fixtures.py",
        "line": 47,
        "issue": "x",
        "suggestion": "y",
        "severity": "medium",
        "code_replacement": _GITLAB_REPLACEMENT_EIGHT_LINES,
        "replacement_line_count": 8,
    }

    # Act
    result = strategy._build_consolidated(group, syn)

    # Assert
    assert result.replacement_line_count == 8


