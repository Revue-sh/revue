"""Finding fingerprinting for comment identity tracking (REVUE-110 AC3).

Formula (fallback — issue_type is currently free-text, not a normalised enum):
    sha256(file_path + ":" + hunk_start_line)[:16]

Full formula (TODO — when issue_type is normalised to an enum value):
    sha256(file_path + ":" + issue_type_enum + ":" + hunk_start_line)[:16]

Implementation note: issue_type must be normalised to a stable enum
(e.g. "null_pointer", "sql_injection") before it can be included.
Until then, including free-text AI output in the fingerprint causes
instability across re-reviews (the root cause fixed by this story).

hunk_start_line:
    The "+A" value from the nearest preceding @@ -X,Y +A,B @@ diff header.
    All findings within the same diff hunk share the same hunk_start,
    so fingerprints are stable for ±5 line offsets caused by context shifts.
"""
from __future__ import annotations

import hashlib
import re

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", re.MULTILINE)


def hunk_start_for_line(diff: str, line_number: int) -> int:
    """Return the hunk start line (+A) for the diff hunk that contains *line_number*.

    Walks all @@ headers in *diff* and returns the start of the last hunk
    whose start_line <= line_number. Falls back to *line_number* itself when
    there is no diff content or when line_number precedes all hunks.
    """
    best = None
    for m in _HUNK_HEADER.finditer(diff):
        hunk_start = int(m.group(1))
        if hunk_start <= line_number:
            best = hunk_start
    return best if best is not None else line_number


def fingerprint(file_path: str, line_number: int, diff: str = "") -> str:
    """Generate a stable fingerprint for a code review finding.

    Uses hunk_start_for_line to anchor to the diff hunk rather than the
    exact finding line, giving stability for ±N line offsets.

    Returns sha256[:16] hex string.

    Args:
        file_path:   Repository-relative path of the reviewed file.
        line_number: Approximate line number of the finding (from AI output).
        diff:        Raw unified diff for the file. When empty, line_number
                     is used directly (safe fallback for tests / non-diff contexts).
    """
    hunk_start = hunk_start_for_line(diff, line_number)
    key = f"{file_path}:{hunk_start}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
