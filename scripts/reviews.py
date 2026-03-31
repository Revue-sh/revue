#!/usr/bin/env python3
"""Wrapper script for reviews CLI (adds src to Python path)."""

import sys
from pathlib import Path

# Add src to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from cli.reviews import cli

if __name__ == "__main__":
    cli()
