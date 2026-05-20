"""Logging utilities for the revue package."""
from __future__ import annotations

from typing import Callable


class _Lazy:
    """Defers __str__ computation so logging only evaluates when the level is active.

    Usage::

        logging.debug("msg %s", _Lazy(lambda: expensive_call()))
    """
    __slots__ = ("_fn",)

    def __init__(self, fn: Callable[[], str]) -> None:
        self._fn = fn

    def __str__(self) -> str:
        return self._fn()
