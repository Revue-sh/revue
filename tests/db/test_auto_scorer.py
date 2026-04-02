"""
Unit tests for src/db/auto_scorer.py — heuristic scoring functions.

These tests exercise the pure compute_*_score functions only.
No database required.
"""
import json
from pathlib import Path

import pytest

from src.db.auto_scorer import compute_clarity_score, compute_actionability_score


# ---------------------------------------------------------------------------
# Clarity heuristic tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "finding, expected",
    [
        # All criteria met: both fields, long issue, no vague words, file path
        (
            {
                "issue": "Unhandled exception in src/api/handler.py at line 55",
                "details": "The try block does not catch ValueError from parse_input",
            },
            5,
        ),
        # Both non-empty (+2), long issue (+1), has vague word (+0), file path (+1) = 4
        (
            {
                "issue": "Memory leak might exist in src/cache/manager.py pool logic",
                "details": "The connection pool never releases stale entries",
            },
            4,
        ),
        # Both non-empty (+2), long issue (+1), vague word (+0), no path (+0) = 3
        (
            {
                "issue": "This function could be refactored for readability",
                "details": "Complex nested logic",
            },
            3,
        ),
        # Both non-empty (+2), short issue (+0), no vague (+1), no path (+0) = 3
        (
            {
                "issue": "Typo in error msg",
                "details": "The word 'recieve' should be 'receive'",
            },
            3,
        ),
        # Both non-empty (+2), short issue (+0), vague (+0), no path (+0) = 2
        (
            {
                "issue": "Maybe fix this",
                "details": "Perhaps later",
            },
            2,
        ),
        # Only issue (+0), short (+0), no vague (+1), no path (+0) = 1
        (
            {
                "issue": "Broken",
                "details": "",
            },
            1,
        ),
    ],
    ids=[
        "all-criteria-met-score-5",
        "vague-word-with-file-path-score-4",
        "vague-long-no-path-score-3",
        "short-no-vague-both-filled-score-3",
        "vague-short-both-filled-score-2",
        "issue-only-short-score-1",
    ],
)
def test_clarity_heuristic_scoring(finding, expected):
    assert compute_clarity_score(finding) == expected


# ---------------------------------------------------------------------------
# Actionability heuristic tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "finding, expected",
    [
        # rec non-empty (+2), file path (+1), action verb (+1), exact change (+1) = 5
        (
            {
                "recommendation": "Remove the deprecated call in src/utils/helpers.py and replace with new API",
            },
            5,
        ),
        # rec non-empty (+2), backtick (+1), action verb (+1), no file path so no exact (+0) = 4
        (
            {
                "recommendation": "Add `assert value is not None` before the return statement",
            },
            4,
        ),
        # rec non-empty (+2), no code/path (+0), action verb (+1), no exact (+0) = 3
        (
            {
                "recommendation": "Refactor the logic to separate concerns",
            },
            3,
        ),
        # rec non-empty (+2), no code (+0), no action verb (+0), no exact (+0) = 2
        (
            {
                "recommendation": "This should be improved",
            },
            2,
        ),
        # rec empty (+0) = 0 → clamped 1
        (
            {
                "recommendation": "",
            },
            1,
        ),
    ],
    ids=[
        "file-path-and-action-verb-score-5",
        "code-snippet-and-verb-score-4",
        "action-verb-only-score-3",
        "rec-only-no-specifics-score-2",
        "empty-rec-score-1",
    ],
)
def test_actionability_heuristic_scoring(finding, expected):
    assert compute_actionability_score(finding) == expected


# ---------------------------------------------------------------------------
# Minimum score test
# ---------------------------------------------------------------------------

def test_minimum_score_is_one():
    """Empty finding should produce score 1 on both dimensions, not 0."""
    empty = {"issue": "", "details": "", "recommendation": ""}
    assert compute_clarity_score(empty) == 1
    assert compute_actionability_score(empty) == 1

    # Also test with missing keys entirely
    sparse = {}
    assert compute_clarity_score(sparse) == 1
    assert compute_actionability_score(sparse) == 1


# ---------------------------------------------------------------------------
# Synthetic benchmark accuracy (ground truth fixture)
# ---------------------------------------------------------------------------

def test_synthetic_benchmark_accuracy():
    """Auto-scorer output must be within +/-1 of hand-scored ground truth."""
    fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / "scorer_ground_truth.json"
    with open(fixture_path) as f:
        ground_truth = json.load(f)

    assert len(ground_truth) == 5, "Expected 5 ground-truth entries"

    for i, entry in enumerate(ground_truth):
        finding = entry["finding"]
        exp_clarity = entry["expected_clarity"]
        exp_actionability = entry["expected_actionability"]

        actual_clarity = compute_clarity_score(finding)
        actual_actionability = compute_actionability_score(finding)

        assert abs(actual_clarity - exp_clarity) <= 1, (
            f"Finding {i}: clarity {actual_clarity} not within ±1 of expected {exp_clarity}"
        )
        assert abs(actual_actionability - exp_actionability) <= 1, (
            f"Finding {i}: actionability {actual_actionability} not within ±1 of expected {exp_actionability}"
        )
