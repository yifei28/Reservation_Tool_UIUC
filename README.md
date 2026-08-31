# Active Illini Facility Booking Automation

Automated booking system for UIUC Active Illini facilities. Books slots exactly when they become available (72 hours in advance) using fast HTTP requests (~125ms).

## Quick Start

```bash
# 1. Setup
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# 2. Add one or more accounts (repeat for each account)
python3 extract_cookies.py --label "Account 1"
python3 extract_cookies.py --label "Account 2"

# 3. Start web UI
python3 web_ui.py
# Open http://localhost:5001
```

## Features

- **Web UI** - Easy-to-use interface at http://localhost:5001
- **Book Now** - Book available slots immediately
- **Schedule Booking** - Auto-book when slots open (72 hours before)
- **Fast** - Direct HTTP requests (~125ms vs 10+ seconds with browser)
- **Background Daemon** - Runs continuously to execute scheduled bookings
- **Multiple Accounts** - Rotates valid accounts so each account is used at most once per target date
- **Multiple Courts** - Schedule several courts for the same time with one assigned account per attempt
- **Automatic Session Refresh** - Keeps every stored account alive and persists renewed cookies
- **Court Confirmation** - Shows the exact court and account used after a successful reservation
- **Concurrent Execution** - Prepared workers submit simultaneous attempts at the release time

## Usage

### Web Interface (Recommended)

```bash
python3 web_ui.py
```

Open **http://localhost:5001** and use:
- **Book Now** tab - Select facility, date, and time to book immediately
- **Schedule Booking** tab - Select a court quantity and schedule for 72 hours before your desired time
- **My Scheduled Bookings** tab - View assigned accounts, results, and cancel one attempt or a complete batch

When a schedule is created, each attempt is immediately assigned a distinct
credential. The request is all-or-nothing: scheduling two courts requires two
valid accounts that are unused and unassigned for the target date.

### Command Line

```bash
# Book immediately
python3 main.py book ARC_MP1 2025-10-20 "11 AM - 12 PM"

# Schedule for later (executes 72 hours before)
python3 main.py schedule ARC_MP1 2025-10-23 "11 AM - 12 PM"

# List scheduled bookings
python3 main.py list

# Cancel a booking
python3 main.py cancel 0
```

### Run Scheduler Daemon

The web server automatically starts or replaces the scheduler daemon, so no
second command is needed when using `web_ui.py`.

For headless use, run the daemon directly:

```bash
python3 run_scheduler.py
```

Keep this running in the background (use `screen` or `tmux`).

## Supported Facilities

- **ARC Courts**: ARC_MP1, ARC_MP2, ARC_MP4, ARC_MP5
- **ARC Table Tennis**: ARC_MP3_TABLE_TENNIS_ONLY
- **ARC Pickleball**: ARC_PICKLEBALL_BADMINTON (8 courts)
- **ARC Other**: ARC_GYM_2_VOLLEYBALL_COURTS, ARC_RACQUETBALL_TABLE_TENNIS, ARC_REFLECTION_RECOVERY_ROOM, ARC_SQUASH_COURTS
- **CRCE Courts**: CRCE_MP1, CRCE_MP2, CRCE_RACQUETBALL, CRCE_SQUASH_RB_MP_COURT
- **Ice Arena**: ICE_ARENA_FREESTYLE_SKATING

## How It Works

1. **Account Login** - Log in to each account once; cookies are stored separately under `.accounts/`
2. **Schedule-Time Assignment** - Atomically assign one distinct credential to every requested court
3. **Pre-Deadline Preparation** - About 60 seconds before execution, validate assigned sessions, warm connections, and select different preferred courts
4. **Concurrent Booking** - Prepared worker threads submit requests simultaneously at the release time
5. **Fallback and Results** - Each worker tries its preferred court first, falls back to other courts, and records the exact successful court and account

Credential selection, session validation, court discovery, and worker creation
happen before the exact deadline. The critical execution path contains only the
slot lookup and reservation request.

## Troubleshooting

**"No account sessions found"**
```bash
python3 extract_cookies.py --label "Account 1"
```

**"Account session expired"**
```bash
python3 extract_cookies.py --label "Account 1" --account-id ACCOUNT_ID
```

**"Slot not available"**
- Check the date is within 72 hours
- Slot might already be booked

**"Requested N bookings, but only M accounts are available"**
- Add or re-login additional accounts
- Cancel another pending booking for the same target date to release its assignment
- Reduce the requested number of courts

## Important Notes

- Slots open **exactly 72 hours** before the time slot starts
- Each account is used for at most **one successful reservation per target date**
- Pending schedules reserve their assigned accounts until execution or cancellation
- Failed attempts release their accounts; successful attempts mark them used for that date
- An account assigned to a pending booking cannot be removed until that booking is cancelled
- Sessions are refreshed automatically while valid; fully expired Microsoft sessions require re-login
- A successfully used account is excluded only for that reservation's target date
- Keep either the web server or the standalone scheduler daemon running for automated bookings

## Files

- `web_ui.py` - Web interface (Flask server)
- `main.py` - CLI interface
- `run_scheduler.py` - Scheduler daemon used by the web server
- `scheduler_daemon.py` - Standalone scheduler with additional CLI options
- `extract_cookies.py` - Cookie extraction script
- `src/booking_http.py` - Fast HTTP booking client
- `src/scheduler.py` - Scheduler logic
- `.accounts/` - Account registry and separate cookie sessions (gitignored)
- `bookings_schedule.json` - Scheduled bookings database

## License

MIT
