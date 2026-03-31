"""Repository layer for data access abstraction."""

from .base import BaseRepository
from .review_repository import ReviewRepository

__all__ = ["BaseRepository", "ReviewRepository"]
