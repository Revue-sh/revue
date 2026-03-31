#!/usr/bin/env python3
"""CLI for querying review knowledge base.

Usage:
    reviews.py list [--limit N] [--format table|json]
    reviews.py show REVUE-XX [--format table|json]
    reviews.py false-positives [--top N] [--format table|json]
    reviews.py clarity [--model NAME] [--format table|json]
    reviews.py suppression-trend [--format table|json]
    reviews.py patterns [--format table|json]

All queries support --format flag for table (default) or JSON output.
"""

import sys
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from db.connection import get_db_connection
from db.repositories.review_repository import ReviewRepository
from reviews.service import ReviewService
from reviews.models import Review, ReviewDetail


console = Console()


def create_service() -> ReviewService:
    """Factory function to create ReviewService with dependencies."""
    try:
        conn = get_db_connection()
        repo = ReviewRepository(conn)
        return ReviewService(repo)
    except Exception as e:
        console.print(f"[red]Error connecting to database: {e}[/red]")
        console.print("[yellow]Ensure Postgres is running: docker ps | grep revue-db[/yellow]")
        sys.exit(1)


@click.group()
def cli():
    """Query review knowledge base."""
    pass


@cli.command()
@click.option("--limit", default=100, help="Maximum reviews to show")
@click.option("--offset", default=0, help="Number of reviews to skip")
@click.option("--format", type=click.Choice(["table", "json"]), default="table", help="Output format")
def list(limit: int, offset: int, format: str):
    """List all reviews with finding counts."""
    service = create_service()
    reviews = service.get_all_reviews(limit=limit, offset=offset)

    if not reviews:
        console.print("[yellow]No reviews found in database.[/yellow]")
        console.print("[dim]Run ./scripts/run-comparison.sh REVUE-XX /path/to/pr_desc.txt to populate.[/dim]")
        return

    if format == "json":
        import json
        data = [
            {
                "ticket_id": r.ticket_id,
                "branch": r.branch,
                "model": r.model,
                "tier": r.tier,
                "finding_count": r.finding_count,
                "created_at": r.created_at.isoformat(),
            }
            for r in reviews
        ]
        console.print(json.dumps(data, indent=2))
    else:
        table = Table(title=f"Reviews ({len(reviews)} total)")
        table.add_column("Ticket ID", style="cyan")
        table.add_column("Branch", style="magenta")
        table.add_column("Model", style="green")
        table.add_column("Tier", style="blue")
        table.add_column("Findings", style="yellow", justify="right")
        table.add_column("Created", style="dim")

        for review in reviews:
            # Handle created_at as datetime or string
            if hasattr(review.created_at, 'strftime'):
                created_str = review.created_at.strftime("%Y-%m-%d %H:%M")
            else:
                created_str = str(review.created_at)[:16]  # Truncate ISO string
            
            table.add_row(
                review.ticket_id,
                review.branch,
                review.model,
                review.tier,
                str(review.finding_count),
                created_str,
            )

        console.print(table)


@cli.command()
@click.argument("ticket_id")
@click.option("--format", type=click.Choice(["table", "json"]), default="table", help="Output format")
def show(ticket_id: str, format: str):
    """Show full details for a specific review."""
    service = create_service()
    details = service.get_review_details(ticket_id)

    if not details:
        console.print(f"[red]No review found for ticket: {ticket_id}[/red]")
        sys.exit(1)

    if format == "json":
        import json
        data = {
            "review": {
                "ticket_id": details.review.ticket_id,
                "branch": details.review.branch,
                "model": details.review.model,
                "tier": details.review.tier,
                "finding_count": details.review.finding_count,
                "created_at": details.review.created_at.isoformat(),
            },
            "findings": [
                {
                    "severity": f.severity,
                    "file_path": f.file_path,
                    "issue": f.issue,
                    "mode": f.mode,
                }
                for f in details.findings
            ],
            "pr_description": details.pr_description,
        }
        console.print(json.dumps(data, indent=2))
    else:
        # Review summary
        console.print(f"\n[bold cyan]Review: {details.review.ticket_id}[/bold cyan]")
        console.print(f"Branch: {details.review.branch}")
        console.print(f"Model: {details.review.model} ({details.review.tier})")
        
        # Handle created_at as datetime or string
        if hasattr(details.review.created_at, 'strftime'):
            created_str = details.review.created_at.strftime('%Y-%m-%d %H:%M')
        else:
            created_str = str(details.review.created_at)[:16]
        
        console.print(f"Created: {created_str}")
        console.print(f"Findings: {details.review.finding_count}\n")

        # Findings table
        if details.findings:
            table = Table(title="Findings")
            table.add_column("Severity", style="red")
            table.add_column("Mode", style="blue")
            table.add_column("File", style="cyan")
            table.add_column("Issue", style="white")

            for finding in details.findings:
                table.add_row(
                    finding.severity,
                    finding.mode,
                    finding.file_path,
                    finding.issue[:80] + "..." if len(finding.issue) > 80 else finding.issue,
                )

            console.print(table)
        else:
            console.print("[dim]No findings recorded.[/dim]")

        # PR description
        if details.pr_description:
            console.print(f"\n[bold]PR Description:[/bold]")
            console.print(f"[dim]{details.pr_description[:200]}...[/dim]" if len(details.pr_description) > 200 else f"[dim]{details.pr_description}[/dim]")


@cli.command()
@click.option("--top", default=10, help="Top N patterns to show")
@click.option("--format", type=click.Choice(["table", "json"]), default="table", help="Output format")
def false_positives(top: int, format: str):
    """Show most recurring false positive patterns."""
    service = create_service()
    patterns = service.get_false_positive_patterns(top)

    if not patterns:
        console.print("[yellow]No false positive data found.[/yellow]")
        console.print("[dim]Use 'reviews.py rate REVUE-XX' to mark findings as false positives (REVUE-92).[/dim]")
        return

    if format == "json":
        import json
        console.print(json.dumps(patterns, indent=2, default=str))
    else:
        table = Table(title=f"Top {len(patterns)} False Positive Patterns")
        table.add_column("Reason", style="cyan")
        table.add_column("Occurrences", style="yellow", justify="right")
        table.add_column("Reviews", style="blue", justify="right")
        table.add_column("Example Files", style="dim")

        for pattern in patterns:
            example_files = ", ".join(pattern.get("example_files", [])[:2])
            table.add_row(
                pattern["reason_code"],
                str(pattern["occurrence_count"]),
                str(pattern["review_count"]),
                example_files[:60] + "..." if len(example_files) > 60 else example_files,
            )

        console.print(table)
        console.print(f"\n[dim]Tip: Use these patterns in .revue.yml to suppress known false positives (REVUE-94).[/dim]")


@cli.command()
@click.option("--model", default=None, help="Filter by model name")
@click.option("--format", type=click.Choice(["table", "json"]), default="table", help="Output format")
def clarity(model: Optional[str], format: str):
    """Show average clarity scores per model."""
    service = create_service()
    scores = service.get_clarity_scores(model)

    if not scores:
        if model:
            console.print(f"[yellow]No clarity data found for model: {model}[/yellow]")
        else:
            console.print("[yellow]No clarity data found.[/yellow]")
        console.print("[dim]Use 'reviews.py rate REVUE-XX' to add quality ratings (REVUE-92).[/dim]")
        return

    if format == "json":
        import json
        # Convert Decimal to float for JSON serialization
        scores_serializable = [
            {**s, "avg_clarity": float(s["avg_clarity"]) if s["avg_clarity"] else None}
            for s in scores
        ]
        console.print(json.dumps(scores_serializable, indent=2))
    else:
        table = Table(title="Model Clarity Scores" + (f" (filtered: {model})" if model else ""))
        table.add_column("Model", style="cyan")
        table.add_column("Avg Clarity", style="green", justify="right")
        table.add_column("Rated Findings", style="yellow", justify="right")
        table.add_column("Reviews", style="blue", justify="right")

        for score in scores:
            avg_clarity = score.get("avg_clarity")
            clarity_str = f"{float(avg_clarity):.2f}/5.0" if avg_clarity else "N/A"
            table.add_row(
                score["model"],
                clarity_str,
                str(score["rated_count"]),
                str(score["review_count"]),
            )

        console.print(table)
        console.print(f"\n[dim]Clarity scale: 1 (unclear) to 5 (very clear). Higher is better.[/dim]")


@cli.command()
@click.option("--format", type=click.Choice(["table", "json"]), default="table", help="Output format")
def suppression_trend(format: str):
    """Show context suppression rate over time."""
    service = create_service()
    trends = service.get_suppression_trend()

    if not trends:
        console.print("[yellow]No comparison data found.[/yellow]")
        console.print("[dim]Run './scripts/run-comparison.sh REVUE-XX /path/to/pr_desc.txt' to generate comparisons.[/dim]")
        return

    if format == "json":
        import json
        # Convert Decimal to float for JSON serialization
        trends_serializable = [
            {**t, "suppression_rate_pct": float(t["suppression_rate_pct"]) if t["suppression_rate_pct"] else None}
            for t in trends
        ]
        console.print(json.dumps(trends_serializable, indent=2, default=str))
    else:
        table = Table(title="Context Suppression Trend")
        table.add_column("Date", style="cyan")
        table.add_column("Ticket", style="magenta")
        table.add_column("Baseline", style="yellow", justify="right")
        table.add_column("Contextual", style="green", justify="right")
        table.add_column("Suppression", style="blue", justify="right")

        for trend in trends:
            suppression = trend.get("suppression_rate_pct")
            suppression_str = f"{float(suppression):.1f}%" if suppression is not None else "N/A"
            
            # Color code: green if high suppression (good), red if negative
            if suppression and float(suppression) > 20:
                suppression_str = f"[green]{suppression_str}[/green]"
            elif suppression and float(suppression) < 0:
                suppression_str = f"[red]{suppression_str}[/red]"
            
            table.add_row(
                str(trend["review_date"]),
                trend["ticket_id"],
                str(trend["baseline_findings"]),
                str(trend["contextual_findings"]),
                suppression_str,
            )

        console.print(table)
        console.print(f"\n[dim]Suppression rate = (baseline - contextual) / baseline. Higher is better (context reduces noise).[/dim]")


@cli.command()
@click.option("--format", type=click.Choice(["table", "json"]), default="table", help="Output format")
def patterns(format: str):
    """Show active allowed/disallowed patterns."""
    service = create_service()
    pattern_data = service.get_active_patterns()

    allowed = pattern_data.get("allowed", [])
    disallowed = pattern_data.get("disallowed", [])

    if not allowed and not disallowed:
        console.print("[yellow]No active patterns found.[/yellow]")
        console.print("[dim]Define patterns in .revue.yml to suppress false positives (REVUE-94).[/dim]")
        return

    if format == "json":
        import json
        console.print(json.dumps(pattern_data, indent=2, default=str))
    else:
        if allowed:
            table = Table(title="Allowed Patterns (False Positive Suppressors)")
            table.add_column("Pattern", style="green")
            table.add_column("Rationale", style="dim")
            table.add_column("Matches", style="yellow", justify="right")

            for pattern in allowed:
                table.add_row(
                    pattern["pattern"][:50] + "..." if len(pattern["pattern"]) > 50 else pattern["pattern"],
                    pattern["rationale"][:60] + "..." if len(pattern["rationale"]) > 60 else pattern["rationale"],
                    str(pattern.get("matched_findings", 0)),
                )

            console.print(table)
            console.print()

        if disallowed:
            table = Table(title="Disallowed Patterns (Must Not Appear)")
            table.add_column("Pattern", style="red")
            table.add_column("Rationale", style="dim")
            table.add_column("Violations", style="yellow", justify="right")

            for pattern in disallowed:
                table.add_row(
                    pattern["pattern"][:50] + "..." if len(pattern["pattern"]) > 50 else pattern["pattern"],
                    pattern["rationale"][:60] + "..." if len(pattern["rationale"]) > 60 else pattern["rationale"],
                    str(pattern.get("matched_findings", 0)),
                )

            console.print(table)

        console.print(f"\n[dim]Configure patterns in .revue.yml under noise_filters section.[/dim]")


@cli.command()
@click.argument("ticket_id")
def rate(ticket_id: str):
    """Interactively rate findings for a review."""
    service = create_service()
    
    # Get findings to rate
    findings = service.get_findings_for_rating(ticket_id)
    
    if not findings:
        console.print(f"[yellow]No findings found for ticket: {ticket_id}[/yellow]")
        console.print("[dim]Run './scripts/run-comparison.sh REVUE-XX /path/to/pr_desc.txt' first.[/dim]")
        return
    
    # Get FP reasons for reference
    fp_reasons = service.get_fp_reasons()
    fp_reason_map = {r["code"]: r["description"] for r in fp_reasons}
    
    # Show summary
    total = len(findings)
    already_rated = sum(1 for f in findings if f["has_clarity"] and f["has_actionability"] and f["has_fp_status"])
    
    console.print(f"\n[bold cyan]Rating findings for {ticket_id}[/bold cyan]")
    console.print(f"Total findings: {total}")
    console.print(f"Already rated: {already_rated}")
    console.print(f"To rate: {total - already_rated}\n")
    
    if already_rated == total:
        console.print("[green]All findings already rated![/green]")
        cont = input("\nRe-rate all findings? (y/N): ").strip().lower()
        if cont != 'y':
            return
    
    console.print("[dim]Press Enter to skip any field. Type 'q' to quit.\n[/dim]")
    
    rated_count = 0
    
    for idx, finding in enumerate(findings, 1):
        # Show progress
        console.print(f"[bold]Finding {idx}/{total}[/bold]", style="blue")
        console.print(f"File: {finding['file_path']}:{finding['line_start'] or '?'}")
        console.print(f"Severity: {finding['severity']} | Mode: {finding['mode']}")
        console.print(f"\n[yellow]Issue:[/yellow] {finding['issue']}")
        
        if finding['details']:
            console.print(f"[dim]Details: {finding['details'][:150]}{'...' if len(finding['details']) > 150 else ''}[/dim]")
        
        if finding['recommendation']:
            console.print(f"[green]Recommendation:[/green] {finding['recommendation'][:150]}{'...' if len(finding['recommendation']) > 150 else ''}")
        
        console.print()
        
        # Skip if already fully rated (unless re-rating)
        if finding['has_clarity'] and finding['has_actionability'] and finding['has_fp_status']:
            skip = input("[dim]Already rated. Press Enter to skip, or 'r' to re-rate:[/dim] ").strip().lower()
            if skip != 'r':
                console.print("[dim]Skipped.\n[/dim]")
                console.print("─" * 60 + "\n")
                continue
        
        # Collect ratings
        clarity = None
        actionability = None
        is_fp = None
        fp_reason_code = None
        
        # Clarity rating
        while True:
            clarity_input = input("Clarity (1-5, or Enter to skip): ").strip()
            if clarity_input == 'q':
                console.print("\n[yellow]Quitting. Ratings saved so far.[/yellow]")
                return
            if not clarity_input:
                break
            try:
                clarity = int(clarity_input)
                if 1 <= clarity <= 5:
                    break
                console.print("[red]Please enter a number between 1 and 5.[/red]")
            except ValueError:
                console.print("[red]Please enter a number.[/red]")
        
        # Actionability rating
        while True:
            action_input = input("Actionability (1-5, or Enter to skip): ").strip()
            if action_input == 'q':
                console.print("\n[yellow]Quitting. Ratings saved so far.[/yellow]")
                return
            if not action_input:
                break
            try:
                actionability = int(action_input)
                if 1 <= actionability <= 5:
                    break
                console.print("[red]Please enter a number between 1 and 5.[/red]")
            except ValueError:
                console.print("[red]Please enter a number.[/red]")
        
        # False positive check
        while True:
            fp_input = input("Is this a false positive? (y/n, or Enter to skip): ").strip().lower()
            if fp_input == 'q':
                console.print("\n[yellow]Quitting. Ratings saved so far.[/yellow]")
                return
            if not fp_input:
                break
            if fp_input in ('y', 'n'):
                is_fp = (fp_input == 'y')
                break
            console.print("[red]Please enter 'y' or 'n'.[/red]")
        
        # If FP, ask for reason
        if is_fp:
            console.print("\n[yellow]False positive reasons:[/yellow]")
            for code, desc in fp_reason_map.items():
                console.print(f"  {code}: {desc}")
            
            while True:
                fp_reason_input = input(f"\nReason code (or Enter to skip): ").strip()
                if fp_reason_input == 'q':
                    console.print("\n[yellow]Quitting. Ratings saved so far.[/yellow]")
                    return
                if not fp_reason_input:
                    break
                if fp_reason_input in fp_reason_map:
                    fp_reason_code = fp_reason_input
                    break
                console.print(f"[red]Unknown reason code. Choose from: {', '.join(fp_reason_map.keys())}[/red]")
        
        # Save ratings
        if clarity or actionability or is_fp is not None:
            try:
                service.save_finding_rating(
                    finding['id'], clarity, actionability, is_fp, fp_reason_code
                )
                rated_count += 1
                console.print("[green]✓ Saved[/green]")
            except Exception as e:
                console.print(f"[red]Error saving rating: {e}[/red]")
        else:
            console.print("[dim]No ratings provided, skipping.[/dim]")
        
        console.print("\n" + "─" * 60 + "\n")
    
    # Summary
    console.print(f"\n[bold green]Rating complete![/bold green]")
    console.print(f"Rated: {rated_count}/{total} findings")
    console.print(f"\nRun [cyan]reviews.py false-positives[/cyan] or [cyan]reviews.py clarity[/cyan] to see updated stats.")


if __name__ == "__main__":
    cli()
