"""Shared resolution of on-disk cache file paths used by the skill (REVUE-280).

Two caches live in ``~/.config/revue/``:

* ``licence-cache.json``  — written by ``revue_skill.validate`` after a
  successful /validate round-trip; consumed by ``cost_footer`` and
  ``upgrade_prompt`` to decide what tier-aware messaging to render.
* ``usage-cache.json``    — written by ``update_usage_cache`` to track local
  monthly review counts; consumed by ``cost_footer`` to compute savings.

Both helpers honour an env-var override so the unit tests can redirect
reads/writes into ``tmp_path``. Production callers leave the env vars unset
and pick up the default ``~/.config/revue/`` location.

Also exposes :func:`atomic_json_write` — the single source of truth for the
write contract used by both caches (REVUE-280 code-review #803516272). Any
future change to the atomic-write semantics (``os.fsync``, switching to
``atomicwrites``, cleanup ordering) is made here once.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


_DEFAULT_CACHE_DIR = Path.home() / ".config" / "revue"


def get_licence_cache_path() -> Path:
    """Resolve the licence-cache path.

    Honours ``REVUE_LICENCE_CACHE_PATH`` for tests; falls back to
    ``~/.config/revue/licence-cache.json``. Returns a path even when the file
    does not yet exist — existence checks are the caller's responsibility.
    """
    override = os.environ.get("REVUE_LICENCE_CACHE_PATH")
    if override:
        return Path(override)
    return _DEFAULT_CACHE_DIR / "licence-cache.json"


def get_usage_cache_path() -> Path:
    """Resolve the usage-cache path.

    Honours ``REVUE_USAGE_CACHE_PATH`` for tests; falls back to
    ``~/.config/revue/usage-cache.json``. Returns a path even when the file
    does not yet exist — existence checks are the caller's responsibility.
    """
    override = os.environ.get("REVUE_USAGE_CACHE_PATH")
    if override:
        return Path(override)
    return _DEFAULT_CACHE_DIR / "usage-cache.json"


def atomic_json_write(
    path: Path, data: dict, *, file_mode: int | None = None
) -> None:
    """Write ``data`` as JSON to ``path`` atomically.

    The destination is replaced via ``os.replace`` so concurrent readers
    never observe a half-written file, and concurrent writers cannot lose
    each other's writes mid-flush. The temp file lives in the same
    directory as the destination so the replace stays on a single
    filesystem (``os.replace`` is only atomic within a mount).

    The cleanup uses ``try/finally`` rather than ``except BaseException``
    so ``KeyboardInterrupt`` and ``SystemExit`` propagate cleanly while
    leftover temp files are still removed (REVUE-280 code-review
    #803516267, #803516280). On the success path the temp file has been
    consumed by ``Path.replace`` and ``unlink(missing_ok=True)`` is a
    no-op; on any failure path the leftover ``.tmp`` is removed.

    Args:
        path: Destination path. Parent directory must already exist —
            callers that require specific directory permissions (e.g.
            licence cache at mode 0700) own the ``mkdir`` + ``chmod``.
        data: JSON-serialisable payload.
        file_mode: Optional ``chmod`` applied to the temp file BEFORE the
            atomic replace, so the destination ends up at the requested
            mode without a window where the file exists at the default
            umask. Pass e.g. ``0o600`` for the licence cache.
    """
    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}-", suffix=".tmp"
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        if file_mode is not None:
            os.chmod(tmp_path, file_mode)
        tmp_path.replace(path)
    finally:
        # Successful path: tmp_path was consumed by ``replace`` and unlink
        # is a no-op. Failure path: tmp_path still exists and is removed.
        # KeyboardInterrupt/SystemExit propagate because there's no
        # ``except`` catching them.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
