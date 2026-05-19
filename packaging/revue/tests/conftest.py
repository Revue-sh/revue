"""Shared pytest fixtures for the revue packaging tests."""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGING_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGING_DIR.parent.parent

# Make `revue_skill` importable without a `pip install -e .` round-trip.
SRC = PACKAGING_DIR / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
