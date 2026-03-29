"""License key generation."""
from __future__ import annotations

import secrets

PREFIX = "lic_"


def generate_license_key() -> str:
    """Generate a license key: lic_ + 32 hex chars."""
    return PREFIX + secrets.token_hex(16)
