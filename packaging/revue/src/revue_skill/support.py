"""REVUE-359 — support contact policy for all CLI error paths.

Every activation, install, or licence-validation failure surfaces a single
actionable next step: "Need help? Email support@revue.sh". This module owns the
support-contact *policy* (the copy) — and nothing else. It performs no I/O, so
any importer can reference the contact string without triggering side effects.

REVUE-359 code-review (#573): the output mechanism (writing to stderr) is the
caller's concern — the imperative shell — not this module's. Keeping the policy
pure means the module has exactly one reason to change (the copy), satisfying
SRP and leaving the where/how-to-print decision to each call site.
"""
from __future__ import annotations

from typing import Final

SUPPORT_EMAIL: Final[str] = "support@revue.sh"
"""The canonical support email address. Never hardcoded elsewhere — always
reference this constant so changes are atomic across all error paths."""

SUPPORT_LINE: Final[str] = f"Need help? Email {SUPPORT_EMAIL}"
"""The exact copy pinned by REVUE-359 AC1. Used by all error paths."""


def support_footer() -> str:
    """Return the support-contact footer line.

    Pure — returns the policy string and performs no I/O. Callers (the
    imperative shell) decide where to write it; CLI error paths print it to
    stderr via ``print(support_footer(), file=sys.stderr)``.
    """
    return SUPPORT_LINE
