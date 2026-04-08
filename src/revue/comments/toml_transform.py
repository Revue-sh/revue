"""JSON → TOML prompt transform for consolidator context (REVUE-110 AC7).

The PerPRCommentStore persists dedup state as JSON on disk.
Nova (the consolidator) receives a human-readable TOML snippet to understand
prior review context — so it can avoid repeating known findings.

TOML is **never written to disk** — it is generated on the fly for each
prompt invocation and discarded after the LLM call returns.
"""
from __future__ import annotations

import tomli_w


def comment_json_to_toml(data: dict) -> str:
    """Convert a PerPRCommentStore JSON dict to a TOML string for the consolidator prompt.

    The TOML output is structured for LLM readability:

        [meta]
        pr_number = 42
        platform = "bitbucket"

        [[findings]]
        file_path = "src/foo.py"
        fingerprint = "abc123def456789a"
        line_number = 10
        state = "unresolved"
        platform_comment_id = "12345"
        comment_body = "..."

    Args:
        data: Dict loaded from a ``{platform}-PR-{number}.json`` store file.

    Returns:
        TOML-encoded string ready for injection into a prompt.

    Raises:
        ValueError: If ``data`` is missing required top-level keys.
    """
    if "pr_number" not in data or "platform" not in data:
        raise ValueError("data must contain 'pr_number' and 'platform' keys")

    doc: dict = {
        "meta": {
            "pr_number": int(data["pr_number"]),
            "platform": str(data["platform"]),
        },
        "findings": [],
    }

    files: dict = data.get("files", {})
    for file_path, fingerprints in files.items():
        for fp_hex, entry in fingerprints.items():
            finding: dict = {
                "file_path": file_path,
                "fingerprint": fp_hex,
                "line_number": int(entry.get("line_number", 0)),
                "state": str(entry.get("state") or "unresolved"),
                "platform_comment_id": str(entry.get("platform_comment_id", "")),
                "comment_body": str(entry.get("comment_body", "")),
            }
            doc["findings"].append(finding)

    return tomli_w.dumps(doc)
