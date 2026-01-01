"""Database management for the Traycer queue system."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator


class Database:
    """Manages SQLite database for tracking issues, processing history, and errors."""

    def __init__(self, db_path: str | Path = "traycer_queue.db"):
        """Initialize database connection.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for database connections.

        Yields:
            SQLite connection with row factory enabled
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Table for issues awaiting re-analysis
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS queued_issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo_name TEXT NOT NULL,
                    issue_number INTEGER NOT NULL,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    next_retry_at TIMESTAMP,
                    retry_count INTEGER DEFAULT 0,
                    last_error TEXT,
                    UNIQUE(repo_name, issue_number)
                )
            """)

            # Table for processing history (used for slot calculation)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS processing_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo_name TEXT NOT NULL,
                    issue_number INTEGER NOT NULL,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    success BOOLEAN NOT NULL,
                    rate_limit_message TEXT,
                    rate_limit_seconds INTEGER
                )
            """)

            # Table for error logging
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS error_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    error_type TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    repo_name TEXT,
                    issue_number INTEGER
                )
            """)

            # Create indexes for common queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_queued_issues_retry
                ON queued_issues(next_retry_at)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_processing_history_time
                ON processing_history(processed_at)
            """)

    def add_issue(
        self, repo_name: str, issue_number: int, next_retry_at: datetime | None = None
    ) -> bool:
        """Add or update an issue in the queue.

        Args:
            repo_name: Repository full name (owner/repo)
            issue_number: Issue number
            next_retry_at: When to retry (defaults to now + 32 minutes)

        Returns:
            True if added (new), False if already exists (and was updated)
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Check if issue already exists
            cursor.execute(
                "SELECT 1 FROM queued_issues WHERE repo_name = ? AND issue_number = ?",
                (repo_name, issue_number),
            )
            exists = cursor.fetchone() is not None

            cursor.execute(
                """
                INSERT INTO queued_issues (repo_name, issue_number, next_retry_at)
                VALUES (?, ?, ?)
                ON CONFLICT(repo_name, issue_number)
                DO UPDATE SET next_retry_at = excluded.next_retry_at
            """,
                (repo_name, issue_number, next_retry_at),
            )
            return not exists

    def remove_issue(self, repo_name: str, issue_number: int) -> None:
        """Remove an issue from the queue.

        Args:
            repo_name: Repository full name
            issue_number: Issue number
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM queued_issues WHERE repo_name = ? AND issue_number = ?",
                (repo_name, issue_number),
            )

    def get_issues_ready_for_processing(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Get issues ready for processing (next_retry_at <= now).

        Args:
            limit: Maximum number of issues to return

        Returns:
            List of issue records as dictionaries
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = """
                SELECT * FROM queued_issues
                WHERE next_retry_at IS NULL OR next_retry_at <= ?
                ORDER BY next_retry_at ASC
            """
            if limit:
                query += f" LIMIT {limit}"

            cursor.execute(query, (datetime.now(),))
            return [dict(row) for row in cursor.fetchall()]

    def increment_retry_count(
        self, repo_name: str, issue_number: int, error: str, next_retry_at: datetime | None = None
    ) -> None:
        """Increment retry count for an issue and log error.

        Args:
            repo_name: Repository full name
            issue_number: Issue number
            error: Error message to log
            next_retry_at: Optional new retry time (if rate limited again)
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if next_retry_at:
                cursor.execute(
                    """
                    UPDATE queued_issues
                    SET retry_count = retry_count + 1, last_error = ?, next_retry_at = ?
                    WHERE repo_name = ? AND issue_number = ?
                """,
                    (error, next_retry_at, repo_name, issue_number),
                )
            else:
                cursor.execute(
                    """
                    UPDATE queued_issues
                    SET retry_count = retry_count + 1, last_error = ?
                    WHERE repo_name = ? AND issue_number = ?
                """,
                    (error, repo_name, issue_number),
                )

    def log_processing(
        self,
        repo_name: str,
        issue_number: int,
        success: bool,
        rate_limit_message: str | None = None,
        rate_limit_seconds: int | None = None,
    ) -> None:
        """Log a processing attempt.

        Args:
            repo_name: Repository full name
            issue_number: Issue number
            success: Whether processing succeeded
            rate_limit_message: Rate limit error message if applicable
            rate_limit_seconds: Seconds to wait from rate limit message
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO processing_history
                (repo_name, issue_number, success, rate_limit_message, rate_limit_seconds)
                VALUES (?, ?, ?, ?, ?)
            """,
                (repo_name, issue_number, success, rate_limit_message, rate_limit_seconds),
            )

    def log_error(
        self,
        error_type: str,
        error_message: str,
        repo_name: str | None = None,
        issue_number: int | None = None,
    ) -> None:
        """Log an error.

        Args:
            error_type: Type of error (e.g., 'api_error', 'rate_limit')
            error_message: Detailed error message
            repo_name: Repository name if error is issue-specific
            issue_number: Issue number if error is issue-specific
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO error_log (error_type, error_message, repo_name, issue_number)
                VALUES (?, ?, ?, ?)
            """,
                (error_type, error_message, repo_name, issue_number),
            )

    def get_recent_processing_history(self, minutes: int = 30) -> list[dict[str, Any]]:
        """Get processing history from the last N minutes.

        Args:
            minutes: Number of minutes to look back

        Returns:
            List of processing records
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM processing_history
                WHERE processed_at >= datetime('now', '-' || ? || ' minutes')
                ORDER BY processed_at DESC
            """,
                (minutes,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_consecutive_errors(self, limit: int = 5) -> list[dict[str, Any]]:
        """Get most recent consecutive errors.

        Args:
            limit: Number of recent errors to check

        Returns:
            List of recent error records
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM error_log
                WHERE error_type NOT IN ('rate_limit', 'max_retries', 'circuit_breaker')
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]
