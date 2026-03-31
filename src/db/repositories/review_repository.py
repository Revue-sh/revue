"""Repository for review data access."""

from typing import Optional
from datetime import datetime

from db.repositories.base import BaseRepository
from reviews.models import Review, FindingSummary


class ReviewRepository(BaseRepository):
    """Data access layer for reviews."""

    def list_reviews(
        self, limit: int = 100, offset: int = 0
    ) -> list[Review]:
        """List all reviews with finding counts.
        
        Args:
            limit: Maximum number of reviews to return
            offset: Number of reviews to skip
            
        Returns:
            List of Review domain objects ordered by creation date (newest first)
        """
        rows = self._execute(
            """
            SELECT 
                r.id,
                r.ticket_id,
                r.branch,
                m.name AS model,
                t.name AS tier,
                r.run_at AS created_at,
                COUNT(DISTINCT f.id) AS finding_count
            FROM reviews r
            JOIN models m ON r.model_id = m.id
            JOIN tiers t ON r.tier_id = t.id
            LEFT JOIN findings f ON f.review_id = r.id
            GROUP BY r.id, m.name, t.name, r.run_at
            ORDER BY r.run_at DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
        return [
            Review(
                id=row.get("id"),
                ticket_id=row.get("ticket_id"),
                branch=row.get("branch"),
                model=row.get("model"),
                tier=row.get("tier"),
                created_at=row.get("created_at"),
                finding_count=row.get("finding_count", 0),
            )
            for row in rows
        ]

    def get_by_ticket(self, ticket_id: str) -> Optional[Review]:
        """Get review by ticket ID (most recent if multiple).
        
        Args:
            ticket_id: Jira ticket ID (e.g., REVUE-91)
            
        Returns:
            Review object or None if not found
        """
        row = self._execute_one(
            """
            SELECT 
                r.id,
                r.ticket_id,
                r.branch,
                m.name AS model,
                t.name AS tier,
                r.run_at AS created_at,
                COUNT(DISTINCT f.id) AS finding_count
            FROM reviews r
            JOIN models m ON r.model_id = m.id
            JOIN tiers t ON r.tier_id = t.id
            LEFT JOIN findings f ON f.review_id = r.id
            WHERE r.ticket_id = %s
            GROUP BY r.id, m.name, t.name, r.run_at
            ORDER BY r.run_at DESC
            LIMIT 1
            """,
            (ticket_id,),
        )
        
        if not row:
            return None
        
        return Review(
            id=row["id"],
            ticket_id=row["ticket_id"],
            branch=row["branch"],
            model=row["model"],
            tier=row["tier"],
            created_at=row["created_at"],
            finding_count=row["finding_count"],
        )

    def get_findings_by_review(self, review_id: int) -> list[FindingSummary]:
        """Get findings for a specific review.
        
        Args:
            review_id: Internal review ID
            
        Returns:
            List of FindingSummary objects
        """
        rows = self._execute(
            """
            SELECT 
                f.id,
                sl.name AS severity,
                f.file_path,
                f.issue,
                rm.name AS mode
            FROM findings f
            JOIN severity_levels sl ON f.severity_id = sl.id
            JOIN reviews r ON f.review_id = r.id
            JOIN review_modes rm ON r.mode_id = rm.id
            WHERE f.review_id = %s
            ORDER BY sl.id ASC, f.file_path ASC
            """,
            (review_id,),
        )
        
        return [
            FindingSummary(
                id=row["id"],
                severity=row["severity"],
                file_path=row["file_path"],
                issue=row["issue"],
                mode=row["mode"],
            )
            for row in rows
        ]

    def get_pr_description(self, ticket_id: str) -> Optional[str]:
        """Get PR description for a ticket.
        
        Args:
            ticket_id: Jira ticket ID
            
        Returns:
            PR description text or None if not found
        """
        row = self._execute_one(
            """
            SELECT description_text
            FROM pr_descriptions
            WHERE ticket_id = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (ticket_id,),
        )
        
        return row["description_text"] if row else None

    def get_false_positive_patterns(self, top: int = 10) -> list[dict]:
        """Get most recurring false positive patterns.
        
        Args:
            top: Number of top patterns to return
            
        Returns:
            List of dicts with pattern, count, and example findings
        """
        rows = self._execute(
            """
            SELECT 
                fo.fp_reason_id,
                fpr.code AS reason_code,
                fpr.description AS reason_description,
                COUNT(DISTINCT fo.finding_id) AS occurrence_count,
                COUNT(DISTINCT f.review_id) AS review_count,
                array_agg(DISTINCT f.file_path ORDER BY f.file_path) AS example_files
            FROM finding_outcomes fo
            JOIN fp_reasons fpr ON fo.fp_reason_id = fpr.id
            JOIN findings f ON fo.finding_id = f.id
            WHERE fo.is_false_positive = TRUE
            GROUP BY fo.fp_reason_id, fpr.code, fpr.description
            ORDER BY occurrence_count DESC
            LIMIT %s
            """,
            (top,),
        )
        return rows

    def get_clarity_scores_by_model(self, model_name: str = None) -> list[dict]:
        """Get average clarity scores per model.
        
        Args:
            model_name: Optional model name filter
            
        Returns:
            List of dicts with model, avg_clarity, rated_count
        """
        if model_name:
            rows = self._execute(
                """
                SELECT 
                    m.name AS model,
                    AVG(fq.score) AS avg_clarity,
                    COUNT(DISTINCT fq.finding_id) AS rated_count,
                    COUNT(DISTINCT r.id) AS review_count
                FROM finding_quality fq
                JOIN quality_dimensions qd ON fq.dimension_id = qd.id
                JOIN findings f ON fq.finding_id = f.id
                JOIN reviews r ON f.review_id = r.id
                JOIN models m ON r.model_id = m.id
                WHERE qd.name = 'clarity' AND m.name = %s
                GROUP BY m.name
                ORDER BY avg_clarity DESC
                """,
                (model_name,),
            )
        else:
            rows = self._execute(
                """
                SELECT 
                    m.name AS model,
                    AVG(fq.score) AS avg_clarity,
                    COUNT(DISTINCT fq.finding_id) AS rated_count,
                    COUNT(DISTINCT r.id) AS review_count
                FROM finding_quality fq
                JOIN quality_dimensions qd ON fq.dimension_id = qd.id
                JOIN findings f ON fq.finding_id = f.id
                JOIN reviews r ON f.review_id = r.id
                JOIN models m ON r.model_id = m.id
                WHERE qd.name = 'clarity'
                GROUP BY m.name
                ORDER BY avg_clarity DESC
                """
            )
        return rows

    def get_suppression_trend(self) -> list[dict]:
        """Get context suppression rate over time.
        
        Returns:
            List of dicts with date, baseline_findings, contextual_findings, suppression_rate
        """
        rows = self._execute(
            """
            SELECT 
                DATE(rb.run_at) AS review_date,
                rb.ticket_id,
                COUNT(DISTINCT fb.id) AS baseline_findings,
                COUNT(DISTINCT fc.id) AS contextual_findings,
                ROUND(
                    100.0 * (COUNT(DISTINCT fb.id) - COUNT(DISTINCT fc.id))::numeric / 
                    NULLIF(COUNT(DISTINCT fb.id), 0),
                    1
                ) AS suppression_rate_pct
            FROM comparison_runs cr
            JOIN reviews rb ON cr.baseline_review_id = rb.id
            JOIN reviews rc ON cr.contextual_review_id = rc.id
            LEFT JOIN findings fb ON fb.review_id = rb.id
            LEFT JOIN findings fc ON fc.review_id = rc.id
            GROUP BY DATE(rb.run_at), rb.ticket_id, rb.run_at
            ORDER BY rb.run_at DESC
            """
        )
        return rows

    def get_active_patterns(self) -> dict:
        """Get active allowed and disallowed patterns.
        
        Returns:
            Dict with 'allowed' and 'disallowed' lists
        """
        allowed = self._execute(
            """
            SELECT 
                ap.id,
                ap.pattern_text AS pattern,
                ap.rationale,
                ap.created_at,
                COUNT(DISTINCT fpm.finding_id) AS matched_findings
            FROM allowed_patterns ap
            LEFT JOIN finding_pattern_matches fpm ON fpm.pattern_id = ap.id
            LEFT JOIN pattern_types pt ON fpm.pattern_type_id = pt.id AND pt.name = 'allowed'
            GROUP BY ap.id, ap.pattern_text, ap.rationale, ap.created_at
            ORDER BY ap.created_at DESC
            """
        )
        
        disallowed = self._execute(
            """
            SELECT 
                dp.id,
                dp.pattern_text AS pattern,
                dp.rationale,
                dp.created_at,
                COUNT(DISTINCT fpm.finding_id) AS matched_findings
            FROM disallowed_patterns dp
            LEFT JOIN finding_pattern_matches fpm ON fpm.pattern_id = dp.id
            LEFT JOIN pattern_types pt ON fpm.pattern_type_id = pt.id AND pt.name = 'disallowed'
            GROUP BY dp.id, dp.pattern_text, dp.rationale, dp.created_at
            ORDER BY dp.created_at DESC
            """
        )
        
        return {"allowed": allowed, "disallowed": disallowed}
