#!/usr/bin/env python3
"""
Standalone scheduler daemon for automated bookings.
Runs independently of the web UI.
"""

import logging
import os
from src.scheduler import BookingScheduler
from src.account_pool import AccountPool

# Configure logging to both file and console
log_file = os.getenv('LOG_FILE', 'scheduler.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()  # Also log to console
    ]
)
logger = logging.getLogger(__name__)

def main():
    logger.info("Starting scheduler daemon...")

    account_pool = AccountPool(
        accounts_dir=os.getenv('ACCOUNTS_DIR', '.accounts'),
        legacy_session_file=os.getenv('SESSION_FILE', '.session')
    )
    if account_pool.account_count() == 0:
        logger.error("No account sessions found. Add an account in the web UI first.")
        return

    scheduler = BookingScheduler(
        account_pool=account_pool,
        schedule_file=os.getenv('SCHEDULE_FILE', 'bookings_schedule.json'),
        reload_signal_file=os.getenv('RELOAD_SIGNAL_FILE', '.reload_cookies_signal')
    )

    logger.info("Scheduler daemon running. Monitoring for scheduled bookings...")

    # Run daemon loop
    try:
        scheduler.run_scheduler(daemon=True)
    except KeyboardInterrupt:
        logger.info("Scheduler daemon stopped by user")
    except Exception as e:
        logger.error(f"Scheduler daemon error: {e}", exc_info=True)

if __name__ == '__main__':
    main()
