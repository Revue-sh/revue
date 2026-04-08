#!/usr/bin/env python3
"""
import_review.py — Import review comparison results into Postgres

Usage:
    python3 src/db/import_review.py docs/review-comparisons/REVUE-XX/ \\
        --model claude-sonnet-4-5 --provider anthropic

Imports:
- baseline.json + contextual.json → reviews + findings tables
- pr_description.txt → pr_descriptions + pr_description_sections
- Links via comparison_runs table

Story: REVUE-90
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from src.db.auto_scorer import score_findings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db_connection() -> psycopg2.extensions.connection:
    """Connect to Postgres using DATABASE_URL from environment."""
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL not set. Run: source ~/.zshenv"
        )
    return psycopg2.connect(database_url, cursor_factory=RealDictCursor)


def get_or_create_model(
    cursor: psycopg2.extensions.cursor,
    model_name: str,
    provider: str
) -> int:
    """Lookup or insert AI model, return model_id."""
    cursor.execute(
        "SELECT id FROM models WHERE name = %s AND provider = %s",
        (model_name, provider)
    )
    row = cursor.fetchone()
    if row:
        return row['id']
    
    # Insert new model
    cursor.execute(
        """
        INSERT INTO models (name, provider)
        VALUES (%s, %s)
        RETURNING id
        """,
        (model_name, provider)
    )
    return cursor.fetchone()['id']


def get_lookup_id(
    cursor: psycopg2.extensions.cursor,
    table: str,
    name: str
) -> int:
    """Generic lookup for reference tables (severity_levels, review_modes, etc.)."""
    cursor.execute(f"SELECT id FROM {table} WHERE name = %s", (name,))
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"Unknown {table} value: {name}")
    return row['id']


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def load_findings(json_path: Path) -> list[dict]:
    """
    Load findings from Revue JSON output.
    
    Handles two formats:
    1. List: [{"review": "..."}, ...]
    2. Dict: {"results": [{"review": "..."}]}
    
    Returns list of finding dicts with keys:
    - severity, issue, file_path, category, details, recommendation
    """
    if not json_path.exists():
        return []
    
    raw = json.loads(json_path.read_text())
    findings = []
    
    if isinstance(raw, list):
        for entry in raw:
            review_text = entry.get("review", "")
            findings.extend(_parse_findings_from_text(review_text))
    elif isinstance(raw, dict):
        for entry in raw.get("results", []):
            findings.extend(_parse_findings_from_text(entry.get("review", "")))
    
    return findings


def _parse_findings_from_text(text: str) -> list[dict]:
    """Extract findings list from JSON embedded in review text."""
    if not text:
        return []
    
    try:
        # Strip markdown code fences if present
        clean = text.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:])
        if clean.endswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[:-1])
        
        data = json.loads(clean.strip())
        
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if "review" in data and isinstance(data["review"], dict):
                data = data["review"]
            return data.get("findings", [])
    except Exception:
        pass
    
    return []


# ---------------------------------------------------------------------------
# PR / CI context detection
# ---------------------------------------------------------------------------

def _detect_pr_context() -> tuple[
    Optional[str], Optional[str], Optional[str], Optional[int]
]:
    """Detect platform name, repo_owner, repo_name, pr_number from CI env vars.

    Returns (None, None, None, None) when no PR context is available
    (local / dry-run mode).
    """
    # Bitbucket
    pr_id = os.environ.get("BITBUCKET_PR_ID")
    if pr_id:
        return (
            "bitbucket",
            os.environ.get("BITBUCKET_REPO_OWNER", ""),
            os.environ.get("BITBUCKET_REPO_SLUG", ""),
            int(pr_id),
        )

    # GitHub  (GITHUB_REPOSITORY is "owner/repo")
    pr_number = os.environ.get("GITHUB_PR_NUMBER")
    if pr_number:
        repo = os.environ.get("GITHUB_REPOSITORY", "/")
        owner, name = repo.split("/", 1)
        return ("github", owner, name, int(pr_number))

    # GitLab  (GITLAB_PROJECT_PATH is "group/project")
    mr_iid = os.environ.get("GITLAB_MR_IID")
    if mr_iid:
        project = os.environ.get("GITLAB_PROJECT_PATH", "/")
        owner, name = project.split("/", 1)
        return ("gitlab", owner, name, int(mr_iid))

    return (None, None, None, None)


def _get_commit_sha() -> str:
    """Get current commit SHA from GIT_COMMIT env or ``git rev-parse HEAD``."""
    sha = os.environ.get("GIT_COMMIT")
    if sha:
        return sha
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _post_findings_as_comments(
    findings: list[dict],
    review_id: int,
) -> None:
    """Post findings as inline PR comments and store in .revue/ TOML.

    Skips silently when no PR context is detected (local/dry-run mode).
    Failures are logged as warnings but never abort the import.

    Comment module imports are lazy so import_review.py can be loaded
    without ``src/`` on PYTHONPATH (e.g. in unit tests).
    """
    platform_name, repo_owner, repo_name, pr_number = _detect_pr_context()
    if not platform_name or not repo_owner or not repo_name or not pr_number:
        return

    # Lazy imports — comment module lives under src/revue/ and requires
    # PYTHONPATH=src which is only guaranteed in CI / run-comparison.sh.
    from revue.comments.file_store import CommentFileStore  # noqa: E402
    from revue.comments.fingerprint import fingerprint  # noqa: E402
    from revue.comments.models import (  # noqa: E402
        CommentState,
        Platform,
        PRComment as PRCommentModel,
    )
    from revue.comments.platform_adapter import get_platform_adapter  # noqa: E402

    platform = Platform(platform_name)
    adapter = get_platform_adapter(platform)
    store = CommentFileStore(os.getcwd())
    commit_sha = _get_commit_sha()

    posted = 0
    for finding in findings:
        file_path = finding.get("file_path") or finding.get("file") or ""
        line_number = finding.get("line_start") or 1
        severity = finding.get("severity", "info")
        issue = finding.get("issue") or finding.get("message") or finding.get("title") or ""
        details = finding.get("details") or ""
        recommendation = finding.get("recommendation") or ""

        body = (
            f"\U0001f50d **Revue** [{severity}] {issue}\n\n"
            f"{details}\n\n"
            f"\U0001f4a1 {recommendation}"
        )

        comment_id, thread_id = adapter.post_comment(
            repo_owner, repo_name, pr_number,
            file_path, line_number, body, commit_sha,
        )

        fp = fingerprint(file_path, line_number)  # diff not available here; falls back to line_number

        comment = PRCommentModel(
            id=None,
            platform=platform,
            platform_comment_id=comment_id,
            platform_thread_id=thread_id,
            pr_number=pr_number,
            repo_owner=repo_owner,
            repo_name=repo_name,
            file_path=file_path,
            line_number=line_number,
            comment_body=body,
            finding_id=review_id,
            state=CommentState.UNRESOLVED,
            created_at=None,
            updated_at=None,
            finding_fingerprint=fp,
        )
        store.create_comment(comment)
        posted += 1

    if posted:
        print(
            f"\U0001f4ac Posted {posted} inline comments to "
            f"{platform.value} PR #{pr_number}"
        )


# ---------------------------------------------------------------------------
# Import logic
# ---------------------------------------------------------------------------

def import_review(
    cursor: psycopg2.extensions.cursor,
    json_path: Path,
    ticket_id: str,
    branch: str,
    model_id: int,
    tier_id: int,
    mode_id: int
) -> tuple[Optional[int], list[tuple[int, dict]]]:
    """
    Import a single review (baseline or contextual) into database.

    Returns (review_id, scored_findings) where scored_findings is a list of
    (finding_id, finding_dict) tuples for auto-scoring. Empty list if already
    imported (idempotent).
    """
    findings = load_findings(json_path)
    
    # Check if review already exists (idempotency)
    cursor.execute(
        """
        SELECT id FROM reviews
        WHERE ticket_id = %s AND branch = %s AND mode_id = %s
        ORDER BY run_at DESC LIMIT 1
        """,
        (ticket_id, branch, mode_id)
    )
    existing = cursor.fetchone()
    if existing:
        print(f"⚠️  Review already imported: {ticket_id} ({json_path.name})")
        return existing['id'], []
    
    # Insert review
    cursor.execute(
        """
        INSERT INTO reviews (
            ticket_id, branch, model_id, tier_id, mode_id, total_findings
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (ticket_id, branch, model_id, tier_id, mode_id, len(findings))
    )
    review_id = cursor.fetchone()['id']
    
    # Insert findings and collect (finding_id, finding_dict) for auto-scoring
    scored_findings: list[tuple[int, dict]] = []
    for finding in findings:
        severity_name = finding.get("severity", "info").lower()
        severity_id = get_lookup_id(cursor, "severity_levels", severity_name)

        cursor.execute(
            """
            INSERT INTO findings (
                review_id, file_path, severity_id, category,
                issue, details, recommendation, code_snippet,
                line_start, line_end
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                review_id,
                finding.get("file_path") or finding.get("file") or "",
                severity_id,
                finding.get("category"),
                finding.get("issue") or finding.get("message") or finding.get("title") or "",
                finding.get("details"),
                finding.get("recommendation"),
                finding.get("code_snippet"),
                finding.get("line_start"),
                finding.get("line_end")
            )
        )
        finding_id = cursor.fetchone()['id']
        scored_findings.append((finding_id, finding))

    print(f"✅ Imported {len(findings)} findings from {json_path.name}")

    # Post findings as inline PR comments (CI only; non-fatal)
    try:
        _post_findings_as_comments(findings, review_id)
    except Exception as exc:
        logger.warning("Comment posting failed (non-fatal): %s", exc)

    return review_id, scored_findings


def import_pr_description(
    cursor: psycopg2.extensions.cursor,
    pr_desc_path: Path,
    ticket_id: str
) -> Optional[int]:
    """
    Import PR description into pr_descriptions + pr_description_sections.
    
    Returns pr_description_id if successful, None if file missing.
    """
    if not pr_desc_path.exists():
        print(f"⚠️  No PR description file: {pr_desc_path}")
        return None
    
    content = pr_desc_path.read_text()
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    
    # Check if already imported (deduplication)
    cursor.execute(
        "SELECT id FROM pr_descriptions WHERE sha256_hash = %s",
        (content_hash,)
    )
    existing = cursor.fetchone()
    if existing:
        print(f"⚠️  PR description already imported (hash match)")
        return existing['id']
    
    # Insert PR description
    cursor.execute(
        """
        INSERT INTO pr_descriptions (ticket_id, description_text, sha256_hash)
        VALUES (%s, %s, %s)
        RETURNING id
        """,
        (ticket_id, content, content_hash)
    )
    pr_desc_id = cursor.fetchone()['id']
    
    # Parse sections (split on ## headers)
    sections = _parse_pr_sections(content)
    for section_type, section_content in sections.items():
        # Skip empty sections
        if not section_content.strip():
            continue
        cursor.execute(
            """
            INSERT INTO pr_description_sections (
                pr_description_id, section_type, content
            )
            VALUES (%s, %s, %s)
            """,
            (pr_desc_id, section_type, section_content)
        )
    
    print(f"✅ Imported PR description ({len(sections)} sections)")
    return pr_desc_id


def _parse_pr_sections(content: str) -> dict[str, str]:
    """
    Split PR description by ## headers.
    
    Returns dict: {section_type: content}
    E.g., {"summary": "...", "out_of_scope": "..."}
    """
    sections = {}
    current_section = "preamble"
    current_content = []
    
    for line in content.split("\n"):
        if line.startswith("##"):
            # Save previous section
            if current_content:
                sections[current_section] = "\n".join(current_content).strip()
            
            # Start new section
            current_section = line.replace("##", "").strip().lower().replace(" ", "_")
            current_content = []
        else:
            current_content.append(line)
    
    # Save last section
    if current_content:
        sections[current_section] = "\n".join(current_content).strip()
    
    return sections


def import_comparison(
    comparison_dir: Path,
    model: str,
    provider: str,
    branch: str = "main",
    tier: str = "free"
) -> None:
    """
    Import a full comparison (baseline + contextual + PR description).
    
    Args:
        comparison_dir: Path to docs/review-comparisons/REVUE-XX/
        model: AI model name (e.g., 'claude-sonnet-4-5')
        provider: AI provider ('anthropic', 'openai')
        branch: Git branch (default: 'main')
        tier: License tier (default: 'free')
    """
    ticket_id = comparison_dir.name
    baseline_path = comparison_dir / "baseline.json"
    contextual_path = comparison_dir / "contextual.json"
    pr_desc_path = comparison_dir / "pr_description.txt"
    
    if not baseline_path.exists():
        raise FileNotFoundError(f"Missing baseline.json in {comparison_dir}")
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Lookup reference IDs
        model_id = get_or_create_model(cursor, model, provider)
        tier_id = get_lookup_id(cursor, "tiers", tier)
        baseline_mode_id = get_lookup_id(cursor, "review_modes", "baseline")
        contextual_mode_id = get_lookup_id(cursor, "review_modes", "contextual")
        
        # Import baseline review
        all_scored_findings: list[tuple[int, dict]] = []
        baseline_review_id, baseline_findings = import_review(
            cursor, baseline_path, ticket_id, branch,
            model_id, tier_id, baseline_mode_id
        )
        all_scored_findings.extend(baseline_findings)

        # Import contextual review (if exists)
        contextual_review_id = None
        if contextual_path.exists():
            contextual_review_id, contextual_findings = import_review(
                cursor, contextual_path, ticket_id, branch,
                model_id, tier_id, contextual_mode_id
            )
            all_scored_findings.extend(contextual_findings)
        
        # Link in comparison_runs table
        if baseline_review_id and contextual_review_id:
            cursor.execute(
                """
                INSERT INTO comparison_runs (
                    ticket_id, baseline_review_id, contextual_review_id
                )
                VALUES (%s, %s, %s)
                ON CONFLICT (baseline_review_id, contextual_review_id) DO NOTHING
                """,
                (ticket_id, baseline_review_id, contextual_review_id)
            )
            print(f"✅ Linked comparison run: baseline→contextual")
        
        # Auto-score all imported findings (REVUE-93)
        if all_scored_findings:
            score_findings(cursor, all_scored_findings)

        # Import PR description
        import_pr_description(cursor, pr_desc_path, ticket_id)

        conn.commit()
        print(f"\n✅ Import complete: {ticket_id}")
        
    except psycopg2.OperationalError as e:
        print(f"⚠️  Database unreachable: {e}", file=sys.stderr)
        print(f"   Comparison results saved to JSON only.", file=sys.stderr)
        sys.exit(0)  # Graceful degradation (AC4)
    except Exception as e:
        if 'conn' in locals():
            conn.rollback()
        raise RuntimeError(f"Import failed: {e}") from e
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Import review comparison results into Postgres"
    )
    parser.add_argument(
        "comparison_dir",
        type=Path,
        help="Path to comparison directory (e.g., docs/review-comparisons/REVUE-XX/)"
    )
    parser.add_argument(
        "--model",
        required=True,
        help="AI model name (e.g., claude-sonnet-4-5)"
    )
    parser.add_argument(
        "--provider",
        required=True,
        choices=["anthropic", "openai"],
        help="AI provider"
    )
    parser.add_argument(
        "--branch",
        default="main",
        help="Git branch (default: main)"
    )
    parser.add_argument(
        "--tier",
        default="free",
        choices=["free", "pro", "enterprise"],
        help="License tier (default: free)"
    )
    
    args = parser.parse_args()
    
    if not args.comparison_dir.exists():
        print(f"Error: Directory not found: {args.comparison_dir}", file=sys.stderr)
        sys.exit(1)
    
    import_comparison(
        args.comparison_dir,
        args.model,
        args.provider,
        args.branch,
        args.tier
    )


if __name__ == "__main__":
    main()
