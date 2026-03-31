"""Base repository with shared database utilities."""

from abc import ABC
from typing import Any, Optional


class BaseRepository(ABC):
    """Base class for all repositories providing common DB utilities."""

    def __init__(self, connection):
        """Initialize repository with database connection.
        
        Args:
            connection: psycopg2 connection object
        """
        self.conn = connection

    def _execute(
        self, query: str, params: tuple = (), fetch: bool = True
    ) -> list[dict[str, Any]]:
        """Execute SQL query and return results as list of dicts.
        
        Args:
            query: SQL query string (use %s for parameters)
            params: Query parameters tuple
            fetch: Whether to fetch results (False for INSERT/UPDATE/DELETE)
            
        Returns:
            List of dicts with column names as keys (empty list for non-SELECT)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(query, params)
            
            if fetch and cursor.description:
                # RealDictCursor returns dict-like rows, convert to plain dict
                return [dict(row) for row in cursor.fetchall()]
            
            return []

    def _execute_one(
        self, query: str, params: tuple = ()
    ) -> Optional[dict[str, Any]]:
        """Execute query and return single row as dict.
        
        Args:
            query: SQL query string
            params: Query parameters tuple
            
        Returns:
            Dict with column names as keys, or None if no results
        """
        results = self._execute(query, params)
        return results[0] if results else None
