"""
auto_scorer.py — Heuristic quality scorer for findings

Computes clarity and actionability scores (1-5) for each finding
using deterministic string heuristics. Scores are inserted into
finding_quality with rated_by = 'auto'. Human ratings always take
precedence at read-time.

Story: REVUE-93

Read-time convention (AC3):
    When both auto and human ratings exist for the same finding + dimension,
    queries MUST prefer the human rating. Example:

    SELECT DISTINCT ON (fq.finding_id, fq.dimension_id)
        fq.*
    FROM finding_quality fq
    JOIN rating_sources rs ON rs.id = fq.rated_by_id
    ORDER BY fq.finding_id, fq.dimension_id,
             CASE rs.name WHEN 'human' THEN 0 ELSE 1 END,
             fq.rated_at DESC;
"""
from __future__ import annotations

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Heuristic word lists
# ---------------------------------------------------------------------------

VAGUE_WORDS = {"consider", "might", "perhaps", "maybe", "possibly", "could"}

ACTION_VERBS = {
    "add", "remove", "change", "replace", "rename",
    "delete", "move", "update", "refactor", "extract",
    "fix", "use", "implement", "wrap",
}

# Common source file extensions for "contains file path" heuristic
_FILE_EXT_RE = re.compile(
    r'\.\b(?:py|js|ts|tsx|jsx|go|rb|rs|java|c|cpp|h|hpp|cs|swift|kt|sh|yml|yaml|toml|json|css|html|sql)\b',
    re.IGNORECASE,
)

_WORD_BOUNDARY_RE_CACHE: dict[str, re.Pattern] = {}


def _word_pattern(word: str) -> re.Pattern:
    """Return a compiled regex for whole-word, case-insensitive match."""
    if word not in _WORD_BOUNDARY_RE_CACHE:
        _WORD_BOUNDARY_RE_CACHE[word] = re.compile(
            rf'\b{re.escape(word)}\b', re.IGNORECASE
        )
    return _WORD_BOUNDARY_RE_CACHE[word]


def _clamp(score: int) -> int:
    return max(1, min(5, score))


def _has_file_path(text: str) -> bool:
    """Check if text contains a file path (/ in a token or known extension)."""
    if _FILE_EXT_RE.search(text):
        return True
    # Check for / in any token (e.g., src/foo/bar.py)
    for token in text.split():
        if '/' in token:
            return True
    return False


def _has_line_reference(text: str) -> bool:
    """Check for line references like 'line 42', 'L42', 'line_start'."""
    return bool(re.search(r'\blines?\s*\d+', text, re.IGNORECASE)) or \
           bool(re.search(r'\bL\d+', text))


# ---------------------------------------------------------------------------
# Public scoring functions
# ---------------------------------------------------------------------------

def compute_clarity_score(finding: dict) -> int:
    """
    Compute clarity score (1-5) from finding text heuristics.

    Scoring:
      +2  issue AND details both non-empty
      +1  len(issue) > 20
      +1  No vague words in issue+details
      +1  Contains file path or line reference
    """
    issue = (finding.get("issue") or "").strip()
    details = (finding.get("details") or "").strip()

    raw = 0

    # +2: both fields non-empty
    if issue and details:
        raw += 2

    # +1: issue length > 20
    if len(issue) > 20:
        raw += 1

    # +1: no vague words in issue + details
    combined = f"{issue} {details}"
    has_vague = any(_word_pattern(w).search(combined) for w in VAGUE_WORDS)
    if not has_vague:
        raw += 1

    # +1: contains file path or line reference
    if _has_file_path(combined) or _has_line_reference(combined):
        raw += 1

    return _clamp(raw)


def compute_actionability_score(finding: dict) -> int:
    """
    Compute actionability score (1-5) from finding text heuristics.

    Scoring:
      +2  recommendation non-empty
      +1  Contains code snippet (backtick/indented block) or file path
      +1  Uses action verb in recommendation
      +1  Mentions exact change needed (file path AND action verb in recommendation)
    """
    recommendation = (finding.get("recommendation") or "").strip()

    raw = 0

    # +2: recommendation non-empty
    if recommendation:
        raw += 2

    # +1: contains code snippet or file path
    has_code = '`' in recommendation or bool(
        re.search(r'\n    \S', recommendation)  # indented block
    )
    has_path = _has_file_path(recommendation)
    if has_code or has_path:
        raw += 1

    # +1: uses action verb in recommendation
    has_action_verb = any(
        _word_pattern(v).search(recommendation) for v in ACTION_VERBS
    )
    if has_action_verb:
        raw += 1

    # +1: exact change = file path AND action verb in recommendation
    if has_path and has_action_verb:
        raw += 1

    return _clamp(raw)


# ---------------------------------------------------------------------------
# DB insert functions
# ---------------------------------------------------------------------------

# Module-level cache for lookup IDs (avoids repeated DB round-trips within a session).
# NOTE: Do NOT use a mutable default argument for this — it causes test pollution.
_LOOKUP_CACHE: dict[str, int] = {}

_ALLOWED_LOOKUP_TABLES = frozenset({"quality_dimensions", "rating_sources"})


def _lookup_id(cursor, table: str, name: str) -> int:
    """Look up an ID by name from a lookup table. Cached per process."""
    if table not in _ALLOWED_LOOKUP_TABLES:
        raise ValueError(
            f"Table '{table}' is not in the allowed lookup tables: "
            f"{sorted(_ALLOWED_LOOKUP_TABLES)}"
        )
    key = f"{table}:{name}"
    if key not in _LOOKUP_CACHE:
        cursor.execute(
            f"SELECT id FROM {table} WHERE name = %s",
            (name,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"{table} has no entry named '{name}'")
        _LOOKUP_CACHE[key] = row["id"] if isinstance(row, dict) else row[0]
    return _LOOKUP_CACHE[key]


def score_finding(
    cursor,
    finding_id: int,
    finding: dict,
) -> None:
    """
    Insert two finding_quality rows (clarity + actionability) for one finding.

    Uses ON CONFLICT DO NOTHING for idempotency (AC5).
    """
    clarity = compute_clarity_score(finding)
    actionability = compute_actionability_score(finding)

    dimension_clarity_id = _lookup_id(cursor, "quality_dimensions", "clarity")
    dimension_action_id = _lookup_id(
        cursor, "quality_dimensions", "actionability"
    )
    auto_source_id = _lookup_id(cursor, "rating_sources", "auto")

    for dimension_id, score in (
        (dimension_clarity_id, clarity),
        (dimension_action_id, actionability),
    ):
        cursor.execute(
            """
            INSERT INTO finding_quality
                (finding_id, dimension_id, score, rated_by_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (finding_id, dimension_id, rated_by_id) DO NOTHING
            """,
            (finding_id, dimension_id, score, auto_source_id),
        )


def score_findings(
    cursor,
    findings: list[tuple[int, dict]],
) -> None:
    """
    Score a batch of findings. Each element is (finding_id, finding_dict).
    """
    for finding_id, finding_dict in findings:
        score_finding(cursor, finding_id, finding_dict)
