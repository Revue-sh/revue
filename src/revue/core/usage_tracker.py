"""
Usage tracking for Revue.io free tier enforcement.

Checks reviews_left before starting a review and fires a fire-and-forget
tracking call after completion.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

UPGRADE_URL = "https://revue.io/upgrade"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ReviewLimitError(RuntimeError):
    """Raised when the free-tier review limit has been reached."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_reviews_left(reviews_left: Optional[int]) -> None:
    """Raise :class:`ReviewLimitError` if the limit is exhausted.

    Args:
        reviews_left: Value from the license validation response.
            ``None`` means unlimited (Pro / Enterprise).

    Raises:
        ReviewLimitError: When ``reviews_left == 0``.
    """
    if reviews_left is None:
        return  # unlimited
    if reviews_left <= 0:
        raise ReviewLimitError(
            "You have used all of your free reviews for this billing period. "
            f"Upgrade to Indie ($9/mo) or Pro ($29/mo) for unlimited reviews: {UPGRADE_URL}"
        )


def track(
    key: str,
    repo_id: str,
    agents_used: list[str],
    duration_ms: int,
    *,
    track_url: str | None = None,
    _http_client: httpx.Client | None = None,
) -> None:
    """Fire-and-forget: POST usage data to the Revue API in a background thread.

    Failures are logged as warnings only — the review result is never blocked.

    Args:
        key: The REVUE_LICENSE_KEY.
        repo_id: Repository identifier.
        agents_used: Names of agents that participated in the review.
        duration_ms: Total review wall-clock time in milliseconds.
        track_url: Override for the usage API URL.  Falls back to the
            ``REVUE_USAGE_API_URL`` environment variable when *None*.
        _http_client: Injected httpx.Client for testing.
    """
    url = track_url or os.getenv("REVUE_USAGE_API_URL")
    payload = {
        "key": key,
        "repo_id": repo_id,
        "agents_used": agents_used,
        "duration_ms": duration_ms,
    }

    if _http_client is not None:
        # Synchronous path for tests (avoids threading complexity in unit tests)
        _post_usage(payload, _http_client, url)
    else:
        t = threading.Thread(target=_post_usage, args=(payload, None, url), daemon=True)
        t.start()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _post_usage(payload: dict, client: httpx.Client | None, url: str | None) -> None:
    """Perform the HTTP POST; log but never raise."""
    if not url:
        logger.debug("Usage tracking disabled (REVUE_USAGE_API_URL not set)")
        return
    try:
        if client is not None:
            resp = client.post(url, json=payload, timeout=10.0)
        else:
            with httpx.Client(timeout=10.0) as c:
                resp = c.post(url, json=payload)
        if resp.status_code not in (200, 201, 202, 204):
            logger.warning(
                "Usage tracking returned unexpected status %s — continuing.",
                resp.status_code,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Usage tracking failed (non-blocking): %s", exc)
