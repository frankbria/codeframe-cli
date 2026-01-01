# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Traycer Queue Manager** - Automated queue management for GitHub issue re-analysis with Traycer AI rate limiting.

Traycer AI has rate limits (15 slots, 1 recharges every 30 minutes). This tool automatically:
1. Scans GitHub repos for rate-limited issues
2. Queues them with intelligent timing
3. Processes the queue by toggling issue assignment (triggers Traycer)
4. Infers available slots from processing history

## Development Commands

### Environment Setup
```bash
# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Create required directories
mkdir -p logs

# Configure environment
cp .env.example .env
# Edit .env to add GITHUB_TOKEN and GITHUB_USERNAME
```

### Running the System

**Scanner** (finds rate-limited issues):
```bash
source .venv/bin/activate
export $(cat .env | xargs)
python -m traycer_queue.scanner
```

**Processor** (processes queue):
```bash
source .venv/bin/activate
export $(cat .env | xargs)
python -m traycer_queue.processor
```

### Testing
```bash
# Run all tests with coverage
pytest

# Run specific test file
pytest tests/test_database.py

# Run with verbose output
pytest -v

# Coverage report
pytest --cov=src --cov-report=term-missing
```

### Code Quality
```bash
# Check code style
ruff check src/

# Format code
ruff format src/

# Check and format
ruff check src/ && ruff format src/
```

### Database Inspection
```bash
# Open database
sqlite3 traycer_queue.db

# Check queued issues
sqlite3 traycer_queue.db "SELECT repo_name, issue_number, next_retry_at FROM queued_issues;"

# Check processing history
sqlite3 traycer_queue.db "SELECT * FROM processing_history ORDER BY processed_at DESC LIMIT 10;"

# Check error log
sqlite3 traycer_queue.db "SELECT * FROM error_log ORDER BY timestamp DESC LIMIT 10;"
```

## Architecture

### Core Components

**Scanner (`src/traycer_queue/scanner.py`)**
- Scans all user-owned GitHub repositories for Traycer rate limit comments
- Pattern matching: `Rate limit exceeded. Please try after (\d+) seconds.`
- Calculates retry timing with buffer (comment timestamp + rate limit seconds + 2 min buffer)
- Uses PyGithub to interact with GitHub API

**Processor (`src/traycer_queue/processor.py`)**
- Processes queued issues by toggling GitHub issue assignment
- Assignment toggle strategy (line 196): Has TODO for implementing assignment logic with trade-offs
- Circuit breaker: Stops after 5 consecutive errors within 5 minutes
- Max retries: 3 attempts per issue before removal from queue
- Waits 2 seconds after assignment toggle for Traycer to process

**Slot Calculator (`src/traycer_queue/slot_calculator.py`)**
- Infers available processing slots from recent history
- Rate limit model: 15 total slots, 1 recharges every 30 minutes
- Strategy: Count processing attempts in last 30 minutes (consumed slots)
- Available slots = 15 - consumed slots in last 30 min
- Contains TODOs for slot calculation refinements (lines 47-58, 84-92)

**Database (`src/traycer_queue/database.py`)**
- SQLite with three tables:
  - `queued_issues`: Issues awaiting re-analysis
  - `processing_history`: All attempts (for slot calculation)
  - `error_log`: Error tracking and circuit breaker
- Context manager pattern for connection handling
- Row factory enabled for dict-like access

### Key Design Patterns

**Slot Inference Strategy**: The system doesn't have direct access to Traycer's rate limit API, so it infers available capacity by tracking processing history. Each processing attempt consumes a slot for 30 minutes. This is a constraint-based approach that works around API limitations.

**Assignment Toggle Triggering**: Traycer AI re-analyzes issues when they're assigned. The processor toggles assignment state (assign if unassigned, unassign→reassign if already assigned) to trigger re-analysis. See `processor.py:186-221` for implementation details.

**Circuit Breaker Pattern**: Prevents API abuse by stopping processing after 5 consecutive errors within 5 minutes. Rate limit errors don't trip the breaker (they're expected behavior).

### Configuration Constants

**Rate Limit Timing** (`src/traycer_queue/slot_calculator.py`):
- `TOTAL_SLOTS = 15`
- `SLOT_RECHARGE_MINUTES = 30`

**Retry Behavior** (`src/traycer_queue/scanner.py`):
- `RETRY_BUFFER_MINUTES = 2` (buffer added to rate limit timing)

**Error Handling** (`src/traycer_queue/processor.py`):
- `MAX_RETRIES = 3` (per-issue retry limit)
- `CIRCUIT_BREAKER_THRESHOLD = 5` (consecutive errors before stopping)

### Environment Variables

Required in `.env`:
- `GITHUB_TOKEN`: Personal access token with `repo` scope
- `GITHUB_USERNAME`: GitHub username for issue assignment

## Automation

The system is designed to run via cron jobs (see `crontab.example`):
- **Scanner**: Daily at 2 AM - finds new rate-limited issues
- **Processor**: Every 32 minutes - processes queue respecting available slots

Installation scripts:
- `install_crontab.sh`: Installs cron jobs from template
- `run_scanner.sh`: Wrapper script for scanner with logging
- `run_processor.sh`: Wrapper script for processor with logging

Logs written to `logs/scanner.log` and `logs/processor.log`.

## TODOs and Design Decisions

The codebase has intentional TODOs representing design trade-offs:

**Assignment Toggle Logic** (`processor.py:196-210`):
- Current: Implemented (unassign→reassign if assigned, assign if not)
- Alternatives: Always unassign/reassign, use labels/comments as trigger
- Trade-offs: Event spam vs. simplicity vs. trigger reliability

**Slot Calculation Refinements** (`slot_calculator.py:47-92`):
- Current: Simple count of attempts in last 30 minutes
- Improvements: Parse rate_limit_seconds for validation, handle clock skew, dedupe multiple attempts on same issue
- Trade-offs: Accuracy vs. complexity vs. robustness

When modifying these areas, consider the documented trade-offs and test with real GitHub API interactions.

## Recent Bug Fixes (2026-01-01)

**Circuit Breaker False Positives**:
- **Issue**: Circuit breaker was treating `max_retries` cleanup as real failures
- **Fix**: Updated `database.py:270` to exclude `max_retries` and `circuit_breaker` from consecutive error checks
- **Impact**: Processor can now run without false circuit breaker trips

**Retry Timing Not Updated**:
- **Issue**: When issues were still rate-limited after processing, `next_retry_at` wasn't updated, causing immediate retries
- **Fix**: Modified `increment_retry_count()` to accept `next_retry_at` parameter and updated processor to calculate new retry time from current time + rate limit seconds
- **Impact**: Issues now properly wait for rate limits to expire before retrying
