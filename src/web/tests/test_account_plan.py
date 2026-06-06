"""Unit tests for the Account → Plan page state derivation (REVUE-382).

Tests the pure ``derive_plan_state`` function that maps a
LicenseKey-or-None value to one of four states: "active", "lapsed",
"free", "not_activated".  Covers:
  - NULL-columns (REVUE-413 migration reality)
  - lapsed / inactive read
  - Free sub-case (tier=free, is_active=True)
  - Not-activated (no licence row at all)
  - Route: /account/plan redirects unauthenticated requests to /login
  - Route: /account/plan returns 200 for authenticated user
"""
from __future__ import annotations

import pytest

from models import LicenseKey


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_key(
    *,
    tier: str = "pro",
    is_active: bool = True,
    current_period_end: str | None = None,
    subscription_status: str | None = None,
    reviews_used_this_month: int = 3,
    reviews_limit: int | None = 100,
    last_validated_at: str | None = "2025-06-01T00:00:00",
) -> LicenseKey:
    """Build a LicenseKey for state-derivation tests.

    REVUE-382: ``last_validated_at`` defaults to a non-NULL value so the row is
    treated as ALREADY VALIDATED (active/free). Pass ``last_validated_at=None``
    to model a key that has never been activated (not_activated state).
    """
    return LicenseKey(
        id=1,
        workspace_id=1,
        key="lic_" + "a" * 32,
        tier=tier,
        reviews_used_this_month=reviews_used_this_month,
        reviews_limit=reviews_limit,
        period_reset_at=None,
        created_at="2025-01-01T00:00:00",
        is_active=is_active,
        current_period_end=current_period_end,
        subscription_status=subscription_status,
        last_validated_at=last_validated_at,
    )


# ---------------------------------------------------------------------------
# State derivation — pure function
# ---------------------------------------------------------------------------

class TestDerivePlanState:
    """derive_plan_state(licence_or_None) -> state string."""

    def _call(self, lic):
        from routes.dashboard_routes import derive_plan_state
        return derive_plan_state(lic)

    def test_active_pro(self):
        assert self._call(_make_key(tier="pro", is_active=True)) == "active"

    def test_active_indie(self):
        assert self._call(_make_key(tier="indie", is_active=True)) == "active"

    def test_free_tier_active_is_free_state(self):
        """Free is an Active sub-case but gets its own state for template branching."""
        assert self._call(_make_key(tier="free", is_active=True)) == "free"

    def test_lapsed_inactive_pro(self):
        """is_active=False with a paid tier → lapsed state."""
        assert self._call(_make_key(tier="pro", is_active=False)) == "lapsed"

    def test_lapsed_inactive_indie(self):
        assert self._call(_make_key(tier="indie", is_active=False)) == "lapsed"

    def test_free_inactive_is_not_activated_not_lapsed(self):
        """A free + inactive row is NOT lapsed: "Re-subscribe to Free" + a
        tier=free checkout form is nonsensical. It falls through to
        not_activated (the only actionable path is to re-activate the key)."""
        assert self._call(_make_key(tier="free", is_active=False)) == "not_activated"

    def test_free_inactive_with_validation_still_not_activated(self):
        """Even a previously-validated free key, once inactive, is not_activated
        (never lapsed) — the tier guard wins regardless of last_validated_at."""
        lic = _make_key(tier="free", is_active=False, last_validated_at="2025-06-01T00:00:00")
        assert self._call(lic) == "not_activated"

    def test_not_activated_none_key(self):
        """No licence row at all → not_activated."""
        assert self._call(None) == "not_activated"

    # not_activated: has a key but never validated (last_validated_at IS NULL)
    def test_not_activated_never_validated_free(self):
        """A free key that has never been validated → not_activated (AC5)."""
        lic = _make_key(tier="free", is_active=True, last_validated_at=None)
        assert self._call(lic) == "not_activated"

    def test_not_activated_never_validated_paid(self):
        """not_activated is tier-agnostic: a paid key never validated is still
        not_activated (the user must run `revue activate <key>` first)."""
        lic = _make_key(tier="pro", is_active=True, last_validated_at=None)
        assert self._call(lic) == "not_activated"

    def test_lapsed_precedence_over_never_validated(self):
        """is_active=0 wins over never-validated: a once-active-then-lapsed
        key stays lapsed even if last_validated_at is somehow NULL."""
        lic = _make_key(tier="pro", is_active=False, last_validated_at=None)
        assert self._call(lic) == "lapsed"

    # NULL-columns (REVUE-413 migration reality)
    def test_active_with_null_period_end(self):
        """NULL current_period_end does not crash or change state."""
        lic = _make_key(tier="pro", is_active=True, current_period_end=None)
        assert self._call(lic) == "active"

    def test_active_with_null_subscription_status(self):
        lic = _make_key(tier="pro", is_active=True, subscription_status=None)
        assert self._call(lic) == "active"

    def test_active_both_null_columns(self):
        lic = _make_key(
            tier="indie",
            is_active=True,
            current_period_end=None,
            subscription_status=None,
        )
        assert self._call(lic) == "active"

    def test_lapsed_with_null_columns(self):
        """Lapsed state is derived from is_active alone; NULL columns are irrelevant."""
        lic = _make_key(
            tier="pro",
            is_active=False,
            current_period_end=None,
            subscription_status=None,
        )
        assert self._call(lic) == "lapsed"


# ---------------------------------------------------------------------------
# masked_key_display helper
# ---------------------------------------------------------------------------

class TestMaskedKeyDisplay:
    def _call(self, key: str) -> str:
        from routes.dashboard_routes import masked_key_display
        return masked_key_display(key)

    def test_shows_prefix_and_last4(self):
        key = "lic_" + "a" * 28 + "1234"
        result = self._call(key)
        assert result.startswith("lic_")
        assert result.endswith("1234")
        assert "•" in result

    def test_full_key_not_in_display(self):
        """The masked display must never expose the full key as visible text."""
        key = "lic_" + "a" * 32
        result = self._call(key)
        assert result != key

    def test_short_fallback(self):
        """Keys shorter than expected don't crash."""
        key = "lic_ab"
        result = self._call(key)
        # Should return something reasonable without raising
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# last_verified_ago — "Last verified Nh ago" formatter (AC2)
# ---------------------------------------------------------------------------

class TestLastVerifiedAgo:
    """Formats a naive-UTC last_validated_at timestamp into 'Last verified Nh ago'."""

    def _call(self, ts):
        from routes.dashboard_routes import last_verified_ago
        return last_verified_ago(ts)

    def test_null_returns_not_verified_fallback(self):
        """NULL last_validated_at → graceful fallback, never raw None (AC2)."""
        result = self._call(None)
        assert result is not None
        assert "None" not in result
        assert "not verified" in result.lower()

    def test_hours_ago(self):
        from datetime import datetime, timezone, timedelta
        ts = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)).isoformat()
        result = self._call(ts)
        assert "3" in result
        assert "ago" in result.lower()

    def test_less_than_one_hour_does_not_show_zero_hours(self):
        """A recent validation (<1h) must not render as '0h ago'."""
        from datetime import datetime, timezone, timedelta
        ts = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)).isoformat()
        result = self._call(ts)
        assert "0h" not in result.lower().replace(" ", "")
        assert "ago" in result.lower() or "just now" in result.lower()

    def test_days_ago_for_old_validation(self):
        from datetime import datetime, timezone, timedelta
        # 2 days + a small margin so the floor-division lands cleanly on 2d
        # (avoids a boundary flake if the test straddles an exact 48h tick).
        ts = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(days=2, minutes=1)
        ).isoformat()
        result = self._call(ts)
        # Pin the exact rendered string (≥48h → "Nd ago"), not just "ago".
        assert result == "Last verified 2d ago"

    def test_malformed_timestamp_returns_fallback(self):
        """A non-ISO string must not crash — falls back gracefully."""
        result = self._call("not-a-timestamp")
        assert isinstance(result, str)
        assert "None" not in result


# ---------------------------------------------------------------------------
# Licence accessors — shared query, filter toggled by include_inactive
# ---------------------------------------------------------------------------

class TestLicenceAccessors:
    """get_license_for_user (active-only) vs get_any_license_for_user (unfiltered).

    Both delegate to the private ``_get_license_for_user`` helper; the only
    behavioural difference is the ``is_active = 1`` filter. These tests pin both
    delegation paths so the DRY refactor can't silently drift.
    """

    def _seed_user_with_licence(self, conn, *, is_active: bool):
        import uuid
        from auth import hash_password
        from license import generate_license_key
        from models import (
            create_user, create_workspace, create_license_key,
            set_license_subscription_state,
        )
        email = f"acc-{uuid.uuid4().hex[:8]}@test.com"
        user_id = create_user(conn, email, hash_password("password1"))
        ws_id = create_workspace(conn, user_id, "ws")
        key = generate_license_key()
        create_license_key(conn, ws_id, key, tier="pro")
        if not is_active:
            set_license_subscription_state(conn, user_id, is_active=False)
        conn.commit()
        return user_id, key

    def test_active_row_visible_to_both_accessors(self, _tmp_db):
        import sqlite3
        import database
        from models import get_license_for_user, get_any_license_for_user

        conn = sqlite3.connect(database.get_db_path())
        conn.row_factory = sqlite3.Row
        user_id, key = self._seed_user_with_licence(conn, is_active=True)

        filtered = get_license_for_user(conn, user_id)
        unfiltered = get_any_license_for_user(conn, user_id)
        conn.close()

        assert filtered is not None and filtered.key == key
        assert unfiltered is not None and unfiltered.key == key

    def test_lapsed_row_hidden_from_filtered_but_visible_to_unfiltered(self, _tmp_db):
        """The is_active=0 row is invisible to get_license_for_user but the
        unfiltered accessor returns it — this is what makes the Lapsed state
        reachable (REVUE-382)."""
        import sqlite3
        import database
        from models import get_license_for_user, get_any_license_for_user

        conn = sqlite3.connect(database.get_db_path())
        conn.row_factory = sqlite3.Row
        user_id, key = self._seed_user_with_licence(conn, is_active=False)

        filtered = get_license_for_user(conn, user_id)
        unfiltered = get_any_license_for_user(conn, user_id)
        conn.close()

        # Active-only accessor hides the lapsed row.
        assert filtered is None
        # Unfiltered accessor returns it (is_active preserved as False).
        assert unfiltered is not None
        assert unfiltered.key == key
        assert unfiltered.is_active is False

    def test_no_row_returns_none_for_both(self, _tmp_db):
        import sqlite3
        import database
        from auth import hash_password
        from models import create_user, get_license_for_user, get_any_license_for_user

        conn = sqlite3.connect(database.get_db_path())
        conn.row_factory = sqlite3.Row
        # Bare user, no workspace/licence row.
        user_id = create_user(conn, "bare@test.com", hash_password("password1"))
        conn.commit()

        assert get_license_for_user(conn, user_id) is None
        assert get_any_license_for_user(conn, user_id) is None
        conn.close()


# ---------------------------------------------------------------------------
# Route: /account/plan — auth + response
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_account_plan_redirects_when_unauthenticated(client):
    resp = await client.get("/account/plan", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_account_plan_renders_for_authenticated_user(client):
    # Sign up creates a free user with a licence key.
    resp = await client.post(
        "/signup",
        data={"email": "plan-test@test.com", "password": "password1"},
        follow_redirects=False,
    )
    cookie = resp.cookies.get("revue_session")
    client.cookies.set("revue_session", cookie)

    resp = await client.get("/account/plan")
    assert resp.status_code == 200
    # Should contain the plan state indicator
    content = resp.content
    assert b"account/plan" in content.lower() or b"Plan" in content


@pytest.mark.asyncio
async def test_account_plan_free_state_no_command_box(client):
    """Free tier (validated) renders Upgrade CTA; Command-Box is absent (AC7).

    A fresh signup is in the not_activated state (never validated), so we stamp
    last_validated_at to model a free user who HAS activated their CLI — that's
    the Free state AC7 describes.
    """
    import sqlite3
    import database
    from models import get_user_by_email, get_any_license_for_user, touch_license_validated

    resp = await client.post(
        "/signup",
        data={"email": "free-plan@test.com", "password": "password1"},
        follow_redirects=False,
    )
    cookie = resp.cookies.get("revue_session")
    client.cookies.set("revue_session", cookie)

    # Mark the free user's licence as validated → Free state (not not_activated).
    conn = sqlite3.connect(database.get_db_path())
    conn.row_factory = sqlite3.Row
    user = get_user_by_email(conn, "free-plan@test.com")
    lic = get_any_license_for_user(conn, user.id)
    touch_license_validated(conn, lic.id)
    conn.commit()
    conn.close()

    resp = await client.get("/account/plan")
    assert resp.status_code == 200
    content = resp.content
    # Free state: Upgrade CTA present
    assert b"Upgrade" in content
    # Free state: Command-Box absent (no revue activate command shown)
    assert b"revue activate" not in content


@pytest.mark.asyncio
async def test_account_plan_active_pro_renders_licence_active(client):
    """Active Pro renders 'Licence active' badge and tier badge (AC2)."""
    import sqlite3
    import uuid
    import database
    from models import (
        create_user, create_workspace, create_license_key,
        set_license_subscription_state, update_user_tier,
        get_any_license_for_user, touch_license_validated,
    )
    from license import generate_license_key
    from auth import hash_password

    db_path = database.get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    email = f"pro-plan-{uuid.uuid4().hex[:6]}@test.com"
    user_id = create_user(conn, email, hash_password("password1"))
    ws_id = create_workspace(conn, user_id, "ws")
    key = generate_license_key()
    create_license_key(conn, ws_id, key, tier="pro", reviews_limit=500)
    update_user_tier(conn, user_id, "pro")
    # Stamp validated so the row resolves to the Active state (not not_activated).
    lic = get_any_license_for_user(conn, user_id)
    touch_license_validated(conn, lic.id)
    conn.commit()
    conn.close()

    resp = await client.post(
        "/signup",
        data={"email": f"tmp-{uuid.uuid4().hex[:6]}@test.com", "password": "password1"},
        follow_redirects=False,
    )
    # Sign in as the pro user directly via login
    resp = await client.post(
        "/login",
        data={"email": email, "password": "password1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    cookie = resp.cookies.get("revue_session")
    client.cookies.set("revue_session", cookie)

    resp = await client.get("/account/plan")
    assert resp.status_code == 200
    content = resp.content
    assert b"Licence active" in content or b"active" in content.lower()
    assert b"Pro" in content


@pytest.mark.asyncio
async def test_account_plan_lapsed_no_invalid_word(client):
    """Lapsed state never uses the word 'invalid' (AC6)."""
    import sqlite3
    import uuid
    import database
    from models import (
        create_user, create_workspace, create_license_key,
        set_license_subscription_state, update_user_tier,
    )
    from license import generate_license_key
    from auth import hash_password

    db_path = database.get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    email = f"lapsed-{uuid.uuid4().hex[:6]}@test.com"
    user_id = create_user(conn, email, hash_password("password1"))
    ws_id = create_workspace(conn, user_id, "ws")
    key = generate_license_key()
    create_license_key(conn, ws_id, key, tier="pro", reviews_limit=500)
    update_user_tier(conn, user_id, "pro")
    # Simulate lapsed: set is_active=False
    set_license_subscription_state(
        conn, user_id, is_active=False,
        subscription_status="canceled",
        current_period_end="2025-01-01T00:00:00",
    )
    conn.commit()
    conn.close()

    resp = await client.post(
        "/login",
        data={"email": email, "password": "password1"},
        follow_redirects=False,
    )
    cookie = resp.cookies.get("revue_session")
    client.cookies.set("revue_session", cookie)

    resp = await client.get("/account/plan")
    assert resp.status_code == 200
    content = resp.content
    # Must NOT contain the word "invalid"
    assert b"invalid" not in content.lower()
    # Primary CTA: Re-subscribe
    assert b"Re-subscribe" in content or b"re-subscribe" in content.lower()
    # Secondary CTA: Downgrade to Free (AC6)
    assert b"Downgrade to Free" in content


@pytest.mark.asyncio
async def test_account_plan_not_activated_bare_user_renders_without_crash(client):
    """Not-activated state with NO licence row at all renders without crashing.

    Edge case: a user with no workspace/licence row (e.g. manually-created).
    The Command-Box falls back to the `<your-key>` placeholder since there's no
    key to pre-fill. The pre-filled-key case (the normal one) is covered by
    ``test_account_plan_not_activated_prefills_real_key`` below.
    """
    import sqlite3
    import uuid
    import database
    from models import create_user
    from auth import hash_password

    db_path = database.get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    email = f"nokey-{uuid.uuid4().hex[:6]}@test.com"
    create_user(conn, email, hash_password("password1"))
    conn.commit()
    conn.close()

    resp = await client.post(
        "/login",
        data={"email": email, "password": "password1"},
        follow_redirects=False,
    )
    cookie = resp.cookies.get("revue_session")
    client.cookies.set("revue_session", cookie)

    resp = await client.get("/account/plan")
    assert resp.status_code == 200
    content = resp.content
    # Not-activated branch renders: shows activation instructions
    assert b"Not activated" in content or b"not activated" in content.lower()
    # "Prefer a browser?" secondary link is present (AC5)
    assert b"/activate" in content


@pytest.mark.asyncio
async def test_account_plan_not_activated_prefills_real_key(client):
    """AC5: a freshly signed-up user (has a key, never validated) sees the
    Activation Command-Box pre-filled with their OWN real licence key.

    - State is not_activated because last_validated_at IS NULL.
    - The Copy payload (data-copy-payload) is the FULL `revue activate <key>`
      command containing the user's actual key (matching ^lic_[a-f0-9]{32}$).
    - The full key is NOT in the visible text (masked mode).
    """
    import re
    import sqlite3
    import database
    from models import get_user_by_email, get_any_license_for_user

    email = "prefill@test.com"
    resp = await client.post(
        "/signup",
        data={"email": email, "password": "password1"},
        follow_redirects=False,
    )
    cookie = resp.cookies.get("revue_session")
    client.cookies.set("revue_session", cookie)

    # Read the user's real key from their own row.
    conn = sqlite3.connect(database.get_db_path())
    conn.row_factory = sqlite3.Row
    user = get_user_by_email(conn, email)
    lic = get_any_license_for_user(conn, user.id)
    conn.close()
    real_key = lic.key
    assert re.match(r"^lic_[a-f0-9]{32}$", real_key), f"seed key shape: {real_key}"
    # Never validated yet → not_activated.
    assert lic.last_validated_at is None

    resp = await client.get("/account/plan")
    assert resp.status_code == 200
    html = resp.content.decode()

    # not_activated branch is rendered.
    assert "Not activated" in html or "not activated" in html.lower()

    # AC5: Copy payload pre-filled with the user's REAL key.
    expected_payload = f"revue activate {real_key}"
    assert f'data-copy-payload="{expected_payload}"' in html, (
        "Command-Box must pre-fill the authenticated user's own key"
    )
    # Security: the full key must NOT appear as visible command text (masked).
    # The only place the full key appears is inside the data-copy-payload attr.
    visible_occurrences = html.count(real_key)
    assert visible_occurrences == 1, (
        f"full key should appear exactly once (in data-copy-payload), "
        f"found {visible_occurrences}"
    )


@pytest.mark.asyncio
async def test_account_plan_not_activated_only_renders_own_key(client):
    """Security invariant: the not-activated Command-Box renders ONLY the
    authenticated user's key, never another user's."""
    import sqlite3
    import database
    from models import get_user_by_email, get_any_license_for_user

    # User A signs up (their key must NOT leak to B's page).
    await client.post(
        "/signup",
        data={"email": "owner-a@test.com", "password": "password1"},
        follow_redirects=False,
    )
    conn = sqlite3.connect(database.get_db_path())
    conn.row_factory = sqlite3.Row
    a = get_user_by_email(conn, "owner-a@test.com")
    a_key = get_any_license_for_user(conn, a.id).key
    conn.close()

    # User B signs up and views their plan page.
    resp = await client.post(
        "/signup",
        data={"email": "owner-b@test.com", "password": "password1"},
        follow_redirects=False,
    )
    cookie = resp.cookies.get("revue_session")
    client.cookies.set("revue_session", cookie)

    resp = await client.get("/account/plan")
    html = resp.content.decode()
    # B's page must never contain A's key.
    assert a_key not in html


# ---------------------------------------------------------------------------
# Review #3 — cookie re-issue, currency symbol, lapsed secondary CTA
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_account_plan_reissues_session_cookie_with_new_tier(client):
    """The handler re-issues the session cookie reflecting the DB tier, so a
    webhook upgrade (DB tier changed, cookie stale) updates the badge without
    re-login. A bare session["tier"]= mutation would be a no-op."""
    import sqlite3
    import database
    import auth
    from models import get_user_by_email, update_user_tier

    # Sign up as free (cookie issued with tier=free).
    resp = await client.post(
        "/signup",
        data={"email": "reissue@test.com", "password": "password1"},
        follow_redirects=False,
    )
    cookie = resp.cookies.get("revue_session")
    client.cookies.set("revue_session", cookie)

    # Simulate a webhook upgrade: DB tier → pro, but the cookie still says free.
    conn = sqlite3.connect(database.get_db_path())
    conn.row_factory = sqlite3.Row
    user = get_user_by_email(conn, "reissue@test.com")
    update_user_tier(conn, user.id, "pro")
    conn.commit()
    conn.close()

    resp = await client.get("/account/plan")
    assert resp.status_code == 200

    # The response must Set-Cookie a NEW session whose decoded tier is pro.
    new_cookie = resp.cookies.get("revue_session")
    assert new_cookie is not None, "handler must re-issue the session cookie"
    decoded = auth._get_serializer().loads(new_cookie)
    assert decoded["tier"] == "pro", (
        f"re-issued cookie must reflect the DB tier, got {decoded['tier']!r}"
    )


@pytest.mark.asyncio
async def test_account_plan_free_cta_shows_currency_symbol(client):
    """The Free Upgrade CTA renders the currency symbol (not blank) in '$9/mo'."""
    import sqlite3
    import database
    from config import CURRENCY_SYMBOL
    from models import get_user_by_email, get_any_license_for_user, touch_license_validated

    resp = await client.post(
        "/signup",
        data={"email": "symbol@test.com", "password": "password1"},
        follow_redirects=False,
    )
    cookie = resp.cookies.get("revue_session")
    client.cookies.set("revue_session", cookie)

    # Validate the free key so the page resolves to the Free state (not not_activated).
    conn = sqlite3.connect(database.get_db_path())
    conn.row_factory = sqlite3.Row
    user = get_user_by_email(conn, "symbol@test.com")
    lic = get_any_license_for_user(conn, user.id)
    touch_license_validated(conn, lic.id)
    conn.commit()
    conn.close()

    resp = await client.get("/account/plan")
    assert resp.status_code == 200
    html = resp.content.decode()
    # The price line must carry the symbol, e.g. "$9/mo" — never a blank "9/mo".
    assert f"{CURRENCY_SYMBOL}9/mo" in html
    assert ">9/mo" not in html  # guard against the symbol rendering blank


@pytest.mark.asyncio
async def test_account_plan_renders_both_modes(client):
    """REVUE-408: the Account → Plan page reflects both modes (CLI primary, CI
    complementary) using the shared two-mode partial."""
    resp = await client.post(
        "/signup",
        data={"email": "twomode-plan@test.com", "password": "password1"},
        follow_redirects=False,
    )
    cookie = resp.cookies.get("revue_session")
    client.cookies.set("revue_session", cookie)

    resp = await client.get("/account/plan")
    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'data-mode="cli"' in html
    assert 'data-mode="ci"' in html
    assert "before you commit" in html.lower()
    assert "/docs/ci-setup" in html
    assert html.index('data-mode="cli"') < html.index('data-mode="ci"')
