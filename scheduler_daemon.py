#!/usr/bin/env python3
"""
Scheduler daemon for Active Illini facility bookings.
Runs continuously in the background, executing bookings when slots open.
"""

import sys
import logging
import argparse
from datetime import datetime
from pathlib import Path

from src.scheduler import BookingScheduler
from src.booking_http import FastBookingClient


def setup_logging(log_file: str = "scheduler.log", verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_formatter)

    # File handler
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)


def main():
    import os

    parser = argparse.ArgumentParser(
        description="Automated facility booking scheduler daemon"
    )

    parser.add_argument(
        "--session",
        default=os.getenv('SESSION_FILE', '.session'),
        help="Path to session file with cookies (default: .session or $SESSION_FILE)"
    )

    parser.add_argument(
        "--schedule",
        default=os.getenv('SCHEDULE_FILE', 'bookings_schedule.json'),
        help="Path to schedule file (default: bookings_schedule.json or $SCHEDULE_FILE)"
    )

    parser.add_argument(
        "--log",
        default=os.getenv('LOG_FILE', 'scheduler.log'),
        help="Path to log file (default: scheduler.log or $LOG_FILE)"
    )


    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging"
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one booking and exit (non-daemon mode)"
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(log_file=args.log, verbose=args.verbose)
    logger = logging.getLogger(__name__)

    logger.info("=" * 80)
    logger.info("FACILITY BOOKING SCHEDULER DAEMON")
    logger.info("=" * 80)
    logger.info(f"Session file: {args.session}")
    logger.info(f"Schedule file: {args.schedule}")
    logger.info(f"Log file: {args.log}")
    logger.info(f"Daemon mode: {not args.once}")
    logger.info("=" * 80)

    # Verify session file exists
    if not Path(args.session).exists():
        logger.error(f"Session file not found: {args.session}")
        logger.error("Run: python3 extract_cookies.py")
        return 1

    # Initialize booking client and scheduler
    try:
        logger.info("Initializing fast HTTP booking client...")
        client = FastBookingClient(session_file=args.session)

        logger.info("Initializing scheduler...")
        scheduler = BookingScheduler(
            booking_client=client,
            schedule_file=args.schedule
        )

        # Show scheduled bookings
        bookings = scheduler.list_scheduled_bookings()
        logger.info(f"\nFound {len(bookings)} scheduled bookings:")

        for i, booking in enumerate(bookings):
            logger.info(f"  [{i}] {booking.facility} - {booking.slot_time}")
            logger.info(f"      Target: {booking.target_date.strftime('%Y-%m-%d %H:%M')}")
            logger.info(f"      Execute: {booking.execute_at.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"      Status: {booking.status}")
            if booking.error:
                logger.info(f"      Error: {booking.error}")

        if not bookings:
            logger.warning("\n⚠️  No bookings scheduled!")
            logger.info("Use the CLI to add bookings:")
            logger.info("  python3 main.py schedule --facility ARC_MP1 --date 2025-10-20 --time '11 AM - 12 PM'")
            return 0

        # Run scheduler
        logger.info("\n" + "=" * 80)
        logger.info("Starting scheduler loop...")
        logger.info("Press Ctrl+C to stop")
        logger.info("=" * 80 + "\n")

        scheduler.run_scheduler(daemon=not args.once)

        logger.info("\nScheduler stopped normally")
        return 0

    except KeyboardInterrupt:
        logger.info("\n\nScheduler stopped by user (Ctrl+C)")
        return 0

    except Exception as e:
        logger.error(f"\nScheduler error: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
