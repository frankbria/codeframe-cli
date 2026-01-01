"""TUI Dashboard for Traycer Queue Manager.

Displays real-time status of the queue, processing history, and slot availability.
"""

from datetime import datetime
from typing import Any

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .database import Database
from .slot_calculator import SlotCalculator


class QueueDashboard:
    """Interactive dashboard for monitoring the Traycer queue system."""

    def __init__(self, db: Database):
        """Initialize dashboard with database connection.

        Args:
            db: Database instance
        """
        self.db = db
        self.slot_calculator = SlotCalculator(db)
        self.console = Console()

    def create_layout(self) -> Layout:
        """Create the dashboard layout.

        Returns:
            Rich Layout object with all panels
        """
        layout = Layout()

        # Split into header and body
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
        )

        # Split body into left and right columns
        layout["body"].split_row(
            Layout(name="left"),
            Layout(name="right"),
        )

        # Split left column into queue stats and repo breakdown
        layout["left"].split_column(
            Layout(name="queue_stats", ratio=1),
            Layout(name="repo_breakdown", ratio=2),
        )

        # Split right column into recent activity and errors
        layout["right"].split_column(
            Layout(name="recent_activity", ratio=2),
            Layout(name="errors", ratio=1),
        )

        return layout

    def render_header(self) -> Panel:
        """Render the dashboard header.

        Returns:
            Panel with header content
        """
        title = Text("Traycer Queue Manager Dashboard", style="bold cyan")
        subtitle = Text(f"Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", style="dim")
        header_text = Text.assemble(title, "\n", subtitle)

        return Panel(header_text, style="white on blue")

    def render_queue_stats(self) -> Panel:
        """Render queue statistics panel.

        Returns:
            Panel with queue stats
        """
        with self.db._get_connection() as conn:
            cursor = conn.cursor()

            # Total queued issues
            cursor.execute("SELECT COUNT(*) FROM queued_issues")
            total_queued = cursor.fetchone()[0]

            # Issues ready for processing
            cursor.execute(
                "SELECT COUNT(*) FROM queued_issues WHERE next_retry_at IS NULL OR next_retry_at <= ?",
                (datetime.now(),),
            )
            ready_now = cursor.fetchone()[0]

            # Issues with retries
            cursor.execute("SELECT COUNT(*) FROM queued_issues WHERE retry_count > 0")
            with_retries = cursor.fetchone()[0]

        # Get slot availability
        slot_status = self.slot_calculator.calculate_available_slots()

        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("Metric", style="cyan")
        table.add_column("Value", justify="right", style="green bold")

        table.add_row("Total Queued", str(total_queued))
        table.add_row("Ready Now", str(ready_now))
        table.add_row("With Retries", str(with_retries))
        table.add_row("", "")  # Spacer
        table.add_row(
            "Available Slots", f"{slot_status.available_slots}/{slot_status.total_slots}"
        )

        return Panel(table, title="[bold]Queue Status", border_style="blue")

    def render_repo_breakdown(self) -> Panel:
        """Render repository breakdown table.

        Returns:
            Panel with repo breakdown
        """
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT repo_name, COUNT(*) as count,
                       SUM(CASE WHEN retry_count > 0 THEN 1 ELSE 0 END) as retries
                FROM queued_issues
                GROUP BY repo_name
                ORDER BY count DESC
                LIMIT 10
            """
            )
            repos = cursor.fetchall()

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Repository", style="dim")
        table.add_column("Issues", justify="right")
        table.add_column("Retries", justify="right", style="yellow")

        for repo in repos:
            repo_short = repo[0].split("/")[-1]  # Show just repo name, not org/repo
            table.add_row(repo_short, str(repo[1]), str(repo[2]))

        return Panel(table, title="[bold]Top Repositories", border_style="green")

    def render_recent_activity(self) -> Panel:
        """Render recent processing activity.

        Returns:
            Panel with recent activity
        """
        history = self.db.get_recent_processing_history(minutes=60)

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Time", style="dim")
        table.add_column("Repository", style="dim")
        table.add_column("#", justify="right")
        table.add_column("Status")

        for record in history[:10]:
            time_str = datetime.fromisoformat(record["processed_at"]).strftime("%H:%M:%S")
            repo_short = record["repo_name"].split("/")[-1]

            status = "✓ Success" if record["success"] else "⚠ Rate Limited"
            status_style = "green" if record["success"] else "yellow"

            table.add_row(
                time_str,
                repo_short,
                str(record["issue_number"]),
                f"[{status_style}]{status}[/]",
            )

        if not history:
            table.add_row("", "[dim]No recent activity[/]", "", "")

        return Panel(table, title="[bold]Recent Activity (Last Hour)", border_style="magenta")

    def render_errors(self) -> Panel:
        """Render recent errors.

        Returns:
            Panel with error log
        """
        errors = self.db.get_consecutive_errors(limit=5)

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Time", style="dim")
        table.add_column("Type", style="yellow")
        table.add_column("Message", style="red", no_wrap=False)

        for error in errors:
            time_str = datetime.fromisoformat(error["timestamp"]).strftime("%H:%M")
            error_type = error["error_type"]
            message = error["error_message"][:50] + "..." if len(error["error_message"]) > 50 else error["error_message"]

            table.add_row(time_str, error_type, message)

        if not errors:
            table.add_row("", "[dim green]No recent errors[/]", "")

        return Panel(table, title="[bold]Recent Errors", border_style="red")

    def render_dashboard(self) -> Layout:
        """Render the complete dashboard.

        Returns:
            Complete dashboard layout
        """
        layout = self.create_layout()

        layout["header"].update(self.render_header())
        layout["queue_stats"].update(self.render_queue_stats())
        layout["repo_breakdown"].update(self.render_repo_breakdown())
        layout["recent_activity"].update(self.render_recent_activity())
        layout["errors"].update(self.render_errors())

        return layout

    def run_static(self) -> None:
        """Display a static snapshot of the dashboard."""
        self.console.print(self.render_dashboard())

    def run_live(self, refresh_seconds: int = 5) -> None:
        """Run the dashboard with live updates.

        Args:
            refresh_seconds: Seconds between refreshes
        """
        try:
            with Live(self.render_dashboard(), console=self.console, refresh_per_second=1 / refresh_seconds) as live:
                while True:
                    import time
                    time.sleep(refresh_seconds)
                    live.update(self.render_dashboard())
        except KeyboardInterrupt:
            self.console.print("\n[dim]Dashboard closed.[/]")


def main() -> None:
    """Main entry point for dashboard script."""
    import argparse

    parser = argparse.ArgumentParser(description="Traycer Queue Manager Dashboard")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run dashboard with live updates (refresh every 5 seconds)",
    )
    parser.add_argument(
        "--refresh",
        type=int,
        default=5,
        help="Refresh interval in seconds for live mode (default: 5)",
    )

    args = parser.parse_args()

    db = Database()
    dashboard = QueueDashboard(db)

    if args.live:
        dashboard.run_live(refresh_seconds=args.refresh)
    else:
        dashboard.run_static()


if __name__ == "__main__":
    main()
