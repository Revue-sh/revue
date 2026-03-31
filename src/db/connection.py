"""Database connection utilities."""

import os
import psycopg2
from psycopg2.extras import RealDictCursor


def get_db_connection() -> psycopg2.extensions.connection:
    """Connect to Postgres using DATABASE_URL from environment.
    
    Returns:
        psycopg2 connection object with RealDictCursor
        
    Raises:
        RuntimeError: If DATABASE_URL environment variable not set
    """
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL not set. Run: source ~/.zshenv"
        )
    return psycopg2.connect(database_url, cursor_factory=RealDictCursor)
