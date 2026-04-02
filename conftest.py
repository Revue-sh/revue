"""Root conftest — ensures src/ is on sys.path so ``from revue …`` imports work."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "src"))


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: marks tests requiring a live database")
