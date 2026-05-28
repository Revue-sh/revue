"""Update local usage cache (REVUE-280 dependency).

Maintains a local counter at ~/.config/revue/usage-cache.json tracking reviews
run this calendar month. Used by cost_footer.py to compute savings display.

The counter is independent of server-side telemetry (emit_usage.py).
Server telemetry is best-effort and may fail; this local counter is
maintained for display purposes and does not block the review.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from revue_skill.skill.cache_paths import atomic_json_write, get_usage_cache_path


def update_usage_cache() -> None:
    """Increment the monthly review counter in the local usage cache.

    Creates or updates ~/.config/revue/usage-cache.json with the structure:
    {
        "reviews_this_month": <count>,
        "cached_at": <unix_timestamp>,
        "month_reset_at": "YYYY-MM-DD HH:MM:SS"
    }

    Month boundaries are UTC calendar month (e.g., 2026-05-01 00:00:00 UTC).
    If the stored month_reset_at is in the past, the counter is reset to 1.
    Otherwise, it is incremented.

    Does not raise; silently returns on any error (best-effort).
    """
    from revue_core.core.logging_channels import Log

    cache_path = get_usage_cache_path()

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    month_start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    try:
        # Read existing cache if present
        if cache_path.exists():
            data = json.loads(cache_path.read_text())
            stored_month_start_str = data.get("month_reset_at", "")
            try:
                stored_month_start = datetime.fromisoformat(stored_month_start_str)
            except (ValueError, TypeError):
                stored_month_start = None

            # If month boundary hasn't changed, increment; else reset to 1
            if stored_month_start == month_start:
                data["reviews_this_month"] = data.get("reviews_this_month", 0) + 1
            else:
                data["reviews_this_month"] = 1
        else:
            data = {"reviews_this_month": 1}

        # Update metadata
        data["cached_at"] = int(now_utc.timestamp())
        data["month_reset_at"] = month_start.strftime("%Y-%m-%d %H:%M:%S")

        # Write atomically via the shared helper so parallel ``revue``
        # invocations cannot lose increments: without atomic replace, two
        # processes reading N and both writing N+1 would lose one count.
        # The same helper backs ``revue_skill.validate._write_cache``.
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(cache_path, data)

        Log.cli.debug(
            "[revue] updated usage cache: %d reviews this month",
            data["reviews_this_month"],
        )

    except Exception as exc:
        Log.cli.debug("[revue] failed to update usage cache: %s (best-effort)", exc)
