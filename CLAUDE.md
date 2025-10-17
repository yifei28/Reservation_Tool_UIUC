# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Active Illini Facility Booking Automation - automated booking system for UIUC Active Illini facility reservations. Books slots exactly when they become available (72 hours in advance) using direct HTTP POST requests for maximum speed (~125ms vs 10+ seconds with browser automation).

## Quick Start

```bash
# Setup environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium  # Only needed for cookie extraction

# Extract authentication cookies (do this first!)
python3 extract_cookies.py

# Start web UI (recommended)
python3 web_ui.py  # Opens at http://localhost:5001

# Or use CLI
python3 main.py book ARC_MP1 2025-10-20 "11 AM - 12 PM"
python3 main.py schedule ARC_MP1 2025-10-23 "11 AM - 12 PM"
python3 main.py list
python3 scheduler_daemon.py  # Run scheduler daemon for automated bookings
```

## Architecture

### Core Components

**FastBookingClient** (`src/booking_http.py`)
- Primary booking engine using direct HTTP POST requests
- Handles multi-court facilities (e.g., 8 pickleball courts) with random selection to reduce contention
- **Performance optimizations**:
  - Connection pooling and warming
  - DNS pre-resolution
  - Preparation phase (call `prepare_booking()` 5-10s before execution)
  - Caches facility IDs for single-court facilities
- **Multi-court logic**: Aggregates slot availability across all courts, randomly selects initial court, falls back to others
- Session cookie authentication from pickled `.session` file

**BookingScheduler** (`src/scheduler.py`)
- Schedules bookings to execute exactly 72 hours before target time
- Automatic cookie reload detection (checks file modification time every 5 minutes)
- Manual reload via signal file (`.reload_cookies_signal`)
- Saves state to `bookings_schedule.json`
- Pre-warms connection 10 seconds before execution

**Web UI** (`web_ui.py`)
- Flask server on port 5001
- Three main features:
  1. **Book Now**: Check availability and book immediately
  2. **Schedule Booking**: Schedule for 72 hours before OR custom time
  3. **My Scheduled Bookings**: View/cancel scheduled bookings (newest first, max 15)
- Integrated cookie extraction with auto-detection (Playwright)
- Manages scheduler daemon process lifecycle

### Data Flow

1. **Cookie Extraction**: Playwright browser → Manual login → Extract cookies → Save to `.session` (pickled dict)
2. **Immediate Booking**: Web UI/CLI → FastBookingClient → Check slots → Submit POST to `/booking/reserve` → Success/Failure
3. **Scheduled Booking**: Web UI/CLI → BookingScheduler → Save to `bookings_schedule.json` → Scheduler daemon wakes up → Pre-warm → Execute at exact time
4. **Multi-Court Handling**: Get all facility IDs → Random selection → Try initial court → Fallback to remaining courts if needed

## Critical Implementation Details

### Time Slot Parsing (IMPORTANT!)

When scheduling bookings, time slot strings like "6 - 7 PM" must be parsed correctly:

```python
# web_ui.py lines 199-208
parts = slot_time.split()
hour = parts[0]
am_pm = 'PM' if 'PM' in slot_time else 'AM'
time_str = f"{hour} {am_pm}"
target_date = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %I %p")
```

**DO NOT** use `%I` without `%p` - this defaults to AM and causes PM bookings to be scheduled 12 hours early!

### Multi-Court Slot Aggregation

For facilities with multiple courts (e.g., `ARC_PICKLEBALL_BADMINTON` has 8 courts):

- `check_available_slots()` aggregates across all courts
- Returns slots showing "5 of 8 courts available" instead of just "unavailable"
- `book_slot()` uses random court selection to reduce contention
- Specific court ID can be provided to skip multi-court logic (faster)

### Session Management

- `.session` file contains pickled dict: `{'cookies': {...}, 'authenticated': True, 'auth_time': timestamp}`
- Cookies expire after some time - re-run `extract_cookies.py` if auth errors occur
- Scheduler daemon auto-reloads if `.session` file modified
- Manual reload: create `.reload_cookies_signal` file or use Web UI "Reload Cookies" button

### Booking History Display

- `/api/scheduled-bookings` endpoint returns 15 most recent bookings sorted newest-to-oldest
- **Preserves original indices** for cancel functionality (important!)
- Sorts by `execute_at` timestamp in descending order

## Environment Variables

All services support configuration via environment variables:

- `SESSION_FILE` - Path to session cookies (default: `.session`)
- `SCHEDULE_FILE` - Path to bookings database (default: `bookings_schedule.json`)
- `LOG_FILE` - Path to scheduler log (default: `scheduler.log`)
- `PID_FILE` - Path to scheduler PID file (default: `.scheduler.pid`)
- `RELOAD_SIGNAL_FILE` - Path to reload signal (default: `.reload_cookies_signal`)

## File Reference

### Core Source Files
- `src/booking_http.py` - FastBookingClient (HTTP-based, primary)
- `src/scheduler.py` - BookingScheduler (manages scheduled bookings)
- `src/booking.py` - Legacy Playwright-based client (not used)
- `src/auth.py` - SessionManager (legacy, cookie extraction is manual now)
- `src/config.py` - Configuration utilities

### Entry Points
- `web_ui.py` - Flask web server (port 5001)
- `main.py` - CLI interface (book, schedule, list, cancel commands)
- `scheduler_daemon.py` - Background scheduler daemon
- `extract_cookies.py` - Playwright-based cookie extraction

### Data Files
- `.session` - Pickled authentication cookies (**DO NOT commit**)
- `bookings_schedule.json` - Scheduled bookings database
- `.scheduler.pid` - Scheduler daemon PID (for process management)
- `.reload_cookies_signal` - Signal file for manual cookie reload
- `scheduler.log` - Scheduler daemon log output

### Templates
- `templates/index.html` - Web UI frontend (18 time slots from 6 AM - 12 AM)

## Known Facilities

All facilities are defined in `FastBookingClient.FACILITIES` dict with their `product_id` and optional cached `facility_id`:

- `ARC_MP1`, `ARC_MP2`, `ARC_MP4`, `ARC_MP5` - Multi-purpose courts
- `ARC_PICKLEBALL_BADMINTON` - 8 courts (multi-court)
- `ARC_GYM_2_VOLLEYBALL_COURTS` - Volleyball
- `ARC_RACQUETBALL_TABLE_TENNIS` - Racquetball
- `ARC_SQUASH_COURTS` - Squash
- `ARC_MP3_TABLE_TENNIS_ONLY` - Table tennis only
- `ARC_REFLECTION_RECOVERY_ROOM` - Recovery room
- `CRCE_MP1`, `CRCE_MP2` - CRCE multi-purpose courts
- `CRCE_RACQUETBALL` - CRCE racquetball
- `CRCE_SQUASH_RB_MP_COURT` - CRCE squash
- `ICE_ARENA_FREESTYLE_SKATING` - Ice skating

## Common Tasks

### Testing Booking Logic Without Actually Booking

```bash
# Dry run mode
python3 main.py book ARC_MP1 2025-10-20 "11 AM - 12 PM" --dry-run

# Or in code
client = FastBookingClient()
success = client.book_slot(facility="ARC_MP1", date=date, slot_time="11 AM - 12 PM", dry_run=True)
```

### Adding a New Facility

1. Find product ID from Active Illini booking page URL: `/booking/{product_id}/facilities`
2. Add to `FastBookingClient.FACILITIES` dict in `src/booking_http.py`
3. Add to facilities list in `web_ui.py` `/api/facilities` endpoint
4. Facility ID will be auto-detected on first use

### Debugging Booking Failures

1. Check `scheduler.log` for daemon execution logs
2. Check `bookings_schedule.json` for booking status and error messages
3. Enable verbose logging: `python3 main.py book ... --verbose` or `python3 scheduler_daemon.py --verbose`
4. Verify cookies are valid: check `.session` file modification time, re-run `extract_cookies.py` if old
5. Check response details in logs (HTTP status, JSON response, error codes)

### Modifying Time Slot Options (Web UI)

Edit `templates/index.html` lines 410-432 to add/remove time slot options in the dropdown. Ensure format matches Active Illini's slot text exactly (e.g., "11 AM - 12 PM").

### Restarting Flask Server

The Flask server runs in debug mode with auto-reload, but if changes don't appear:

```bash
# Kill existing processes
pkill -f "python3 web_ui.py"
lsof -ti:5001 | xargs kill -9

# Restart
python3 web_ui.py
```

### Managing Scheduler Daemon

```bash
# Check if running
cat .scheduler.pid
ps aux | grep scheduler_daemon

# Start daemon
python3 scheduler_daemon.py

# Stop daemon
kill $(cat .scheduler.pid)

# Run once (non-daemon mode)
python3 scheduler_daemon.py --once
```

## Important Constraints

### Campus Recreation Policies

- **ONE reservation per day per specific court type** (can't book ARC_MP1 at 10 AM AND 11 AM)
- CAN book different court types on same day (ARC_MP1 + ARC_PICKLEBALL on same day is OK)
- 1 hour maximum per reservation
- 72-hour advance booking window (slots open exactly 72 hours before slot start time)
- Must show up within 10 minutes or forfeit

**Note**: Current implementation does NOT enforce the one-per-day policy.

## Task Master AI Instructions

**Import Task Master's development workflow commands and guidelines, treat as if import is in the main CLAUDE.md file.**
@./.taskmaster/CLAUDE.md
