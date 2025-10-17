#!/usr/bin/env python3
"""
Active Illini Facility Booking Automation

Commands:
  book      - Book a facility immediately
  schedule  - Schedule a booking for when slots open (72 hours in advance)
  list      - List scheduled bookings
  cancel    - Cancel a scheduled booking
  daemon    - Run scheduler daemon
"""

import sys
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path

from src.booking_http import FastBookingClient
from src.scheduler import BookingScheduler


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )


def cmd_book(args):
    """Book a facility immediately."""
    logging.info(f"Booking {args.facility} on {args.date} at {args.time}")

    try:
        client = FastBookingClient(session_file=args.session)

        # Parse date
        target_date = datetime.strptime(args.date, "%Y-%m-%d")

        # Check if slot is available first
        if not args.force:
            print(f"Checking availability for {args.date}...")
            slots = client.check_available_slots(args.facility, target_date)

            target_slot = None
            for slot in slots:
                if slot['time_text'] == args.time:
                    target_slot = slot
                    break

            if not target_slot:
                print(f"❌ Slot '{args.time}' is not available!")
                print("\nAvailable slots:")
                for slot in slots:
                    print(f"  - {slot['time_text']}")
                return 1

            print(f"✓ Slot is available: {target_slot['spots_available']}")

        # Confirm if not forced
        if not args.yes:
            print(f"\n⚠️  You are about to book:")
            print(f"  Facility: {args.facility}")
            print(f"  Date: {args.date}")
            print(f"  Time: {args.time}")
            print("\nType 'yes' to confirm: ", end='')
            confirmation = input().strip().lower()

            if confirmation != 'yes':
                print("Cancelled")
                return 0

        # Book it
        print("\nBooking...")
        success = client.book_slot(
            facility=args.facility,
            date=target_date,
            slot_time=args.time,
            dry_run=args.dry_run
        )

        if success:
            if args.dry_run:
                print("✅ DRY RUN - Would have booked successfully")
            else:
                print("✅ BOOKING SUCCESSFUL!")
                print(f"\nCheck your bookings at:")
                print("https://active.illinois.edu/booking/mybookings")
            return 0
        else:
            print("❌ BOOKING FAILED")
            return 1

    except FileNotFoundError as e:
        print(f"❌ {e}")
        print("Run: python3 extract_cookies.py")
        return 1
    except Exception as e:
        logging.error(f"Booking error: {e}", exc_info=args.verbose)
        return 1


def cmd_schedule(args):
    """Schedule a booking for when slots open."""
    try:
        # Parse date
        target_date = datetime.strptime(f"{args.date} {args.time.split()[0]}", "%Y-%m-%d %I")

        # Initialize scheduler
        scheduler = BookingScheduler(schedule_file=args.schedule)

        # Add booking to schedule
        booking = scheduler.schedule_booking(
            facility=args.facility,
            target_date=target_date,
            slot_time=args.time
        )

        print("✅ Booking scheduled!")
        print(f"\nDetails:")
        print(f"  Facility: {booking.facility}")
        print(f"  Target Date: {booking.target_date.strftime('%Y-%m-%d %H:%M')}")
        print(f"  Time Slot: {booking.slot_time}")
        print(f"  Will Execute: {booking.execute_at.strftime('%Y-%m-%d %H:%M:%S')}")

        time_until = (booking.execute_at - datetime.now()).total_seconds()
        hours = time_until / 3600
        print(f"  Time Until Execution: {hours:.1f} hours")

        print(f"\nTo run the scheduler daemon:")
        print(f"  python3 scheduler_daemon.py")

        return 0

    except Exception as e:
        logging.error(f"Scheduling error: {e}", exc_info=args.verbose)
        return 1


def cmd_list(args):
    """List scheduled bookings."""
    try:
        scheduler = BookingScheduler(schedule_file=args.schedule)
        bookings = scheduler.list_scheduled_bookings()

        if not bookings:
            print("No scheduled bookings")
            return 0

        print(f"Scheduled Bookings ({len(bookings)}):")
        print("=" * 80)

        now = datetime.now()

        for i, booking in enumerate(bookings):
            time_until = (booking.execute_at - now).total_seconds()
            hours_until = time_until / 3600

            print(f"\n[{i}] {booking.facility} - {booking.slot_time}")
            print(f"    Target: {booking.target_date.strftime('%Y-%m-%d %H:%M')}")
            print(f"    Execute: {booking.execute_at.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"    Status: {booking.status}")

            if booking.status == "pending":
                if hours_until > 0:
                    print(f"    Time Until: {hours_until:.1f} hours")
                else:
                    print(f"    OVERDUE by {-hours_until:.1f} hours")

            if booking.error:
                print(f"    Error: {booking.error}")

        print()
        return 0

    except Exception as e:
        logging.error(f"List error: {e}", exc_info=args.verbose)
        return 1


def cmd_cancel(args):
    """Cancel a scheduled booking."""
    try:
        scheduler = BookingScheduler(schedule_file=args.schedule)

        if scheduler.cancel_booking(args.index):
            print(f"✅ Cancelled booking #{args.index}")
            return 0
        else:
            print(f"❌ Booking #{args.index} not found or cannot be cancelled")
            return 1

    except Exception as e:
        logging.error(f"Cancel error: {e}", exc_info=args.verbose)
        return 1


def cmd_daemon(args):
    """Run scheduler daemon."""
    print("Use scheduler_daemon.py instead:")
    print(f"  python3 scheduler_daemon.py")
    print()
    print("Options:")
    print("  --headless      Run browser in headless mode")
    print("  --once          Process one booking and exit")
    print("  --verbose       Verbose logging")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Active Illini Facility Booking Automation"
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging"
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Book command
    book_parser = subparsers.add_parser('book', help='Book a facility immediately')
    book_parser.add_argument('facility', help='Facility name (e.g., ARC_MP1)')
    book_parser.add_argument('date', help='Date to book (YYYY-MM-DD)')
    book_parser.add_argument('time', help='Time slot (e.g., "11 AM - 12 PM")')
    book_parser.add_argument('--session', default='.session', help='Session file')
    book_parser.add_argument('--dry-run', action='store_true', help='Dry run (don\'t actually book)')
    book_parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation')
    book_parser.add_argument('--force', action='store_true', help='Skip availability check')

    # Schedule command
    schedule_parser = subparsers.add_parser('schedule', help='Schedule a booking')
    schedule_parser.add_argument('facility', help='Facility name (e.g., ARC_MP1)')
    schedule_parser.add_argument('date', help='Date to book (YYYY-MM-DD)')
    schedule_parser.add_argument('time', help='Time slot (e.g., "11 AM - 12 PM")')
    schedule_parser.add_argument('--schedule', default='bookings_schedule.json', help='Schedule file')

    # List command
    list_parser = subparsers.add_parser('list', help='List scheduled bookings')
    list_parser.add_argument('--schedule', default='bookings_schedule.json', help='Schedule file')

    # Cancel command
    cancel_parser = subparsers.add_parser('cancel', help='Cancel a scheduled booking')
    cancel_parser.add_argument('index', type=int, help='Booking index from list command')
    cancel_parser.add_argument('--schedule', default='bookings_schedule.json', help='Schedule file')

    # Daemon command
    daemon_parser = subparsers.add_parser('daemon', help='Run scheduler daemon')

    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    if not args.command:
        parser.print_help()
        return 0

    # Dispatch to command handler
    commands = {
        'book': cmd_book,
        'schedule': cmd_schedule,
        'list': cmd_list,
        'cancel': cmd_cancel,
        'daemon': cmd_daemon
    }

    return commands[args.command](args)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nCancelled by user")
        sys.exit(0)
