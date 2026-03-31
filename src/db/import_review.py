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
import os
import sys
from pathlib import Path
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor


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
) -> Optional[int]:
    """
    Import a single review (baseline or contextual) into database.
    
    Returns review_id if successful, None if already imported (idempotent).
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
        return existing['id']
    
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
    
    # Insert findings
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
    
    print(f"✅ Imported {len(findings)} findings from {json_path.name}")
    return review_id


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
        baseline_review_id = import_review(
            cursor, baseline_path, ticket_id, branch,
            model_id, tier_id, baseline_mode_id
        )
        
        # Import contextual review (if exists)
        contextual_review_id = None
        if contextual_path.exists():
            contextual_review_id = import_review(
                cursor, contextual_path, ticket_id, branch,
                model_id, tier_id, contextual_mode_id
            )
        
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
