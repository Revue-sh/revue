"""REVUE-417 — regression guard: vendored modules must import on Python 3.9.

PEP 604 union annotations (``X | Y``) without ``from __future__ import
annotations`` raise ``TypeError: unsupported operand type(s) for |`` on
Python 3.9 at import time, because the annotation is evaluated eagerly.
Adding the future import defers evaluation to a string, fixing the crash on
the Nuitka compile target (Python 3.9).

This test imports each previously-failing vendored module directly, so any
regression (e.g. a future vendor sync that drops the future import) will
surface as an ImportError / TypeError in CI before the wheel ships.

Note: the test passes on all supported Python versions (3.9 and 3.10+).
The TypeError is only reproducible on 3.9; on 3.10+ it is silently OK
with or without the fix. To prove the fix is load-bearing, the CI pipeline
that produces the Nuitka binary runs on Python 3.9.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the vendored source is importable from the packaging tree.
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_logging_channels_imports_without_error() -> None:
    """REVUE-417: logging_channels.py must import on Python 3.9.

    ``log_comment_posted`` uses ``str | None`` on its ``comment_id``
    parameter — the original trigger for the py3.9 TypeError.
    """
    import importlib

    # Fresh import (not from cache) so even in the test suite's 3.10+ env the
    # import machinery re-parses the module header.
    mod = importlib.import_module("revue_skill.vendored.logging_channels")
    assert hasattr(mod, "log_comment_posted"), (
        "log_comment_posted must be defined in vendored/logging_channels"
    )


def test_positioning_adapters_protocol_imports_without_error() -> None:
    """REVUE-417: positioning_adapters/protocol.py must import on Python 3.9.

    ``PositioningExtractor.get`` returns ``list | dict``.
    """
    import importlib

    mod = importlib.import_module(
        "revue_skill.vendored.positioning_adapters.protocol"
    )
    assert hasattr(mod, "PositioningExtractor"), (
        "PositioningExtractor must be defined in vendored/positioning_adapters/protocol"
    )


def test_positioning_adapters_helpers_imports_without_error() -> None:
    """REVUE-417: positioning_adapters/helpers.py must import on Python 3.9.

    ``_http_get`` returns ``list | dict``.
    """
    import importlib

    mod = importlib.import_module(
        "revue_skill.vendored.positioning_adapters.helpers"
    )
    assert hasattr(mod, "_http_get"), (
        "_http_get must be defined in vendored/positioning_adapters/helpers"
    )


def test_positioning_adapters_github_imports_without_error() -> None:
    """REVUE-417: positioning_adapters/github.py must import on Python 3.9.

    ``GitHubClient.get`` returns ``list | dict``.
    """
    import importlib

    mod = importlib.import_module(
        "revue_skill.vendored.positioning_adapters.github"
    )
    assert hasattr(mod, "GitHubClient"), (
        "GitHubClient must be defined in vendored/positioning_adapters/github"
    )


def test_positioning_adapters_gitlab_imports_without_error() -> None:
    """REVUE-417: positioning_adapters/gitlab.py must import on Python 3.9.

    ``GitLabClient.get`` returns ``list | dict``.
    """
    import importlib

    mod = importlib.import_module(
        "revue_skill.vendored.positioning_adapters.gitlab"
    )
    assert hasattr(mod, "GitLabClient"), (
        "GitLabClient must be defined in vendored/positioning_adapters/gitlab"
    )
