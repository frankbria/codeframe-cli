"""Repository scanner to find rate-limited Traycer AI issues."""

import re
from datetime import datetime, timedelta
from typing import NamedTuple

from github import Auth, Github
from github.Issue import Issue
from github.IssueComment import IssueComment
from github.Repository import Repository

from .database import Database


class RateLimitInfo(NamedTuple):
    """Information parsed from a Traycer rate limit message."""

    seconds: int
    comment_created_at: datetime
    message: str


class IssueScanner:
    """Scans GitHub repositories for Traycer AI rate-limited issues."""

    TRAYCER_BOT_LOGIN = "traycerai[bot]"
    # Updated pattern to handle blockquote format: "> [!WARNING]\n> Rate limit exceeded..."
    RATE_LIMIT_PATTERN = re.compile(
        r"Rate limit exceeded\.\s+Please try after (\d+) seconds\.", re.MULTILINE | re.DOTALL
    )
    RETRY_BUFFER_MINUTES = 2  # Add 2 minutes buffer to 30-minute intervals

    def __init__(self, github_token: str, db: Database):
        """Initialize scanner with GitHub token and database.

        Args:
            github_token: GitHub personal access token
            db: Database instance
        """
        auth = Auth.Token(github_token)
        self.github = Github(auth=auth)
        self.db = db
        self.user = self.github.get_user()

    def scan_all_repos(self) -> tuple[int, int]:
        """Scan all owned repositories for rate-limited issues.

        Returns:
            Tuple of (repos_scanned, issues_queued)
        """
        repos_scanned = 0
        issues_queued = 0

        for repo in self.user.get_repos():
            # Skip forks - only scan owned repos
            if repo.fork:
                continue

            # Skip repos without issues enabled
            if not repo.has_issues:
                continue

            repos_scanned += 1
            issues_queued += self._scan_repo(repo)

        return repos_scanned, issues_queued

    def _scan_repo(self, repo: Repository) -> int:
        """Scan a single repository for rate-limited issues.

        Args:
            repo: GitHub repository object

        Returns:
            Number of issues queued from this repo
        """
        issues_queued = 0

        try:
            # Get all open issues
            for issue in repo.get_issues(state="open"):
                # Skip pull requests
                if issue.pull_request:
                    continue

                rate_limit_info = self._check_for_rate_limit(issue)
                if rate_limit_info:
                    self._queue_issue(repo, issue, rate_limit_info)
                    issues_queued += 1

        except Exception as e:
            self.db.log_error(
                error_type="scan_error",
                error_message=f"Error scanning repo {repo.full_name}: {str(e)}",
                repo_name=repo.full_name,
            )

        return issues_queued

    def _check_for_rate_limit(self, issue: Issue) -> RateLimitInfo | None:
        """Check if an issue has a Traycer rate limit comment.

        Args:
            issue: GitHub issue object

        Returns:
            RateLimitInfo if rate limit found, None otherwise
        """
        try:
            # Get all comments, most recent first
            comments = list(issue.get_comments())
            comments.reverse()

            for comment in comments:
                if comment.user.login == self.TRAYCER_BOT_LOGIN:
                    match = self.RATE_LIMIT_PATTERN.search(comment.body)
                    if match:
                        seconds = int(match.group(1))
                        return RateLimitInfo(
                            seconds=seconds,
                            comment_created_at=comment.created_at,
                            message=comment.body,
                        )

        except Exception as e:
            self.db.log_error(
                error_type="comment_check_error",
                error_message=f"Error checking comments on issue #{issue.number}: {str(e)}",
                repo_name=issue.repository.full_name,
                issue_number=issue.number,
            )

        return None

    def _queue_issue(self, repo: Repository, issue: Issue, rate_limit_info: RateLimitInfo) -> None:
        """Add an issue to the queue with calculated retry time.

        Args:
            repo: Repository object
            issue: Issue object
            rate_limit_info: Parsed rate limit information
        """
        # Calculate next retry time
        # Use comment timestamp + rate limit seconds + buffer
        retry_time = rate_limit_info.comment_created_at + timedelta(
            seconds=rate_limit_info.seconds,
            minutes=self.RETRY_BUFFER_MINUTES,
        )

        # Add to queue
        added = self.db.add_issue(
            repo_name=repo.full_name, issue_number=issue.number, next_retry_at=retry_time
        )

        # Log the finding
        action = "Added" if added else "Updated"
        print(
            f"{action} {repo.full_name}#{issue.number} to queue "
            f"(retry at {retry_time.isoformat()})"
        )


def main() -> None:
    """Main entry point for scanner script."""
    import os
    import sys

    # Get GitHub token from environment
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        print("Error: GITHUB_TOKEN environment variable not set", file=sys.stderr)
        sys.exit(1)

    # Initialize database and scanner
    db = Database()
    scanner = IssueScanner(github_token, db)

    # Scan all repos
    print("Scanning repositories for rate-limited Traycer issues...")
    repos_scanned, issues_queued = scanner.scan_all_repos()

    print(f"\nScan complete:")
    print(f"  Repositories scanned: {repos_scanned}")
    print(f"  Issues queued: {issues_queued}")


if __name__ == "__main__":
    main()
