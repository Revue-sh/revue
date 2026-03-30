"""Documentation site routes — serves Markdown docs as HTML pages."""
from __future__ import annotations

import os
from pathlib import Path

import markdown
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from config import templates

router = APIRouter()

DOCS_DIR = Path(__file__).parent.parent / "docs_content"

# Ordered nav structure
NAV = [
    {
        "section": "Getting Started",
        "pages": [
            {"slug": "quickstart-github", "title": "GitHub Actions"},
            {"slug": "quickstart-gitlab", "title": "GitLab CI"},
            {"slug": "quickstart-bitbucket", "title": "Bitbucket Pipelines"},
        ],
    },
    {
        "section": "Reference",
        "pages": [
            {"slug": "revue-yml-reference", "title": ".revue.yml Reference"},
            {"slug": "agents", "title": "Agent Catalogue"},
        ],
    },
    {
        "section": "Help",
        "pages": [
            {"slug": "faq", "title": "FAQ"},
        ],
    },
]

# Flat slug → title map for breadcrumbs
_SLUG_TITLES: dict[str, str] = {
    page["slug"]: page["title"]
    for section in NAV
    for page in section["pages"]
}

_MD = markdown.Markdown(
    extensions=["fenced_code", "tables", "toc", "attr_list"],
    extension_configs={"toc": {"title": ""}},
)


def _render(slug: str) -> str | None:
    """Return rendered HTML for a doc slug, or None if not found."""
    path = DOCS_DIR / f"{slug}.md"
    if not path.exists():
        return None
    _MD.reset()
    return _MD.convert(path.read_text(encoding="utf-8"))


@router.get("/docs", response_class=HTMLResponse)
async def docs_index() -> RedirectResponse:
    return RedirectResponse("/docs/quickstart-github", status_code=302)


@router.get("/docs/{slug}", response_class=HTMLResponse)
async def docs_page(request: Request, slug: str) -> HTMLResponse:
    if slug not in _SLUG_TITLES:
        return HTMLResponse("<h1>Page not found</h1>", status_code=404)

    html_content = _render(slug)
    if html_content is None:
        return HTMLResponse("<h1>Content not found</h1>", status_code=404)

    return templates.TemplateResponse(request, "docs.html", {
        "nav": NAV,
        "current_slug": slug,
        "title": _SLUG_TITLES[slug],
        "content": html_content,
    })
