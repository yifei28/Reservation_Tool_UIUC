#!/usr/bin/env python3
"""
Web UI for Active Illini Facility Booking
Simple Flask-based interface for booking and scheduling
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for
from datetime import datetime
from pathlib import Path
import logging
import subprocess
import os
import signal
import uuid
import threading
import time

from src.scheduler import BookingScheduler
from src.account_pool import AccountPool, NoAvailableAccountError

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration from environment variables
SESSION_FILE = os.getenv('SESSION_FILE', '.session')
SCHEDULE_FILE = os.getenv('SCHEDULE_FILE', 'bookings_schedule.json')
SCHEDULER_PID_FILE = Path(os.getenv('PID_FILE', '.scheduler.pid'))
RELOAD_SIGNAL_FILE = Path(os.getenv('RELOAD_SIGNAL_FILE', '.reload_cookies_signal'))
ACCOUNTS_DIR = os.getenv('ACCOUNTS_DIR', '.accounts')

# Global dictionary to track cookie extraction sessions
extraction_sessions = {}

# Session keep-alive background thread
keep_alive_thread = None

# Initialize the shared multi-account pool. Existing .session data is imported
# once as "Default account" for backward compatibility.
try:
    account_pool = AccountPool(
        accounts_dir=ACCOUNTS_DIR,
        legacy_session_file=SESSION_FILE
    )
    scheduler = BookingScheduler(
        schedule_file=SCHEDULE_FILE,
        account_pool=account_pool,
        reload_signal_file=str(RELOAD_SIGNAL_FILE)
    )
    try:
        booking_client = account_pool.get_client()
    except NoAvailableAccountError:
        booking_client = None
except Exception as e:
    logger.warning(f"Could not initialize clients: {e}")
    account_pool = None
    booking_client = None
    scheduler = None


def get_booking_client(account_id=None):
    """Get a current pool client for non-consuming operations."""
    if not account_pool:
        raise NoAvailableAccountError('Account pool is not initialized')
    return account_pool.get_client(account_id)


def is_scheduler_running() -> bool:
    """Check if scheduler daemon process is running."""
    if not SCHEDULER_PID_FILE.exists():
        return False

    try:
        pid = int(SCHEDULER_PID_FILE.read_text().strip())

        # Check if process exists (sends signal 0, doesn't actually kill)
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, OSError):
        # PID file exists but process is dead
        SCHEDULER_PID_FILE.unlink(missing_ok=True)
        return False


def start_scheduler_process() -> bool:
    """Start scheduler daemon as a separate process."""
    try:
        # Start scheduler as background process
        process = subprocess.Popen(
            ['python3', 'run_scheduler.py'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # Detach from parent
            cwd=os.getcwd()
        )

        # Save PID to file
        SCHEDULER_PID_FILE.write_text(str(process.pid))

        logger.info(f"✅ Started scheduler daemon (PID: {process.pid})")
        return True
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")
        return False


def ensure_scheduler_running():
    """Ensure scheduler daemon is running, start if not."""
    if is_scheduler_running():
        logger.debug("Scheduler daemon already running")
        return True

    logger.info("Scheduler not running, starting it now...")
    return start_scheduler_process()


@app.route('/')
def index():
    """Main dashboard."""
    return render_template('index.html')


@app.route('/api/check-session')
def check_session():
    """Check whether at least one account session is stored."""
    account_count = account_pool.account_count() if account_pool else 0
    return jsonify({
        'has_session': account_count > 0,
        'account_count': account_count,
        'message': f'{account_count} account session(s) found' if account_count else 'No accounts - add an account'
    })


@app.route('/api/accounts')
def list_accounts():
    """List stored accounts and optional usage for a target date."""
    if not account_pool:
        return jsonify({'error': 'Account pool not initialized'}), 500
    target_date = request.args.get('date')
    return jsonify(account_pool.list_accounts(target_date))


@app.route('/api/accounts/<account_id>', methods=['DELETE'])
def remove_account(account_id):
    """Remove one stored account session."""
    if not account_pool:
        return jsonify({'error': 'Account pool not initialized'}), 500
    removed = account_pool.remove_account(account_id)
    return jsonify({
        'success': removed,
        'message': 'Account removed' if removed else 'Account not found'
    }), 200 if removed else 404


@app.route('/api/accounts/<account_id>/validate', methods=['POST'])
def validate_account(account_id):
    """Validate and persist refreshed cookies for one account."""
    if not account_pool:
        return jsonify({'error': 'Account pool not initialized'}), 500
    valid = account_pool.validate_account(account_id)
    return jsonify({'success': valid, 'valid': valid})


@app.route('/api/debug-cookie-jar')
def debug_cookie_jar():
    """Debug endpoint: dump the in-memory cookie jar."""
    try:
        client = get_booking_client(request.args.get('account_id'))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    jar_cookies = {name: value for name, value in client.session.cookies.items()}

    # Compare with .session file
    import pickle
    session_file = client.session_file
    file_cookies = {}
    if session_file.exists():
        with open(session_file, 'rb') as f:
            data = pickle.load(f)
        file_cookies = data.get('cookies', {})

    # Find extras in jar that aren't in .session file
    extras = {k: v for k, v in jar_cookies.items() if k not in file_cookies}

    return jsonify({
        'jar_count': len(jar_cookies),
        'file_count': len(file_cookies),
        'extra_count': len(extras),
        'extras': {k: v[:50] + '...' if len(v) > 50 else v for k, v in extras.items()},
        'jar_names': sorted(jar_cookies.keys()),
        'file_names': sorted(file_cookies.keys()),
    })


@app.route('/api/cookie-status')
def cookie_status():
    """Validate one account or refresh the complete account pool."""
    if not account_pool:
        return jsonify({'error': 'Account pool not initialized', 'valid': False}), 500
    account_id = request.args.get('account_id')
    if account_id:
        valid = account_pool.validate_account(account_id)
        account = next(
            (item for item in account_pool.list_accounts() if item['id'] == account_id),
            None
        )
        return jsonify({
            'valid': valid,
            'error': None if valid else (account or {}).get('last_error', 'Session expired'),
            'account': account
        })

    results = account_pool.refresh_due_accounts(force=True)
    accounts = account_pool.list_accounts()
    valid_count = sum(1 for item in accounts if item['status'] == 'valid')
    return jsonify({
        'valid': valid_count > 0,
        'valid_count': valid_count,
        'account_count': len(accounts),
        'accounts': accounts,
        'error': None if valid_count else 'No valid account sessions'
    })


@app.route('/api/facilities')
def get_facilities():
    """Get list of available facilities."""
    facilities = [
        {'id': 'ARC_GYM_2_VOLLEYBALL_COURTS', 'name': 'ARC Gym 2 Volleyball Courts'},
        {'id': 'ARC_MP1', 'name': 'ARC Multi-Purpose Court 1'},
        {'id': 'ARC_MP2', 'name': 'ARC Multi-Purpose Court 2'},
        {'id': 'ARC_MP3_TABLE_TENNIS_ONLY', 'name': 'ARC MP3 - Table Tennis ONLY'},
        {'id': 'ARC_MP4', 'name': 'ARC Multi-Purpose Court 4'},
        {'id': 'ARC_MP5', 'name': 'ARC Multi-Purpose Court 5'},
        {'id': 'ARC_PICKLEBALL_BADMINTON', 'name': 'ARC Pickleball/Badminton'},
        {'id': 'ARC_RACQUETBALL_TABLE_TENNIS', 'name': 'ARC Racquetball & Table Tennis Courts'},
        {'id': 'ARC_REFLECTION_RECOVERY_ROOM', 'name': 'ARC Reflection & Recovery Room'},
        {'id': 'ARC_SQUASH_COURTS', 'name': 'ARC Squash Courts'},
        {'id': 'CRCE_MP1', 'name': 'CRCE Multi-Purpose Court 1'},
        {'id': 'CRCE_MP2', 'name': 'CRCE Multi-Purpose Court 2'},
        {'id': 'CRCE_RACQUETBALL', 'name': 'CRCE Racquetball'},
        {'id': 'CRCE_SQUASH_RB_MP_COURT', 'name': 'CRCE Squash/RB MP Court'},
        {'id': 'ICE_ARENA_FREESTYLE_SKATING', 'name': 'Ice Arena Freestyle Skating'}
    ]
    return jsonify(facilities)


@app.route('/api/slots', methods=['POST'])
def get_slots():
    """Get available slots for a facility and date."""
    data = request.json
    facility = data.get('facility')
    date_str = data.get('date')

    try:
        client = get_booking_client()
        date = datetime.strptime(date_str, '%Y-%m-%d')
        slots = client.check_available_slots(facility, date)

        return jsonify({
            'slots': slots,
            'date': date_str,
            'facility': facility
        })
    except Exception as e:
        logger.error(f"Error getting slots: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/book', methods=['POST'])
def book_now():
    """Book a slot immediately."""
    data = request.json
    facility = data.get('facility')
    date_str = data.get('date')
    slot_time = data.get('time')

    lease = None
    try:
        date = datetime.strptime(date_str, '%Y-%m-%d')
        lease = account_pool.acquire(date, validate=True)
        lease.client.prepare_booking(facility=facility, date=date)
        success = lease.client.book_slot(
            facility=facility,
            date=date,
            slot_time=slot_time,
            dry_run=False
        )

        if success:
            account_pool.mark_used(lease)
        else:
            account_pool.release(lease)

        return jsonify({
            'success': success,
            'message': 'Booking successful!' if success else 'Booking failed',
            'account_id': lease.account_id,
            'account_label': lease.label
        })
    except NoAvailableAccountError as e:
        return jsonify({'error': str(e)}), 409
    except Exception as e:
        if lease:
            account_pool.release(lease)
        logger.error(f"Booking error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/schedule', methods=['POST'])
def schedule_booking():
    """Schedule a booking for a specific time or 72 hours before."""
    if not scheduler:
        return jsonify({'error': 'Scheduler not initialized'}), 500

    data = request.json
    facility = data.get('facility')
    date_str = data.get('date')
    slot_time = data.get('time')
    execute_datetime_str = data.get('execute_at')  # Optional: custom execution time

    try:
        # Parse the date and time
        # Extract hour and AM/PM from slot_time (e.g., "6 - 7 PM" -> "6 PM", "11 AM - 12 PM" -> "11 AM")
        parts = slot_time.split()
        hour = parts[0]
        # Get AM/PM from the start time (parts[1]) if it exists, otherwise from the end
        if len(parts) > 1 and parts[1] in ['AM', 'PM']:
            am_pm = parts[1]
        else:
            am_pm = 'PM' if 'PM' in slot_time else 'AM'
        time_str = f"{hour} {am_pm}"

        target_date = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %I %p")

        # Parse custom execution time if provided
        execute_at = None
        if execute_datetime_str:
            execute_at = datetime.strptime(execute_datetime_str, "%Y-%m-%dT%H:%M")

        # Schedule the booking
        booking = scheduler.schedule_booking(
            facility=facility,
            target_date=target_date,
            slot_time=slot_time,
            execute_at=execute_at
        )

        # Ensure scheduler daemon is running
        ensure_scheduler_running()

        return jsonify({
            'success': True,
            'message': f'Booking scheduled for {booking.execute_at.strftime("%Y-%m-%d %H:%M:%S")}',
            'execute_at': booking.execute_at.isoformat()
        })
    except Exception as e:
        logger.error(f"Scheduling error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/scheduled-bookings')
def list_scheduled():
    """Get all scheduled bookings (newest to oldest, max 15)."""
    if not scheduler:
        return jsonify({'error': 'Scheduler not initialized'}), 500

    bookings = scheduler.list_scheduled_bookings()
    now = datetime.now()

    # Sort by execute_at descending (newest first)
    sorted_bookings = sorted(
        enumerate(bookings),
        key=lambda x: x[1].execute_at,
        reverse=True
    )

    # Take only the 15 most recent
    result = []
    for original_index, booking in sorted_bookings[:15]:
        time_until = (booking.execute_at - now).total_seconds()
        result.append({
            'index': original_index,  # Keep original index for cancellation
            'facility': booking.facility,
            'target_date': booking.target_date.strftime('%Y-%m-%d %H:%M'),
            'slot_time': booking.slot_time,
            'execute_at': booking.execute_at.strftime('%Y-%m-%d %H:%M:%S'),
            'status': booking.status,
            'error': booking.error,
            'account_id': booking.account_id,
            'hours_until': time_until / 3600 if time_until > 0 else 0
        })

    return jsonify(result)


@app.route('/api/cancel-booking/<int:index>', methods=['DELETE'])
def cancel_booking(index):
    """Cancel a scheduled booking."""
    if not scheduler:
        return jsonify({'error': 'Scheduler not initialized'}), 500

    try:
        success = scheduler.cancel_booking(index)
        return jsonify({
            'success': success,
            'message': 'Booking cancelled' if success else 'Booking not found'
        })
    except Exception as e:
        logger.error(f"Cancel error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/scheduler-status')
def scheduler_status():
    """Check if scheduler daemon is running."""
    running = is_scheduler_running()

    if running and SCHEDULER_PID_FILE.exists():
        pid = int(SCHEDULER_PID_FILE.read_text().strip())
        return jsonify({
            'running': True,
            'pid': pid,
            'message': 'Scheduler daemon is running'
        })
    else:
        return jsonify({
            'running': False,
            'message': 'Scheduler daemon is not running'
        })


@app.route('/api/reload-cookies', methods=['POST'])
def reload_cookies():
    """Refresh all account sessions and signal the scheduler daemon."""
    global booking_client, scheduler

    try:
        if not account_pool:
            return jsonify({'error': 'Account pool not initialized'}), 500
        results = account_pool.refresh_due_accounts(force=True)
        try:
            booking_client = account_pool.get_client()
        except NoAvailableAccountError:
            booking_client = None

        start_keep_alive_thread()
        RELOAD_SIGNAL_FILE.touch()
        valid_count = sum(1 for valid in results.values() if valid)

        return jsonify({
            'success': valid_count > 0,
            'valid_count': valid_count,
            'account_count': account_pool.account_count(),
            'message': f'Refreshed {valid_count} valid account session(s)'
        })
    except FileNotFoundError as e:
        logger.error(f"❌ Session file not found: {e}")
        return jsonify({'error': 'No session file found - please extract cookies first'}), 400
    except Exception as e:
        logger.error(f"❌ Failed to reload cookies: {e}")
        return jsonify({'error': str(e)}), 500


def save_cookies_to_session(cookies, label, account_id=None):
    """Save Playwright cookies as one account-specific session."""
    cookie_dict = {cookie['name']: cookie['value'] for cookie in cookies}
    stored_id = account_pool.store_cookies(
        label=label,
        cookies=cookie_dict,
        account_id=account_id
    )
    logger.info("Saved %d cookies for account %s", len(cookies), label)
    return len(cookies), stored_id


def run_cookie_extraction_browser(session_id):
    """
    Background thread for cookie extraction with auto-detection.
    Opens Playwright browser and waits for login completion.
    """
    try:
        from playwright.sync_api import sync_playwright

        extraction_sessions[session_id]['status'] = 'browser_launching'

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            extraction_sessions[session_id]['status'] = 'waiting_for_login'
            extraction_sessions[session_id]['browser'] = browser
            extraction_sessions[session_id]['context'] = context
            extraction_sessions[session_id]['page'] = page

            # Navigate to booking page
            page.goto('https://active.illinois.edu/booking')
            extraction_sessions[session_id]['current_url'] = page.url

            # Auto-detection loop
            stable_url = None
            stable_count = 0
            timeout_time = time.time() + 600  # 10 minute timeout
            check_interval = 2  # Check every 2 seconds

            while time.time() < timeout_time:
                # Check if manual completion was triggered
                if extraction_sessions[session_id]['status'] == 'extracting':
                    break

                time.sleep(check_interval)

                try:
                    current_url = page.url
                    cookies = context.cookies()
                    extraction_sessions[session_id]['current_url'] = current_url
                    extraction_sessions[session_id]['cookies_count'] = len(cookies)

                    # Auto-detection: Check if logged in
                    # Heuristic: URL contains "active.illinois.edu", not on login page, has cookies
                    if ('active.illinois.edu' in current_url and
                        'login' not in current_url.lower() and
                        'shibboleth' not in current_url.lower() and
                        len(cookies) > 10):

                        # Check if URL is stable (hasn't changed)
                        if current_url == stable_url:
                            stable_count += 1

                            # If stable for 3 checks (6 seconds), auto-extract
                            if stable_count >= 3:
                                logger.info(f"Auto-detected login completion for session {session_id}")
                                extraction_sessions[session_id]['status'] = 'extracting'
                                break
                        else:
                            stable_url = current_url
                            stable_count = 0

                except Exception as e:
                    logger.error(f"Error during auto-detection: {e}")
                    continue

            # Extract cookies if status is 'extracting'
            if extraction_sessions[session_id]['status'] == 'extracting':
                try:
                    cookies = context.cookies()
                    cookies_count, account_id = save_cookies_to_session(
                        cookies,
                        extraction_sessions[session_id]['label'],
                        extraction_sessions[session_id].get('account_id')
                    )

                    extraction_sessions[session_id]['status'] = 'complete'
                    extraction_sessions[session_id]['cookies_count'] = cookies_count
                    extraction_sessions[session_id]['account_id'] = account_id
                    extraction_sessions[session_id]['message'] = f'Successfully extracted {cookies_count} cookies'

                    # Signal scheduler daemon to reload cookies
                    RELOAD_SIGNAL_FILE.touch()

                    # Keep browser open for 2 seconds so user can see success
                    time.sleep(2)
                except Exception as e:
                    logger.error(f"Error extracting cookies: {e}")
                    extraction_sessions[session_id]['status'] = 'failed'
                    extraction_sessions[session_id]['message'] = f'Failed to extract cookies: {str(e)}'
            elif time.time() >= timeout_time:
                extraction_sessions[session_id]['status'] = 'timeout'
                extraction_sessions[session_id]['message'] = 'Timeout: Please try again'

            # Close browser
            browser.close()
            extraction_sessions[session_id]['browser'] = None
            extraction_sessions[session_id]['context'] = None
            extraction_sessions[session_id]['page'] = None

    except Exception as e:
        logger.error(f"Error in cookie extraction browser: {e}", exc_info=True)
        extraction_sessions[session_id]['status'] = 'failed'
        extraction_sessions[session_id]['message'] = f'Error: {str(e)}'


@app.route('/api/extract-cookies-start', methods=['POST'])
def extract_cookies_start():
    """Launch Playwright browser for manual login and cookie extraction."""
    try:
        data = request.get_json(silent=True) or {}
        label = (data.get('label') or '').strip()
        account_id = data.get('account_id')
        if account_id:
            existing = next(
                (item for item in account_pool.list_accounts() if item['id'] == account_id),
                None
            )
            if not existing:
                return jsonify({'error': 'Account not found'}), 404
            label = label or existing['label']
        if not label:
            return jsonify({'error': 'Account label is required'}), 400

        session_id = str(uuid.uuid4())

        # Initialize session tracking
        extraction_sessions[session_id] = {
            'status': 'initializing',
            'message': 'Starting browser...',
            'cookies_count': 0,
            'current_url': '',
            'browser': None,
            'context': None,
            'page': None,
            'label': label,
            'account_id': account_id
        }

        # Start background thread
        thread = threading.Thread(
            target=run_cookie_extraction_browser,
            args=(session_id,),
            daemon=True
        )
        thread.start()

        logger.info(f"Started cookie extraction session: {session_id}")

        return jsonify({
            'success': True,
            'session_id': session_id,
            'message': 'Browser launching...'
        })

    except Exception as e:
        logger.error(f"Failed to start cookie extraction: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/extract-cookies-status/<session_id>')
def extract_cookies_status(session_id):
    """Get status of cookie extraction process."""
    if session_id not in extraction_sessions:
        return jsonify({'error': 'Session not found'}), 404

    session = extraction_sessions[session_id]

    return jsonify({
        'status': session['status'],
        'message': session.get('message', ''),
        'cookies_count': session.get('cookies_count', 0),
        'current_url': session.get('current_url', ''),
        'account_id': session.get('account_id'),
        'label': session.get('label')
    })


@app.route('/api/extract-cookies-complete/<session_id>', methods=['POST'])
def extract_cookies_complete(session_id):
    """Manually trigger cookie extraction (user clicked 'I'm Logged In' button)."""
    if session_id not in extraction_sessions:
        return jsonify({'error': 'Session not found'}), 404

    session = extraction_sessions[session_id]

    # Trigger extraction by updating status
    if session['status'] in ['waiting_for_login', 'browser_launching']:
        session['status'] = 'extracting'
        logger.info(f"Manual extraction triggered for session {session_id}")

        return jsonify({
            'success': True,
            'message': 'Extracting cookies...'
        })
    else:
        return jsonify({
            'success': False,
            'message': f'Cannot extract in current state: {session["status"]}'
        }), 400


def session_keep_alive_loop():
    """Refresh due account sessions outside reservation execution."""
    while True:
        time.sleep(60)
        if account_pool:
            try:
                results = account_pool.refresh_due_accounts()
                if results:
                    logger.info("Background-refreshed %d account sessions", len(results))
            except Exception as e:
                logger.warning(f"Session keep-alive error: {e}")


def start_keep_alive_thread():
    """Start the session keep-alive background thread if not already running."""
    global keep_alive_thread
    if keep_alive_thread and keep_alive_thread.is_alive():
        return
    keep_alive_thread = threading.Thread(target=session_keep_alive_loop, daemon=True)
    keep_alive_thread.start()
    logger.info("Started session keep-alive background thread (10 min interval)")


# Start keep-alive thread when module loads (if we have a valid client)
# Only start in the worker process, not Flask's reloader process
if account_pool and account_pool.account_count() and os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    start_keep_alive_thread()


if __name__ == '__main__':
    print("=" * 80)
    print("Active Illini Booking Web UI")
    print("=" * 80)
    print()
    print("Starting server at http://localhost:5001")
    print("Press Ctrl+C to stop")
    print()

    app.run(debug=True, host='0.0.0.0', port=5001)
