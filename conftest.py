"""Root conftest — ensures src/ is on sys.path so ``from revue …`` imports work."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
