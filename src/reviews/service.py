"""Service layer for review business logic."""

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from db.repositories.review_repository import ReviewRepository

from reviews.models import Review, ReviewDetail


class ReviewService:
    """Business logic for review operations."""

    def __init__(self, review_repo: "ReviewRepository"):
        """Initialize service with repository dependency.
        
        Args:
            review_repo: ReviewRepository instance (injected)
        """
        self.review_repo = review_repo

    def get_all_reviews(self, limit: int = 100, offset: int = 0) -> list[Review]:
        """Get all reviews with pagination.
        
        Args:
            limit: Maximum reviews to return
            offset: Number of reviews to skip
            
        Returns:
            List of Review objects
        """
        return self.review_repo.list_reviews(limit=limit, offset=offset)

    def get_review_details(self, ticket_id: str) -> Optional[ReviewDetail]:
        """Get full review details including findings.
        
        Args:
            ticket_id: Jira ticket ID (e.g., REVUE-91)
            
        Returns:
            ReviewDetail with findings, or None if not found
        """
        review = self.review_repo.get_by_ticket(ticket_id)
        if not review:
            return None

        findings = self.review_repo.get_findings_by_review(review.id)
        pr_description = self.review_repo.get_pr_description(ticket_id)

        return ReviewDetail(
            review=review, findings=findings, pr_description=pr_description
        )

    def get_false_positive_patterns(self, top: int = 10) -> list[dict]:
        """Get most recurring false positive patterns.
        
        Args:
            top: Number of top patterns to return
            
        Returns:
            List of pattern statistics
        """
        return self.review_repo.get_false_positive_patterns(top)

    def get_clarity_scores(self, model_name: str = None) -> list[dict]:
        """Get average clarity scores per model.
        
        Args:
            model_name: Optional model name filter
            
        Returns:
            List of model clarity statistics
        """
        return self.review_repo.get_clarity_scores_by_model(model_name)

    def get_suppression_trend(self) -> list[dict]:
        """Get context suppression rate over time.
        
        Returns:
            List of suppression statistics by date
        """
        return self.review_repo.get_suppression_trend()

    def get_active_patterns(self) -> dict:
        """Get active allowed and disallowed patterns.
        
        Returns:
            Dict with 'allowed' and 'disallowed' pattern lists
        """
        return self.review_repo.get_active_patterns()
