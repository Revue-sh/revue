"""CSRF double-submit-cookie protection (REVUE-418).

Mechanism
---------
Double-submit cookie. On any GET that lacks the CSRF cookie, the middleware
mints a token and sets it as a cookie (``revue_csrf``). Every protected,
state-changing form POST must echo that same token in a hidden form field
(``csrf_token``). The middleware accepts the request only when the cookie
value and the form value match (constant-time compare) AND both are validly
signed.

The token is signed with ``itsdangerous`` using the app ``SECRET_KEY`` so a
cross-site attacker — who can read neither the httponly-irrelevant cookie nor
the secret — cannot forge a value that passes both the equality check and the
signature check. The cookie itself is intentionally NOT httponly: the form
must be able to carry the same value, and for a double-submit cookie the
defence comes from same-origin read access, not from hiding the value.

Implementation note — pure ASGI
-------------------------------
The enforcement runs as a **pure-ASGI** middleware (``CSRFMiddleware``), NOT a
Starlette ``BaseHTTPMiddleware``. Reading the form to extract the token in a
BaseHTTPMiddleware consumes the request body stream, so the downstream handler's
own ``await request.form()`` / ``Form(...)`` parameters see an empty body
(observed as HTTP 422 on signup/login). The ASGI middleware buffers the body
once and replays a FRESH copy to both the token-parsing ``Request`` and the
downstream app, so handlers receive the body intact.

Scope
-----
Token mint / verify helpers, the shared constants, and the ASGI enforcement
mechanism live here. The protect/exempt PATH POLICY (which paths are exempt) is
injected by ``main.py`` via the ``is_exempt`` callable, keeping this module free
of route knowledge.
"""
from __future__ import annotations

import secrets
from typing import Awaitable, Callable

from itsdangerous import BadSignature, URLSafeSerializer
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# The shared SECRET_KEY already used for session signing. Importing from auth
# keeps a single signing-secret source of truth for the web app — and the shared
# cookie-hardening helpers, so the CSRF and session cookies harden in lockstep.
from auth import (
    SECRET_KEY,
    SESSION_MAX_AGE,
    cookie_secure,
    host_prefixed,
    make_serializer_cache,
)

# Base (insecure / dev) name of the cookie that carries the CSRF token to the
# browser. NOT httponly — the page's form must echo the same value back
# (double-submit). Distinct from the session cookie. Over HTTPS this is upgraded
# to ``__Host-revue_csrf`` (see ``csrf_cookie_name``).
CSRF_COOKIE_BASE = "revue_csrf"


def csrf_cookie_name() -> str:
    """Resolve the CSRF cookie name for the current mode (``__Host-`` over HTTPS).

    Read PER CALL so the set and read sides always agree within a process run.
    There is intentionally NO module-level ``CSRF_COOKIE`` constant: a constant
    that always resolved to the insecure base name was a footgun — any caller
    reading/writing via it would silently use the wrong cookie under
    ``COOKIE_SECURE``. Use this resolver everywhere instead.
    """
    return host_prefixed(CSRF_COOKIE_BASE)

# Hidden form field every protected HTML form must include.
CSRF_FORM_FIELD = "csrf_token"

# Namespacing salt so a CSRF token can never be confused with any other
# itsdangerous-signed value minted from the same SECRET_KEY (e.g. the session).
_SALT = "revue-csrf-token"

# 32 bytes of urlsafe randomness → ~43 chars before signing. Ample entropy.
_RANDOM_BYTES = 32

# Defense-in-depth body cap. ``_buffer_body`` reads the unsafe-method body into
# memory before the token check; without a bound a hostile client could stream
# an unbounded body and exhaust memory ahead of any validation. Every protected
# route is a tiny login/signup/billing form, so 1 MiB cannot break a legitimate
# request (including multipart). Over-cap requests are rejected with 413 before
# any parsing. This guards ONLY unsafe, must-check requests (the sole caller).
MAX_BODY_BYTES = 1024 * 1024

# Sentinel returned by ``_buffer_body`` when the accumulated body exceeds
# ``MAX_BODY_BYTES``. Distinct from any real (possibly empty) body so the caller
# can branch to a 413 without ambiguity.
_BODY_TOO_LARGE = object()

# Distinct from the session serializer: UNTIMED + SALTED (the salt namespaces a
# CSRF token so it can never be confused with a session token minted from the
# same SECRET_KEY). Only the lazy build-once/reset PATTERN is shared, via
# ``make_serializer_cache`` imported from auth.
_get_serializer, reset_serializer = make_serializer_cache(
    lambda: URLSafeSerializer(SECRET_KEY, salt=_SALT)
)


def issue_token() -> str:
    """Mint a fresh, signed CSRF token.

    Each call embeds new randomness, so two tokens never collide. The value is
    signed with the app secret; ``tokens_match`` rejects any value that does
    not carry a valid signature.
    """
    raw = secrets.token_urlsafe(_RANDOM_BYTES)
    return _get_serializer().dumps(raw)


def _is_validly_signed(token: str) -> bool:
    if not token:
        return False
    try:
        _get_serializer().loads(token)
        return True
    except BadSignature:
        return False


def tokens_match(cookie_token: str, form_token: str) -> bool:
    """Return True iff the double-submit check passes.

    Both the cookie value and the submitted form value must be present, carry a
    valid signature, and be equal under a constant-time comparison. An empty or
    tampered value on either side fails closed.
    """
    if not cookie_token or not form_token:
        return False
    # Constant-time equality first (the double-submit core), then verify the
    # value is a genuinely signed token (not attacker-chosen plaintext that
    # merely matches a value the attacker also planted).
    if not secrets.compare_digest(cookie_token, form_token):
        return False
    return _is_validly_signed(cookie_token)


# ---------------------------------------------------------------------------
# Pure-ASGI enforcement middleware
# ---------------------------------------------------------------------------

# Unsafe (state-changing) methods that require a CSRF token on cookie-session
# form requests. GET/HEAD/OPTIONS/TRACE are safe and never checked.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# The "CORS-simple" request content types — the ONLY ones a cross-site HTML
# form / ``fetch`` can emit WITHOUT triggering a CORS preflight the attacker
# cannot satisfy. An EMPTY/absent content-type is equally forgeable. CSRF is
# enforced on these (plus empty). Anything else (e.g. ``application/json``)
# forces a preflight, so it is not a cross-site-forgeable vector and is skipped
# — this also keeps non-form JSON POSTs to unmatched paths returning their
# natural 404/422 instead of a CSRF 403.
_SIMPLE_CONTENT_TYPES = (
    "application/x-www-form-urlencoded",
    "multipart/form-data",
    "text/plain",
)

# The subset that actually carries form fields we can parse a token out of.
_PARSEABLE_FORM_TYPES = (
    "application/x-www-form-urlencoded",
    "multipart/form-data",
)


def _build_set_cookie_headers(token: str) -> list[tuple[bytes, bytes]]:
    """Produce the raw Set-Cookie header(s) for a freshly minted CSRF cookie,
    reusing Starlette's cookie formatting rather than hand-building the string.

    NOT httponly: the page's form must echo the same value back (double-submit).
    ``samesite=lax`` mirrors the session cookie. Over HTTPS the name becomes
    ``__Host-revue_csrf`` with ``Secure`` + ``Path=/`` and no ``Domain`` (the
    browser requirement for the ``__Host-`` prefix), in lockstep with the
    session cookie.
    """
    tmp = Response()
    tmp.set_cookie(
        csrf_cookie_name(),
        token,
        max_age=SESSION_MAX_AGE,
        httponly=False,
        samesite="lax",
        secure=cookie_secure(),
        path="/",
    )
    return [(k, v) for k, v in tmp.raw_headers if k == b"set-cookie"]


def _replay_receive(body: bytes) -> Callable[[], Awaitable[dict]]:
    """Return a fresh ASGI ``receive`` callable that yields the buffered body
    exactly once. A new callable must be built per consumer (token parser and
    downstream app) — sharing one would let the first consumer drain it, leaving
    the second with an empty body."""
    async def receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


class CSRFMiddleware:
    """Double-submit-cookie CSRF enforcement as pure ASGI.

    Responsibilities:
      * Expose ``request.state.csrf_token`` (read-or-generate) for templates.
      * Set the CSRF cookie on responses when it was absent (so unauthenticated
        GET pages mint a usable token); never rotate an existing cookie.
      * On unsafe, non-exempt requests, validate the submitted form token against
        the cookie BEFORE the handler runs; reject with 403 on mismatch.
      * Buffer and replay the request body so handlers parse it intact.

    The exempt-path policy is injected via ``is_exempt`` so this class owns the
    mechanism, not the route map.
    """

    def __init__(self, app, *, is_exempt: Callable[[str], bool]):
        self.app = app
        self.is_exempt = is_exempt

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Trust the cookie ONLY if it carries a valid signature. A tampered or
        # stale value (e.g. after SECRET_KEY rotation) is treated as absent and
        # re-minted; otherwise that browser would 403 forever (permanent lockout)
        # because its cookie can never match a freshly-signed form token.
        cookie_token = self._read_cookie_token(scope)
        if cookie_token and _is_validly_signed(cookie_token):
            token, had_cookie = cookie_token, True
        else:
            token, had_cookie = issue_token(), False

        # Expose to templates via request.state (scope["state"]). Handlers render
        # ``{{ request.state.csrf_token }}`` into the hidden form field.
        scope.setdefault("state", {})
        scope["state"]["csrf_token"] = token

        # Set the cookie on the response only when it was ABSENT or INVALID
        # (read-or-generate); never overwrite an existing valid cookie, keeping it
        # stable across tabs. Computed once and used by BOTH the success path and
        # the 403 path, so a 403 heals the browser exactly when a normal response
        # would have minted a cookie.
        send_wrapped = send if had_cookie else self._wrap_send(send, token)

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        content_type = self._content_type(scope)
        must_check = (
            method in UNSAFE_METHODS
            and not self.is_exempt(path)
            # Only the CORS-simple (or empty) content types are cross-site
            # forgeable; a preflight-protected type (e.g. application/json) is
            # not a CSRF vector, so it is not checked. This keeps non-form JSON
            # POSTs returning their natural status (404/422) rather than 403.
            and self._is_simple_content_type(content_type)
        )

        downstream_receive = receive
        if must_check:
            # Buffer the body ONCE (looping on more_body for chunked uploads),
            # parse the token from a fresh replay, then validate. The buffer is
            # size-capped (defense-in-depth) — an over-cap body is rejected with
            # 413 before any parsing.
            body = await self._buffer_body(receive)
            if body is _BODY_TOO_LARGE:
                response = JSONResponse(
                    {"error": "Request body too large"},
                    status_code=413,
                )
                await response(scope, receive, send_wrapped)
                return
            form_token = await self._extract_form_token(scope, body)
            if not tokens_match(cookie_token, form_token):
                response = JSONResponse(
                    {"error": "CSRF token missing or invalid"},
                    status_code=403,
                )
                await response(scope, receive, send_wrapped)
                return
            # Hand the downstream app its OWN fresh replay of the buffered body.
            downstream_receive = _replay_receive(body)

        await self.app(scope, downstream_receive, send_wrapped)

    @staticmethod
    def _read_cookie_token(scope) -> str:
        # Build a lightweight Request to reuse Starlette's cookie parsing. Read
        # whichever name the current mode uses, so set/read never diverge.
        request = Request(scope)
        return request.cookies.get(csrf_cookie_name(), "")

    @staticmethod
    async def _buffer_body(receive):
        """Buffer the request body, capped at ``MAX_BODY_BYTES``.

        Returns the buffered ``bytes`` on success, or the ``_BODY_TOO_LARGE``
        sentinel if the accumulated size exceeds the cap. The cap is enforced on
        the RUNNING total (not a Content-Length precheck) because Content-Length
        is absent on chunked requests and is attacker-controlled — exactly the
        client this guard defends against. Once over-cap we stop reading and
        bail; we do not drain the rest of the stream.
        """
        chunks: list[bytes] = []
        total = 0
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                # e.g. http.disconnect — stop buffering.
                break
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > MAX_BODY_BYTES:
                return _BODY_TOO_LARGE
            chunks.append(chunk)
            more = message.get("more_body", False)
        return b"".join(chunks)

    @staticmethod
    def _content_type(scope) -> str:
        for name, value in scope.get("headers", []):
            if name == b"content-type":
                return value.decode("latin-1")
        return ""

    @staticmethod
    def _is_simple_content_type(content_type: str) -> bool:
        # Empty/absent content-type is cross-site forgeable → enforce.
        if not content_type:
            return True
        return content_type.startswith(_SIMPLE_CONTENT_TYPES)

    @staticmethod
    async def _extract_form_token(scope, body: bytes) -> str:
        content_type = CSRFMiddleware._content_type(scope)
        # Only parse a token out of the types that actually carry form fields.
        # A simple-but-non-form body (e.g. text/plain, or empty) carries no
        # parseable token → returns "" → fails the match → 403, which is the
        # correct fail-closed behaviour for a forgeable request without a token.
        if not content_type.startswith(_PARSEABLE_FORM_TYPES):
            return ""
        request = Request(scope, receive=_replay_receive(body))
        form: FormData = await request.form()
        return str(form.get(CSRF_FORM_FIELD, ""))

    def _wrap_send(self, send, token: str):
        cookie_headers = _build_set_cookie_headers(token)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                headers = list(message.get("headers", []))
                headers.extend(cookie_headers)
                message["headers"] = headers
            await send(message)

        return send_wrapper
