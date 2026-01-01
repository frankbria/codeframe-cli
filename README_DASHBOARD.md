# Traycer Queue Dashboard

Quick TUI dashboard for monitoring the Traycer queue system.

## Usage

### Static Snapshot
```bash
./dashboard.sh
```

### Live Updates (auto-refresh every 5 seconds)
```bash
./dashboard.sh --live
```

### Custom Refresh Rate
```bash
./dashboard.sh --live --refresh 10  # Refresh every 10 seconds
```

### Using Python Directly
```bash
source .venv/bin/activate
python -m traycer_queue.dashboard [--live] [--refresh SECONDS]
```

## Dashboard Sections

**Queue Status** (top-left):
- Total issues queued
- Issues ready for immediate processing
- Issues with retry attempts
- Available Traycer slots (X/15)

**Top Repositories** (bottom-left):
- Top 10 repositories by queued issue count
- Number of issues with retries per repo

**Recent Activity** (top-right):
- Last 10 processing attempts in the past hour
- Status: ✓ Success or ⚠ Rate Limited

**Recent Errors** (bottom-right):
- Last 5 errors (excluding rate limits, max retries, circuit breaker)
- Shows actual system errors requiring attention

## Future Enhancements

Planned additions for the TUI dashboard:
- Interactive navigation between views
- Detailed per-repository drill-down
- Error log viewer with filtering
- Configuration editor
- Manual queue management (add/remove issues)
- Processing history timeline
- Export data to CSV/JSON
