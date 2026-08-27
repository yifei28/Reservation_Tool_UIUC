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
- **Automatic Session Refresh** - Keeps every stored account alive and persists renewed cookies

## Usage

### Web Interface (Recommended)

```bash
python3 web_ui.py
```

Open **http://localhost:5001** and use:
- **Book Now** tab - Select facility, date, and time to book immediately
- **Schedule Booking** tab - Schedule for 72 hours before your desired time
- **My Scheduled Bookings** tab - View and cancel scheduled bookings

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

To execute scheduled bookings automatically:

```bash
python3 scheduler_daemon.py
```

Keep this running in the background (use `screen` or `tmux`).

## Supported Facilities

- **ARC Courts**: ARC_MP1, ARC_MP2, ARC_MP4, ARC_MP5
- **ARC Pickleball**: ARC_PICKLEBALL_BADMINTON (8 courts)
- **ARC Other**: ARC_GYM_2_VOLLEYBALL_COURTS, ARC_RACQUETBALL_TABLE_TENNIS, ARC_SQUASH_COURTS
- **CRCE Courts**: CRCE_MP1, CRCE_MP2, CRCE_RACQUETBALL, CRCE_SQUASH_RB_MP_COURT
- **Ice Arena**: ICE_ARENA_FREESTYLE_SKATING

## How It Works

1. **Account Login** - Log in to each account once; cookies are stored separately under `.accounts/`
2. **Account Leasing** - Before execution, reserve one valid account unused for the target date
3. **Fast HTTP Booking** - Direct POST requests to `/booking/reserve` API endpoint
4. **Scheduling** - Calculate the 72-hour window and save to `bookings_schedule.json`
5. **Auto-Execution** - Warm each account connection early and release simultaneous requests concurrently

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
- Verify time format matches (e.g., "11 AM - 12 PM")
- Slot might already be booked

## Important Notes

- Slots open **exactly 72 hours** before the time slot starts
- You can only book **one slot per day** per facility type
- Sessions are refreshed automatically while valid; fully expired Microsoft sessions require re-login
- A successfully used account is excluded only for that reservation's target date
- Keep the scheduler daemon running for automated bookings

## Files

- `web_ui.py` - Web interface (Flask server)
- `main.py` - CLI interface
- `scheduler_daemon.py` - Background scheduler
- `extract_cookies.py` - Cookie extraction script
- `src/booking_http.py` - Fast HTTP booking client
- `src/scheduler.py` - Scheduler logic
- `.accounts/` - Account registry and separate cookie sessions (gitignored)
- `bookings_schedule.json` - Scheduled bookings database

## License

MIT
