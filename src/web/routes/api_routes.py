"""License validation and usage tracking API routes."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from rate_limiter import (
    check_ip_rate_limit,
    check_key_rate_limit,
    emit_flood_event,
    log_activation_attempt,
    validate_activation_headers,
    ActivationRateLimitError,
)
from pydantic import BaseModel, Field, ValidationError

from database import REVIEWS_LIMIT_BY_TIER, get_db
from auth import get_session
from jwt_signing import JWTSigningKeyMissing, sign_licence_jwt
from jwt_verify import decode_licence_jwt
from models import get_license_by_key, get_active_license_for_workspace, increment_usage, reset_monthly_counter, create_review_run, get_all_runs_for_user, get_analytics, record_usage_event, count_usage_events_since_month_start
import jwt as pyjwt

router = APIRouter()

ALL_AGENTS = [
    "orchestrator", "security-analyst", "performance-expert",
    "code-quality-expert", "architecture-reviewer", "consolidator", "sage",
]

AGENTS_BY_TIER: dict[str, list[str]] = {
    "free": ["orchestrator", "code-quality-expert", "consolidator"],
    "indie": ALL_AGENTS,
    "pro": ALL_AGENTS,
    "enterprise_starter": ALL_AGENTS,
    "enterprise_growth": ALL_AGENTS,
    "enterprise_plus": ALL_AGENTS,
}


class ValidateRequest(BaseModel):
    key: str
    repo_id: str = ""
    ci_run_id: str = ""


class ActivateRequest(BaseModel):
    """REVUE-277 Phase 2: payload for POST /api/v2/licence/activate."""

    key: str
    machine_fingerprint: str


class LicenceValidateRequest(BaseModel):
    """REVUE-278 AC1: payload for POST /api/v2/licence/validate.

    The skill sends the JWT read from ~/.config/revue/licence.jwt. The server
    verifies the signature, extracts the tier and workspace_id, and returns a
    validation response with refresh_after_ts (server-issued cache horizon) and
    optionally a refreshed_jwt.

    The 4 KiB cap is the same asymmetric-attack-surface defence ``ActivateRequest``
    applies to ``machine_fingerprint`` — real RS256 licence JWTs are under 2 KB;
    an attacker POSTing a 1 MB string would otherwise burn CPU on the failing
    decode and bloat the request log."""

    jwt: str = Field(max_length=4096)


class UsageEmitRequest(BaseModel):
    """REVUE-278 AC6: payload for POST /api/v2/usage/emit — per-invocation
    telemetry from ``/revue-local`` (or any skill invoking ``/validate``).

    The client supplies a signed JWT (same one issued by /activate) so the
    server can derive workspace_id from the verified claims rather than
    trusting a client-supplied value — otherwise any unauthenticated caller
    could poison another tenant's usage counters or inflate billing.

    The client supplies the epoch-seconds timestamp of invocation (``ts``);
    the server stamps received_at at write time so billing windows use the
    authoritative server timeline, not client clocks.

    JWT length is capped at 4 KiB for the same reason as
    ``LicenceValidateRequest`` — symmetric defence across both authenticated
    endpoints."""

    jwt: str = Field(max_length=4096)
    reviews_run: int
    findings_count: int
    ts: int  # epoch seconds — client-supplied invocation time


# S9: enforce length cap + charset on the client-supplied fingerprint
# BEFORE it gets signed into a JWT claim. Untrusted input that gets
# wrapped in a cryptographic envelope is still untrusted input — the
# server must refuse oversized payloads (1 MB JWTs would let a client
# bloat our token store and inflate every CLI call) and reject control
# characters / shell metacharacters that have no place in an opaque
# fingerprint.
_FINGERPRINT_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
_INVALID_FINGERPRINT_BODY: dict = {
    "error": "invalid_fingerprint",
    "message": "machine_fingerprint must be 1-128 chars from [a-zA-Z0-9_-]",
}


class TrackRequest(BaseModel):
    key: str
    repo_id: str = ""
    pr_title: str = ""
    pr_number: int = 0
    agents_used: list[str] = []
    findings_count: int = 0
    findings_by_severity: dict = {}
    duration_ms: int = 0


def _next_month_first(now: datetime) -> datetime:
    """Return midnight on the first day of the next month."""
    if now.month == 12:
        return datetime(now.year + 1, 1, 1)
    return datetime(now.year, now.month + 1, 1)


@router.post("/license/validate")
async def validate_license(body: ValidateRequest) -> JSONResponse:
    with get_db() as conn:
        lic = get_license_by_key(conn, body.key)

        if not lic or not lic.is_active:
            return JSONResponse(
                {"valid": False, "message": "Invalid license key"},
                status_code=401,
            )

        # Reset monthly counter if period_reset_at is in the past
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if lic.period_reset_at:
            reset_time = datetime.fromisoformat(lic.period_reset_at)
            if now >= reset_time:
                reset_monthly_counter(conn, lic.id)
                lic.reviews_used_this_month = 0
                # Set next reset to first day of next month
                next_reset = _next_month_first(now)
                conn.execute(
                    "UPDATE license_keys SET period_reset_at = ? WHERE id = ?",
                    (next_reset.isoformat(), lic.id),
                )

        # Check limit
        if lic.reviews_limit is not None and lic.reviews_used_this_month >= lic.reviews_limit:
            return JSONResponse({
                "valid": False,
                "message": "Review limit reached. Upgrade at https://revue.sh/upgrade",
            })

        # Calculate reviews_left
        reviews_left = None
        if lic.reviews_limit is not None:
            reviews_left = max(0, lic.reviews_limit - lic.reviews_used_this_month)

        agents = AGENTS_BY_TIER.get(lic.tier, AGENTS_BY_TIER["free"])

        return JSONResponse({
            "valid": True,
            "tier": lic.tier,
            "agents_allowed": agents,
            "reviews_left": reviews_left,
            "expires_at": "",
        })


def _rate_limited_response(error: ActivationRateLimitError) -> JSONResponse:
    """Build the 429 envelope with a Retry-After header (REVUE-325 AC1)."""
    response = JSONResponse(
        {"error": "rate_limited", "message": error.reason},
        status_code=429,
    )
    if error.retry_after_seconds is not None:
        response.headers["Retry-After"] = str(error.retry_after_seconds)
    return response


@router.post("/v2/licence/activate")
async def activate_licence(request: Request) -> JSONResponse:
    """REVUE-277 AC1+AC4: exchange a licence key for a signed RS256 JWT.

    REVUE-325: Rate-limited to prevent automated abuse:
    - Per-IP limit: 5 requests / 10 minutes
    - Per-key limit: 10 successful activations / 24 hours

    Success → 200 with ``{jwt, tier}``. The CLI verifies the JWT against
    the embedded public key, writes it to ``~/.config/revue/licence.jwt``
    with mode 0600, and uses it for the offline hot-path verification.

    Failure envelope is always ``{error, message}`` with a documented
    ``error`` code so the CLI can produce actionable messages without
    string-matching the human-readable text.
    """
    # AC5: validate required headers BEFORE parsing the body, so a malformed
    # Content-Type is rejected with 400 (FastAPI's automatic body binding would
    # otherwise mask it as a 422). The body is parsed manually below; field-level
    # validation still yields 422 to preserve the REVUE-277 activation contract.
    try:
        validate_activation_headers(
            user_agent=request.headers.get("user-agent"),
            content_type=request.headers.get("content-type"),
        )
    except ValueError as e:
        return JSONResponse(
            {"error": "invalid_request", "message": str(e)},
            status_code=400,
        )

    try:
        raw_body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "invalid_request", "message": "Request body must be valid JSON."},
            status_code=400,
        )
    try:
        body = ActivateRequest.model_validate(raw_body)
    except ValidationError:
        return JSONResponse(
            {"error": "invalid_request", "message": "Request body failed validation."},
            status_code=422,
        )

    # ``fullmatch`` (not ``match``) — default-flag ``$`` matches at
    # "end-of-string OR just before a trailing newline", which would let
    # ``"abc\n"`` through and sign the newline into the JWT claim.
    if not _FINGERPRINT_PATTERN.fullmatch(body.machine_fingerprint):
        return JSONResponse(_INVALID_FINGERPRINT_BODY, status_code=422)

    # Trusted client IP. On Fly.io the edge sets ``Fly-Client-IP`` server-side
    # and a client cannot forge it. ``X-Forwarded-For``'s leftmost entry is
    # client-supplied and MUST NOT be trusted for a security decision — using it
    # would let an attacker spoof a fresh IP per request and defeat AC1 entirely.
    # ``.strip()`` so a present-but-blank header is treated as absent rather than
    # silently falling through to the (shared) proxy peer address.
    client_ip = (request.headers.get("fly-client-ip") or "").strip() or (
        request.client.host if request.client else None
    )
    if not client_ip:
        # Fail closed rather than bucket every caller under one sentinel IP.
        return JSONResponse(
            {
                "error": "invalid_request",
                "message": "Could not determine client address.",
            },
            status_code=400,
        )

    with get_db() as conn:
        # No explicit locking is needed for the check-then-write critical section:
        # the web tier runs a SINGLE uvicorn worker (see Dockerfile) and this
        # ``async`` handler performs only blocking sqlite3 calls between here and
        # the response — there is no ``await`` inside the ``with get_db()`` block,
        # so the event loop cannot interleave another request's count+insert. The
        # section is therefore atomic on the event loop. If the web tier is ever
        # scaled to multiple workers/machines, move this rate-limit state to
        # Postgres (AC6's original intent) or add BEGIN IMMEDIATE + busy_timeout.

        # AC1: per-IP limit runs BEFORE the key lookup so brute-forcing keys
        # (each probe hitting a non-existent key) is throttled too.
        try:
            check_ip_rate_limit(conn, client_ip)
        except ActivationRateLimitError as e:
            log_activation_attempt(
                conn, None, body.key, client_ip, body.machine_fingerprint,
                is_successful=False, blocked=True,
            )
            return _rate_limited_response(e)

        lic = get_license_by_key(conn, body.key)

        if lic is None:
            # AC3: log invalid-key attempts (NULL key id) so probes are visible.
            log_activation_attempt(
                conn, None, body.key, client_ip, body.machine_fingerprint,
                is_successful=False,
            )
            return JSONResponse(
                {
                    "error": "invalid_key",
                    "message": "Licence key not recognised. "
                    "Double-check the key from your account at https://revue.sh/account.",
                },
                status_code=404,
            )

        # AC2: per-key limit, regardless of source IP.
        try:
            check_key_rate_limit(conn, lic.id)
        except ActivationRateLimitError as e:
            # AC4: a key crossing its limit is the flood signal.
            emit_flood_event(conn, lic.id, body.key)
            log_activation_attempt(
                conn, lic.id, body.key, client_ip, body.machine_fingerprint,
                is_successful=False, blocked=True,
            )
            return _rate_limited_response(e)

        if not lic.is_active:
            log_activation_attempt(
                conn, lic.id, body.key, client_ip, body.machine_fingerprint,
                is_successful=False,
            )
            return JSONResponse(
                {
                    "error": "inactive_licence",
                    "message": "This licence is no longer active. "
                    "Contact support@revue.sh to reactivate.",
                },
                status_code=403,
            )

        try:
            token = sign_licence_jwt(
                workspace_id=lic.workspace_id,
                tier=lic.tier,
                machine_fingerprint=body.machine_fingerprint,
            )
        except JWTSigningKeyMissing as exc:
            # Server-side misconfiguration (500) is not the caller's fault — mark
            # the attempt ``blocked`` so it is recorded for incident response but
            # excluded from the per-IP count, so a user retrying after the outage
            # does not lock themselves out.
            log_activation_attempt(
                conn, lic.id, body.key, client_ip, body.machine_fingerprint,
                is_successful=False, blocked=True,
            )
            return JSONResponse(
                {
                    "error": "server_misconfigured",
                    "message": str(exc),
                },
                status_code=500,
            )

        # AC3: record the successful activation (also drives the per-key counter).
        log_activation_attempt(
            conn, lic.id, body.key, client_ip, body.machine_fingerprint,
            is_successful=True,
        )
        return JSONResponse({"jwt": token, "tier": lic.tier})


@router.post("/usage/track")
async def track_usage(body: TrackRequest) -> Response:
    with get_db() as conn:
        lic = get_license_by_key(conn, body.key)
        if not lic:
            return JSONResponse({"error": "Invalid key"}, status_code=404)

        increment_usage(conn, lic.id)
        create_review_run(
            conn,
            license_key_id=lic.id,
            repo_id=body.repo_id or None,
            pr_title=body.pr_title or None,
            pr_number=body.pr_number or None,
            agents_used=body.agents_used,
            findings_count=body.findings_count,
            findings_by_severity=body.findings_by_severity or None,
            duration_ms=body.duration_ms,
        )

    return Response(status_code=204)


@router.get("/runs")
async def list_runs(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    repo_id: str = "",
    status: str = "",
) -> JSONResponse:
    """GET /api/runs — paginated run history for the authenticated user.

    Internal telemetry endpoint. Requires session auth (cookie).
    Returns JSON suitable for dashboards and metrics scripts.
    """
    session = get_session(request)
    if not session:
        return JSONResponse({"error": "Unauthorised"}, status_code=401)

    user_id = session["user_id"]
    with get_db() as conn:
        runs, total = get_all_runs_for_user(
            conn,
            user_id=user_id,
            limit=min(limit, 200),  # cap at 200 per page
            offset=offset,
            repo_id=repo_id or None,
            status=status or None,
        )

    return JSONResponse({
        "total": total,
        "limit": limit,
        "offset": offset,
        "runs": [  # type: ignore[misc]
            {
                "id": r.id,
                "repo_id": r.repo_id,
                "pr_title": r.pr_title,
                "pr_number": r.pr_number,
                "ci_run_id": r.ci_run_id,
                "agents_used": r.agents_used,
                "findings_count": r.findings_count,
                "findings_by_severity": r.findings_by_severity,
                "duration_ms": r.duration_ms,
                "status": r.status,
                "created_at": r.created_at,
            }
            for r in runs
        ],
    })


@router.post("/v2/licence/validate")
async def validate_licence(body: LicenceValidateRequest) -> JSONResponse:
    """REVUE-278 AC1–AC5: validate a JWT and return tier + cache refresh window.

    The skill POSTs the JWT from ~/.config/revue/licence.jwt. The server:
    1. Verifies the JWT signature
    2. Checks the workspace's licence is still ``is_active`` in the DB
    3. Extracts tier and workspace_id claims
    4. Returns {valid, tier, reviews_remaining, refresh_after_ts, refreshed_jwt?}

    If verification fails (expired, tampered, missing claims) OR the licence
    has been revoked since issuance, returns {valid: false}. All tiers get the
    same 24h cache window (no tier-graded grace to prevent tier-bypass attacks
    per AC5).

    The ``refresh_after_ts`` is issued by the server (issuance_ts + 86400),
    so the skill can trust it as the canonical cache horizon even if the
    client clock is skewed (per decision #4 in PM-plan).

    Revocation note: the JWT carries a 365-day ``exp``; without the DB
    ``is_active`` check, the only way to invalidate a leaked or churned JWT
    would be full signing-key rotation (which kills every customer). Adding
    the lookup makes per-workspace revocation honour the 24h cache bound
    that PM-plan decision #4 already accepts.
    """
    try:
        claims = decode_licence_jwt(body.jwt)
    except pyjwt.PyJWTError:
        # Any JWT error (expired, invalid sig, missing claims, malformed)
        # returns valid: false. The skill will retry or block, depending on
        # whether it has a fresh cached result (AC3–AC4).
        return JSONResponse({
            "valid": False,
            "tier": None,
            "reviews_remaining": None,
            "paywall_state": None,
            "refresh_after_ts": None,
            "refreshed_jwt": None,
        })

    # Decode succeeded — extract the claims
    tier = claims.get("tier")
    workspace_id = claims.get("workspace_id")

    # Single context manager for revocation check + usage count: ensures both
    # the licence validity and the paywall counter are consistent under the
    # same connection/SQLite snapshot. Without this, a revocation between the
    # two checks would create a TOCTOU gap.
    reviews_remaining = None
    paywall_state = None
    if isinstance(workspace_id, int):
        with get_db() as conn:
            # Revocation gate. Done after JWT decode (cheap, no DB hit) so
            # signature / expiry failures still short-circuit before the lookup.
            if get_active_license_for_workspace(conn, workspace_id) is None:
                return JSONResponse({
                    "valid": False,
                    "tier": None,
                    "reviews_remaining": None,
                    "paywall_state": None,
                    "refresh_after_ts": None,
                    "refreshed_jwt": None,
                })

            # Calculate reviews_remaining for free tier by counting events this
            # month (under the same connection so licence + counter are
            # consistent). Paid tiers have no cap (reviews_remaining = None).
            # Paywall state is "exhausted" only when tier == "free" AND
            # reviews_remaining <= 0.
            if tier == "free":
                count = count_usage_events_since_month_start(conn, workspace_id)
                # REVIEWS_LIMIT_BY_TIER["free"] is typed int | None (paid tiers
                # have None for "no cap"). Free is guaranteed to be an int, but
                # narrow defensively so mypy + future tier renames stay safe
                # (REVUE-279 code-review fix: replaced hardcoded 25).
                free_cap = REVIEWS_LIMIT_BY_TIER["free"] or 25
                reviews_remaining = max(0, free_cap - count)
                if reviews_remaining <= 0:
                    paywall_state = "exhausted"

    # AC1 / decision #4: refresh_after_ts is server-issued and canonical.
    # Compute from server clock — do NOT derive from the JWT's
    # ``issuance_ts`` claim, since that is client-presented (signed, but a
    # leaked signing key or a future bug could mint a far-future
    # issuance_ts and effectively disable re-validation forever).
    refresh_after_ts = int(datetime.now(timezone.utc).timestamp()) + 86400

    return JSONResponse({
        "valid": True,
        "tier": tier,
        "reviews_remaining": reviews_remaining,
        "paywall_state": paywall_state,
        "refresh_after_ts": refresh_after_ts,
        "refreshed_jwt": None,  # Rotation policy deferred; always None for now
    })


@router.post("/v2/usage/emit")
async def emit_usage(body: UsageEmitRequest) -> Response:
    """REVUE-278 AC6: record one per-invocation usage event.

    Telemetry endpoint used by ``/revue-local`` to report the number of
    reviews run and findings returned in a single invocation.

    Authentication: the client supplies the licence JWT (same one issued by
    ``/activate``). Workspace_id is derived from the verified claims so an
    unauthenticated caller cannot poison another tenant's counters or
    inflate billing. The endpoint:

    1. Verifies the JWT signature and required claims
    2. Rejects negative counter values (defence against billing fraud)
    3. Persists one UsageEvent row tied to the verified workspace_id
    4. Returns 200 (success is implicit; no response body)
    """
    try:
        claims = decode_licence_jwt(body.jwt)
    except pyjwt.PyJWTError as exc:
        return JSONResponse(
            {
                "error": "invalid_jwt",
                "message": f"licence JWT failed verification: {exc}",
            },
            status_code=401,
        )

    workspace_id = claims.get("workspace_id")
    if not isinstance(workspace_id, int):
        return JSONResponse(
            {
                "error": "invalid_jwt",
                "message": "licence JWT is missing a valid workspace_id claim",
            },
            status_code=401,
        )

    # Revocation gate. Symmetric with /v2/licence/validate — a revoked
    # workspace must not be able to keep emitting telemetry until JWT exp.
    with get_db() as conn:
        if get_active_license_for_workspace(conn, workspace_id) is None:
            return JSONResponse(
                {
                    "error": "licence_revoked",
                    "message": "workspace has no active licence",
                },
                status_code=401,
            )

    if body.reviews_run < 0 or body.findings_count < 0:
        return JSONResponse(
            {
                "error": "invalid_counters",
                "message": "reviews_run and findings_count must be non-negative",
            },
            status_code=422,
        )

    with get_db() as conn:
        record_usage_event(
            conn,
            workspace_id=workspace_id,
            reviews_run=body.reviews_run,
            findings_count=body.findings_count,
            emitted_at=body.ts,
        )
    return Response(status_code=200)


@router.get("/analytics")
async def analytics_data(
    request: Request,
    days: int = 30,
) -> JSONResponse:
    """GET /api/analytics — aggregate finding trends for the authenticated user.

    Query params:
        days: lookback window in days (7–365, default 30)
    """
    session = get_session(request)
    if not session:
        return JSONResponse({"error": "Unauthorised"}, status_code=401)

    days = max(7, min(days, 365))
    user_id = session["user_id"]
    with get_db() as conn:
        data = get_analytics(conn, user_id, days=days)
    return JSONResponse(data)
