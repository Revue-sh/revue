"""Legal pages — Terms of Service and Privacy Policy.

Renders Markdown legal content as HTML pages.
Mirrors docs_routes.py pattern for consistency.
"""
from __future__ import annotations

from pathlib import Path

import markdown
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from config import templates

router = APIRouter()

LEGAL_DIR = Path(__file__).parent.parent / "legal_content"


def _render(slug: str) -> str | None:
    """Return rendered HTML for a legal slug, or None if not found."""
    path = LEGAL_DIR / f"{slug}.md"
    if not path.exists():
        return None
    md = markdown.Markdown(
        extensions=["fenced_code", "tables", "toc", "attr_list"],
        extension_configs={"toc": {"title": ""}},
    )
    return md.convert(path.read_text(encoding="utf-8"))


@router.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request) -> HTMLResponse:
    html_content = _render("terms")
    if html_content is None:
        return HTMLResponse("<h1>Terms of Service not found</h1>", status_code=404)

    return templates.TemplateResponse(request, "legal.html", {
        "title": "Terms of Service",
        "content": html_content,
    })


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request) -> HTMLResponse:
    html_content = _render("privacy")
    if html_content is None:
        return HTMLResponse("<h1>Privacy Policy not found</h1>", status_code=404)

    return templates.TemplateResponse(request, "legal.html", {
        "title": "Privacy Policy",
        "content": html_content,
    })
