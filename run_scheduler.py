#!/usr/bin/env python3
"""
Standalone scheduler daemon for automated bookings.
Runs independently of the web UI.
"""

import logging
import time
import os
from pathlib import Path
from src.scheduler import BookingScheduler
from src.booking_http import FastBookingClient

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

    # Check if session file exists
    session_file = Path('.session')
    if not session_file.exists():
        logger.error("No .session file found. Run extract_cookies.py first.")
        return

    # Create scheduler
    scheduler = BookingScheduler()

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
