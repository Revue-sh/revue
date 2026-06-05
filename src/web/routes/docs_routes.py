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
#
# REVUE-407: the three per-platform quickstart slugs were consolidated into a
# single authoritative /docs/ci-setup page (rendered from a dedicated template,
# not Markdown — it needs the tabbed copy-button UI). The "Getting Started"
# section now points at that one page; the legacy slugs redirect to it.
NAV = [
    {
        "section": "Getting Started",
        "pages": [
            {"slug": "ci-setup", "title": "CI Setup"},
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

# Legacy per-platform quickstart slugs, retired in favour of /docs/ci-setup
# (REVUE-407 AC5 — single source of truth). Kept only to 301-redirect old links.
LEGACY_QUICKSTART_SLUGS = (
    "quickstart-github",
    "quickstart-gitlab",
    "quickstart-bitbucket",
)

# ---------------------------------------------------------------------------
# Canonical CI-setup YAML snippets (REVUE-407 — the single source of truth).
#
# The CI review pipeline runs the `revue-ci review` console script from the
# `revue-ci` package (revue_ci/cli.py). The `revue` package ships the CLI-mode
# `revue activate` command; CI mode is a separate binary — two modes, two
# commands. Comment posting is enabled automatically by `--platform` + `--pr-id`
# (there is no --post-comments flag). All three use the unified provider-key
# variable AI_API_KEY (AC6) plus AI_PROVIDER/AI_MODEL for provider selection.
#
# Kept as Python constants (not in the Jinja template) because the GitHub
# Actions ``${{ ... }}`` syntax collides with Jinja's own delimiters — passing
# the raw string sidesteps escaping.
# ---------------------------------------------------------------------------

_BITBUCKET_CI_YAML = """image: python:3.12-slim

pipelines:
  pull-requests:
    "**":
      - step:
          name: "Revue AI Code Review"
          script:
            - pip install revue-ci --quiet
            - |
              AUTH=$(echo -n "${BITBUCKET_USERNAME}:${BITBUCKET_API_TOKEN}" | base64)
              curl -s \\
                -H "Authorization: Basic ${AUTH}" \\
                -H "Accept: text/plain" \\
                "https://api.bitbucket.org/2.0/repositories/${BITBUCKET_WORKSPACE}/${BITBUCKET_REPO_SLUG}/pullrequests/${BITBUCKET_PR_ID}/diff" \\
                -o /tmp/revue_pr.diff
            - |
              revue-ci review \\
                --diff /tmp/revue_pr.diff \\
                --platform bitbucket \\
                --pr-id "${BITBUCKET_PR_ID}" \\
                --workspace "${BITBUCKET_WORKSPACE}" \\
                --repo-slug "${BITBUCKET_REPO_SLUG}" \\
                --bb-username "${BITBUCKET_USERNAME}" \\
                --bb-token "${BITBUCKET_API_TOKEN}" \\
                --provider "${AI_PROVIDER:-anthropic}" \\
                --model "${AI_MODEL:-claude-sonnet-4-5}" \\
                --config .revue.yml
"""

_GITHUB_CI_YAML = """name: Revue AI Code Review

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write

jobs:
  revue:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install revue-ci
      - name: Run Revue review
        env:
          REVUE_LICENSE_KEY: ${{ secrets.REVUE_LICENSE_KEY }}
          AI_API_KEY: ${{ secrets.AI_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPOSITORY: ${{ github.repository }}
        run: |
          git diff origin/${{ github.base_ref }}...HEAD > /tmp/pr.diff
          revue-ci review \\
            --diff /tmp/pr.diff \\
            --platform github \\
            --pr-id "${{ github.event.pull_request.number }}" \\
            --provider "${{ vars.AI_PROVIDER || 'anthropic' }}" \\
            --model "${{ vars.AI_MODEL || 'claude-sonnet-4-5' }}" \\
            --config .revue.yml
"""

_GITLAB_CI_YAML = """stages:
  - review

revue-ai-review:
  stage: review
  image: python:3.12-slim
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  before_script:
    - pip install revue-ci
  script:
    - git fetch origin $CI_MERGE_REQUEST_TARGET_BRANCH_NAME
    - git diff origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME...HEAD > /tmp/mr.diff
    - >
      revue-ci review
      --diff /tmp/mr.diff
      --platform gitlab
      --pr-id "$CI_MERGE_REQUEST_IID"
      --provider "${AI_PROVIDER:-openai}"
      --model "${AI_MODEL:-gpt-4o-mini}"
      --config .revue.yml
  variables:
    REVUE_LICENSE_KEY: $REVUE_LICENSE_KEY
    AI_API_KEY: $AI_API_KEY
    GITLAB_TOKEN: $GITLAB_TOKEN
    CI_PROJECT_PATH: $CI_PROJECT_PATH
"""

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
    return RedirectResponse("/docs/ci-setup", status_code=302)


# NOTE: specific /docs/ci-setup and the legacy redirects MUST be declared BEFORE
# the /docs/{slug} catch-all below, or FastAPI would route them into the generic
# Markdown handler (which has no ci-setup.md and would 404).
@router.get("/docs/ci-setup", response_class=HTMLResponse, name="ci_setup")
async def ci_setup(request: Request) -> HTMLResponse:
    """The single authoritative CI-setup page (REVUE-407).

    Public (no auth). Consolidates Bitbucket Pipelines, GitHub Actions and
    GitLab CI setup into one tabbed page with copy-paste YAML snippets. Named
    ``ci_setup`` so other surfaces can ``url_for("ci_setup")`` (REVUE-361).
    """
    return templates.TemplateResponse(request, "ci_setup.html", {
        "nav": NAV,
        "current_slug": "ci-setup",
        "title": "CI Setup",
        "BITBUCKET_YAML": _BITBUCKET_CI_YAML,
        "GITHUB_YAML": _GITHUB_CI_YAML,
        "GITLAB_YAML": _GITLAB_CI_YAML,
    })


@router.get("/docs/{slug}", response_class=HTMLResponse)
async def docs_page(request: Request, slug: str) -> HTMLResponse:
    # Legacy quickstart slugs are retired — permanently redirect to the
    # consolidated page so old bookmarks and inbound links still land correctly.
    if slug in LEGACY_QUICKSTART_SLUGS:
        return RedirectResponse("/docs/ci-setup", status_code=301)

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
