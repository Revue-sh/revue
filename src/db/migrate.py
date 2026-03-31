#!/usr/bin/env python3
"""
Database migration runner for Revue.io knowledge base.

Usage:
    python3 src/db/migrate.py src/db/migrations/001_initial_schema.sql

Environment Variables Required:
    DATABASE_URL - Full Postgres connection string
"""

import sys
import os
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("❌ psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


def run_migration(migration_file: str) -> None:
    """
    Execute a SQL migration file against the configured database.
    
    Args:
        migration_file: Path to .sql migration file
    
    Raises:
        FileNotFoundError: If migration file doesn't exist
        psycopg2.Error: If database connection or execution fails
    """
    # Validate migration file exists
    migration_path = Path(migration_file)
    if not migration_path.exists():
        raise FileNotFoundError(f"Migration file not found: {migration_file}")
    
    # Get database URL from environment
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise ValueError("DATABASE_URL environment variable not set. Run: source ~/.zshenv")
    
    print(f"🔄 Running migration: {migration_path.name}")
    print(f"📦 Database: {database_url.split('@')[1] if '@' in database_url else 'unknown'}")
    
    # Read migration SQL
    sql = migration_path.read_text()
    
    # Connect and execute
    conn = None
    try:
        conn = psycopg2.connect(database_url)
        cursor = conn.cursor()
        
        # Check if migration was already applied (if schema_version table exists)
        try:
            cursor.execute("""
                SELECT version FROM schema_version 
                WHERE description LIKE %s 
                LIMIT 1;
            """, (f"%{migration_path.stem}%",))
            
            existing = cursor.fetchone()
            if existing:
                print(f"⚠️  Migration appears to have been applied already (version {existing[0]})")
                print(f"   Skipping to avoid duplicate execution.")
                cursor.close()
                conn.close()
                return
        except psycopg2.Error:
            # schema_version table doesn't exist yet - first migration
            pass
        
        # Execute migration
        cursor.execute(sql)
        
        # Commit transaction
        conn.commit()
        
        # Verify schema_version
        cursor.execute("SELECT version, description, applied_at FROM schema_version ORDER BY version DESC LIMIT 1;")
        version_info = cursor.fetchone()
        
        if version_info:
            version, description, applied_at = version_info
            print(f"✅ Migration complete!")
            print(f"   Schema version: {version}")
            print(f"   Description: {description}")
            print(f"   Applied at: {applied_at}")
        else:
            print("⚠️  Migration executed but schema_version table is empty")
        
        cursor.close()
        
    except psycopg2.Error as e:
        print(f"❌ Database error: {e}")
        if conn:
            conn.rollback()
        raise
    
    finally:
        if conn:
            conn.close()


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 src/db/migrate.py <migration_file.sql>")
        print("")
        print("Example:")
        print("  python3 src/db/migrate.py src/db/migrations/001_initial_schema.sql")
        sys.exit(1)
    
    migration_file = sys.argv[1]
    
    try:
        run_migration(migration_file)
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
