"""Authentication routes: signup, login, logout."""
from __future__ import annotations

import re

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from auth import (
    hash_password,
    verify_password,
    create_session,
    clear_session,
    get_session,
)
from database import get_db
from license import generate_license_key
from models import create_user, get_user_by_email, create_workspace, create_license_key
from config import templates

router = APIRouter()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request) -> HTMLResponse:
    session = get_session(request)
    if session:
        return RedirectResponse("/dashboard", status_code=303)
    ref = request.query_params.get("ref", "")
    return templates.TemplateResponse(request, "signup.html", {"error": None, "ref": ref})


@router.post("/signup", response_class=HTMLResponse)
async def signup_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    ref: str = Form(""),
) -> HTMLResponse:
    error = None
    if not EMAIL_RE.match(email):
        error = "Please enter a valid email address."
    elif len(password) < 8:
        error = "Password must be at least 8 characters."

    if error:
        return templates.TemplateResponse(request, "signup.html", {"error": error, "ref": ref})

    referral_source = ref.strip() or None

    with get_db() as conn:
        existing = get_user_by_email(conn, email)
        if existing:
            return templates.TemplateResponse(
                request, "signup.html", {"error": "An account with this email already exists.", "ref": ref}
            )

        pw_hash = hash_password(password)
        user_id = create_user(conn, email, pw_hash, referral_source=referral_source)
        workspace_id = create_workspace(conn, user_id, f"{email}'s workspace")
        key = generate_license_key()
        create_license_key(conn, workspace_id, key)

    response = RedirectResponse("/onboarding", status_code=303)
    create_session(response, user_id, email, "free")
    return response


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    session = get_session(request)
    if session:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
) -> HTMLResponse:
    with get_db() as conn:
        user = get_user_by_email(conn, email)
        if not user or not verify_password(password, user.password_hash):
            return templates.TemplateResponse(
                request, "login.html", {"error": "Invalid email or password."}
            )

    response = RedirectResponse("/dashboard", status_code=303)
    create_session(response, user.id, user.email, user.tier)
    return response


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    response = RedirectResponse("/", status_code=303)
    clear_session(response)
    return response
