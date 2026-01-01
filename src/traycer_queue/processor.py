"""Queue processor for re-analyzing rate-limited issues."""

import re
import time
from datetime import datetime
from typing import Any

from github import Auth, Github, GithubException
from github.Issue import Issue

from .database import Database
from .scanner import IssueScanner
from .slot_calculator import SlotCalculator


class CircuitBreakerError(Exception):
    """Raised when circuit breaker trips due to consecutive errors."""

    pass


class QueueProcessor:
    """Processes queued issues by toggling assignment to trigger Traycer re-analysis."""

    MAX_RETRIES = 3
    CIRCUIT_BREAKER_THRESHOLD = 5
    RATE_LIMIT_PATTERN = re.compile(r"Rate limit exceeded\. Please try after (\d+) seconds\.")

    def __init__(self, github_token: str, username: str, db: Database):
        """Initialize queue processor.

        Args:
            github_token: GitHub personal access token
            username: GitHub username to assign issues to
            db: Database instance
        """
        auth = Auth.Token(github_token)
        self.github = Github(auth=auth)
        self.username = username
        self.db = db
        self.slot_calculator = SlotCalculator(db)
        self.consecutive_errors = 0

    def process_queue(self) -> dict[str, int]:
        """Process all issues ready for processing.

        Returns:
            Dictionary with processing statistics
        """
        stats = {
            "processed": 0,
            "succeeded": 0,
            "rate_limited": 0,
            "failed": 0,
            "skipped_no_slots": 0,
        }

        # Check circuit breaker
        self._check_circuit_breaker()

        # Calculate available slots
        available_slots = self.slot_calculator.get_processing_window_size()
        print(f"Available processing slots: {available_slots}")

        if available_slots == 0:
            slot_status = self.slot_calculator.calculate_available_slots()
            print(f"No slots available. Next slot at: {slot_status.next_slot_available_at}")
            return stats

        # Get issues ready for processing
        issues = self.db.get_issues_ready_for_processing(limit=available_slots)

        if not issues:
            print("No issues ready for processing")
            return stats

        print(f"Processing {len(issues)} issue(s)...")

        for issue_data in issues:
            try:
                result = self._process_issue(issue_data)
                stats["processed"] += 1

                if result == "success":
                    stats["succeeded"] += 1
                    self.consecutive_errors = 0  # Reset on success
                elif result == "rate_limited":
                    stats["rate_limited"] += 1
                    self.consecutive_errors = 0  # Rate limits are expected, not errors
                else:
                    stats["failed"] += 1

            except Exception as e:
                stats["failed"] += 1
                self.consecutive_errors += 1
                self.db.log_error(
                    error_type="processing_error",
                    error_message=str(e),
                    repo_name=issue_data["repo_name"],
                    issue_number=issue_data["issue_number"],
                )
                print(f"Error processing issue: {e}")

                # Check circuit breaker after each error
                try:
                    self._check_circuit_breaker()
                except CircuitBreakerError:
                    print("Circuit breaker tripped. Stopping processing.")
                    raise

        return stats

    def _process_issue(self, issue_data: dict[str, Any]) -> str:
        """Process a single issue by toggling assignment.

        Args:
            issue_data: Issue data from database

        Returns:
            Result status: 'success', 'rate_limited', or 'failed'
        """
        repo_name = issue_data["repo_name"]
        issue_number = issue_data["issue_number"]
        retry_count = issue_data["retry_count"]

        print(f"Processing {repo_name}#{issue_number} (retry {retry_count}/{self.MAX_RETRIES})")

        # Check if max retries exceeded
        if retry_count >= self.MAX_RETRIES:
            print(f"  Max retries exceeded. Removing from queue.")
            self.db.remove_issue(repo_name, issue_number)
            self.db.log_error(
                error_type="max_retries",
                error_message=f"Max retries ({self.MAX_RETRIES}) exceeded",
                repo_name=repo_name,
                issue_number=issue_number,
            )
            return "failed"

        try:
            # Get the issue
            repo = self.github.get_repo(repo_name)
            issue = repo.get_issue(issue_number)

            # Toggle assignment to trigger re-analysis
            self._toggle_assignment(issue)

            # Wait a moment for Traycer to process
            time.sleep(2)

            # Check if rate limit was resolved
            result = self._check_processing_result(issue)

            if result == "success":
                # Remove from queue
                self.db.remove_issue(repo_name, issue_number)
                self.db.log_processing(repo_name, issue_number, success=True)
                print(f"  ✓ Successfully re-analyzed")
                return "success"

            elif result == "rate_limited":
                # Still rate limited, update retry info
                rate_limit_info = IssueScanner.RATE_LIMIT_PATTERN.search(
                    self._get_latest_traycer_comment(issue)
                )
                if rate_limit_info:
                    seconds = int(rate_limit_info.group(1))
                    # Calculate new retry time from NOW + rate limit seconds + buffer
                    from datetime import datetime, timedelta
                    from .scanner import IssueScanner as Scanner

                    next_retry = datetime.now() + timedelta(
                        seconds=seconds, minutes=Scanner.RETRY_BUFFER_MINUTES
                    )

                    self.db.log_processing(
                        repo_name, issue_number, success=False, rate_limit_seconds=seconds
                    )
                    self.db.increment_retry_count(
                        repo_name, issue_number, "Still rate limited", next_retry_at=next_retry
                    )
                    print(f"  ⚠ Still rate limited ({seconds}s), retry at {next_retry.isoformat()}")
                    return "rate_limited"

            else:
                # Unknown result
                self.db.increment_retry_count(repo_name, issue_number, "Unknown result")
                return "failed"

        except GithubException as e:
            error_msg = f"GitHub API error: {e.status} - {e.data.get('message', str(e))}"
            self.db.increment_retry_count(repo_name, issue_number, error_msg)
            print(f"  ✗ {error_msg}")
            return "failed"

    def _toggle_assignment(self, issue: Issue) -> None:
        """Toggle issue assignment to trigger Traycer re-analysis.

        Strategy:
        - If user is assigned: unassign then re-assign
        - If user is not assigned: assign user

        Args:
            issue: GitHub issue object
        """
        # TODO: Implement assignment toggle logic
        # This is a key decision point with trade-offs:
        #
        # Option 1: Always unassign then reassign
        #   Pro: Consistent behavior, clear trigger
        #   Con: Creates extra events, might look spammy
        #
        # Option 2: Check current state and toggle accordingly
        #   Pro: Minimal events, cleaner history
        #   Con: More complex logic, harder to debug
        #
        # Option 3: Use a different trigger (label, comment, etc.)
        #   Pro: Doesn't affect assignment
        #   Con: Requires testing what actually triggers Traycer

        assignees = [assignee.login for assignee in issue.assignees]

        if self.username in assignees:
            # User is assigned, unassign then reassign
            issue.remove_from_assignees(self.username)
            time.sleep(0.5)  # Brief pause
            issue.add_to_assignees(self.username)
        else:
            # User not assigned, just assign
            issue.add_to_assignees(self.username)

    def _check_processing_result(self, issue: Issue) -> str:
        """Check if issue was successfully re-analyzed or still rate limited.

        Args:
            issue: GitHub issue object

        Returns:
            'success', 'rate_limited', or 'unknown'
        """
        latest_comment = self._get_latest_traycer_comment(issue)

        if not latest_comment:
            return "unknown"

        # Check for rate limit message
        if self.RATE_LIMIT_PATTERN.search(latest_comment):
            return "rate_limited"

        # If we have a Traycer comment without rate limit, assume success
        # (Traycer posted analysis instead of rate limit error)
        return "success"

    def _get_latest_traycer_comment(self, issue: Issue) -> str:
        """Get the most recent comment from Traycer bot.

        Args:
            issue: GitHub issue object

        Returns:
            Comment body or empty string if not found
        """
        comments = list(issue.get_comments())
        comments.reverse()  # Most recent first

        for comment in comments:
            if comment.user.login == IssueScanner.TRAYCER_BOT_LOGIN:
                return comment.body

        return ""

    def _check_circuit_breaker(self) -> None:
        """Check if circuit breaker should trip due to consecutive errors.

        Raises:
            CircuitBreakerError: If consecutive errors exceed threshold
        """
        recent_errors = self.db.get_consecutive_errors(limit=self.CIRCUIT_BREAKER_THRESHOLD)

        if len(recent_errors) >= self.CIRCUIT_BREAKER_THRESHOLD:
            # Check if all errors are recent (within last 5 minutes)
            now = datetime.now()
            all_recent = all(
                (now - datetime.fromisoformat(err["timestamp"])).total_seconds() < 300
                for err in recent_errors
            )

            if all_recent:
                error_msg = (
                    f"Circuit breaker tripped: {len(recent_errors)} consecutive errors "
                    f"in last 5 minutes"
                )
                self.db.log_error(error_type="circuit_breaker", error_message=error_msg)
                raise CircuitBreakerError(error_msg)


def main() -> None:
    """Main entry point for processor script."""
    import os
    import sys

    # Get GitHub token and username from environment
    github_token = os.getenv("GITHUB_TOKEN")
    github_username = os.getenv("GITHUB_USERNAME")

    if not github_token:
        print("Error: GITHUB_TOKEN environment variable not set", file=sys.stderr)
        sys.exit(1)

    if not github_username:
        print("Error: GITHUB_USERNAME environment variable not set", file=sys.stderr)
        sys.exit(1)

    # Initialize database and processor
    db = Database()
    processor = QueueProcessor(github_token, github_username, db)

    # Process queue
    print("Processing queued issues...")
    try:
        stats = processor.process_queue()
        print(f"\nProcessing complete:")
        print(f"  Processed: {stats['processed']}")
        print(f"  Succeeded: {stats['succeeded']}")
        print(f"  Rate limited: {stats['rate_limited']}")
        print(f"  Failed: {stats['failed']}")
    except CircuitBreakerError as e:
        print(f"\nCircuit breaker tripped: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
