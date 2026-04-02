"""Finding fingerprinting for comment identity tracking."""
import hashlib


def fingerprint(file_path: str, line_number: int, issue_text: str) -> str:
    """Generate a stable fingerprint for a code review finding.

    Returns sha256[:16] of file_path:line_number:issue[:50].
    """
    key = f"{file_path}:{line_number}:{issue_text[:50]}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
