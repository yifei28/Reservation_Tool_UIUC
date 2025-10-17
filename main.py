#!/usr/bin/env python3
"""
Facility Reserver - Automated UIUC Active Illini Court Booking
"""

import argparse
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.config import load_config
from src.booking import book_facility


def setup_logging(verbose: bool = False):
    """Configure logging for the application."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def main():
    """Main entry point for the facility reserver CLI."""
    parser = argparse.ArgumentParser(
        description='Automated UIUC Active Illini facility booking tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --sport badminton --date 2024-01-20 --time 14:00
  %(prog)s --config config.json --verbose
  %(prog)s --list-bookings
        """
    )

    parser.add_argument(
        '--sport',
        type=str,
        help='Sport/facility to book (badminton, basketball, volleyball, etc.)'
    )

    parser.add_argument(
        '--date',
        type=str,
        help='Date for booking (YYYY-MM-DD format)'
    )

    parser.add_argument(
        '--time',
        type=str,
        help='Time for booking (HH:MM format, 24-hour)'
    )

    parser.add_argument(
        '--config',
        type=str,
        default='config.json',
        help='Path to configuration file (default: config.json)'
    )

    parser.add_argument(
        '--list-bookings',
        action='store_true',
        help='List all upcoming bookings'
    )

    parser.add_argument(
        '--schedule',
        action='store_true',
        help='Run the scheduler to automatically book at the right time'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose debug logging'
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    try:
        # Load configuration
        config = load_config(args.config)
        logger.info(f"Loaded configuration from {args.config}")

        # Handle different commands
        if args.list_bookings:
            logger.info("Listing bookings...")
            # TODO: Implement list bookings
            print("No bookings yet - feature coming soon!")

        elif args.schedule:
            logger.info("Starting booking scheduler...")
            # TODO: Implement scheduler
            print("Scheduler not implemented yet!")

        elif args.sport and args.date and args.time:
            logger.info(f"Booking {args.sport} on {args.date} at {args.time}")
            # TODO: Implement immediate booking
            book_facility(args.sport, args.date, args.time, config)

        else:
            parser.print_help()
            sys.exit(1)

    except FileNotFoundError as e:
        logger.error(f"Configuration file not found: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=args.verbose)
        sys.exit(1)


if __name__ == '__main__':
    main()
