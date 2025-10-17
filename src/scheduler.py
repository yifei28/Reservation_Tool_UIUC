"""
Booking scheduler for Active Illini facilities.
Schedules bookings to execute exactly when slots become available (72 hours in advance).
"""

import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass
from pathlib import Path
import json

from .booking_http import FastBookingClient

logger = logging.getLogger(__name__)


@dataclass
class ScheduledBooking:
    """Represents a scheduled booking attempt."""

    facility: str
    target_date: datetime  # The date/time to book FOR
    slot_time: str  # e.g., "11 AM - 12 PM"
    execute_at: datetime  # When to execute the booking (72 hours before)
    facility_id: Optional[str] = None
    status: str = "pending"  # pending, executing, success, failed
    error: Optional[str] = None
    booking_id: Optional[str] = None


class BookingScheduler:
    """Scheduler for automated facility bookings."""

    # Slots open exactly 72 hours before the time slot
    BOOKING_WINDOW_HOURS = 72

    # How many seconds before the opening time to wake up and prepare
    PREP_SECONDS = 10

    # Signal file for manual cookie reload
    RELOAD_SIGNAL_FILE = Path('.reload_cookies_signal')

    def __init__(
        self,
        booking_client: Optional[FastBookingClient] = None,
        schedule_file: str = "bookings_schedule.json"
    ):
        """
        Initialize scheduler.

        Args:
            booking_client: FastBookingClient instance (creates new one if None)
            schedule_file: Path to save/load scheduled bookings
        """
        self.client = booking_client or FastBookingClient()
        self.schedule_file = Path(schedule_file)
        self.scheduled_bookings: List[ScheduledBooking] = []

        # Cookie reload tracking
        self.client_loaded_at = time.time()
        self.last_cookie_check = 0
        self.cookie_check_interval = 300  # Check every 5 minutes

        # Load existing schedule if available
        self._load_schedule()

    def schedule_booking(
        self,
        facility: str,
        target_date: datetime,
        slot_time: str,
        facility_id: Optional[str] = None,
        execute_at: Optional[datetime] = None
    ) -> ScheduledBooking:
        """
        Schedule a booking to execute at a specific time.

        Args:
            facility: Facility name (e.g., "ARC_MP1")
            target_date: The date to book FOR
            slot_time: Time slot text (e.g., "11 AM - 12 PM")
            facility_id: Optional facility ID
            execute_at: When to execute the booking. If None, defaults to 72 hours before target_date.

        Returns:
            ScheduledBooking object
        """
        # Calculate when to execute
        if execute_at is None:
            # Default: 72 hours before target time
            execute_at = target_date - timedelta(hours=self.BOOKING_WINDOW_HOURS)

        booking = ScheduledBooking(
            facility=facility,
            target_date=target_date,
            slot_time=slot_time,
            execute_at=execute_at,
            facility_id=facility_id,
            status="pending"
        )

        self.scheduled_bookings.append(booking)
        self._save_schedule()

        logger.info(
            f"Scheduled booking: {facility} on {target_date.strftime('%Y-%m-%d')} "
            f"at {slot_time} (will execute at {execute_at.strftime('%Y-%m-%d %H:%M:%S')})"
        )

        return booking

    def reload_cookies(self, force: bool = False):
        """
        Reload cookies from session file.

        Args:
            force: If True, reload regardless of last check time

        Returns:
            True if reload succeeded, False otherwise
        """
        if force:
            logger.info("Force reloading cookies...")

        try:
            self.client = FastBookingClient()
            self.client_loaded_at = time.time()
            self.last_cookie_check = time.time()
            logger.info("✅ Cookies reloaded successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to reload cookies: {e}")
            return False

    def _check_reload_signal(self):
        """Check if manual reload was requested via signal file."""
        if self.RELOAD_SIGNAL_FILE.exists():
            logger.info("📢 Reload signal detected, reloading cookies...")
            self.reload_cookies(force=True)

            # Remove signal file
            self.RELOAD_SIGNAL_FILE.unlink()

    def _reload_cookies_if_needed(self):
        """Reload cookies if session file has been updated."""
        now = time.time()

        # Only check every 5 minutes
        if now - self.last_cookie_check < self.cookie_check_interval:
            return

        self.last_cookie_check = now
        session_file = Path('.session')

        if not session_file.exists():
            logger.warning("Session file not found")
            return

        # Check if file was modified since we last loaded it
        try:
            file_mtime = session_file.stat().st_mtime

            if file_mtime > self.client_loaded_at:
                logger.info("Detected updated session file, reloading cookies...")
                self.reload_cookies(force=True)
        except Exception as e:
            logger.debug(f"Error checking session file: {e}")

    def run_scheduler(self, daemon: bool = False):
        """
        Run the scheduler loop.

        Args:
            daemon: If True, run continuously. If False, process once and exit.
        """
        logger.info(f"Scheduler started (daemon={daemon})")

        while True:
            # Reload schedule to pick up any new bookings added via UI
            self._load_schedule()

            # Check for manual reload signal and automatic cookie refresh
            self._check_reload_signal()
            self._reload_cookies_if_needed()

            # Check for pending bookings
            pending = [b for b in self.scheduled_bookings if b.status == "pending"]

            if not pending:
                if daemon:
                    logger.info("No pending bookings. Sleeping for 60 seconds...")
                    time.sleep(60)
                    continue
                else:
                    logger.info("No pending bookings. Exiting.")
                    break

            # Find next booking to execute
            next_booking = min(pending, key=lambda b: b.execute_at)
            now = datetime.now()

            # Calculate time until execution
            time_until = (next_booking.execute_at - now).total_seconds()

            if time_until > self.PREP_SECONDS:
                # Too early - sleep
                if daemon:
                    sleep_time = min(time_until - self.PREP_SECONDS, 60)
                    logger.info(
                        f"Next booking in {time_until:.0f}s. Sleeping for {sleep_time:.0f}s..."
                    )
                    time.sleep(sleep_time)
                    continue
                else:
                    logger.info(
                        f"Next booking at {next_booking.execute_at.strftime('%Y-%m-%d %H:%M:%S')} "
                        f"({time_until:.0f}s from now). Exiting non-daemon mode."
                    )
                    break

            # Time to execute!
            self._execute_booking(next_booking)

            # Save updated schedule
            self._save_schedule()

            # If not daemon mode, exit after first execution
            if not daemon:
                logger.info("Non-daemon mode - exiting after execution")
                break

    def _execute_booking(self, booking: ScheduledBooking):
        """
        Execute a scheduled booking.

        Args:
            booking: ScheduledBooking to execute
        """
        logger.info(f"Executing booking: {booking.facility} - {booking.slot_time}")
        booking.status = "executing"

        # Pre-warm connection and cache facility data (reduces latency by ~100-200ms)
        try:
            cached_facility_id = self.client.prepare_booking(
                facility=booking.facility,
                date=booking.target_date,
                facility_id=booking.facility_id
            )
            if cached_facility_id:
                booking.facility_id = cached_facility_id
        except Exception as e:
            logger.warning(f"Preparation failed (non-critical): {e}")

        # Sleep until exact execution time
        now = datetime.now()
        wait_time = (booking.execute_at - now).total_seconds()

        if wait_time > 0:
            logger.info(f"Waiting {wait_time:.2f}s until exact execution time...")
            time.sleep(wait_time)

        # Execute the booking
        try:
            logger.info(f"BOOKING NOW: {booking.facility} on {booking.target_date.strftime('%Y-%m-%d')} at {booking.slot_time}")

            success = self.client.book_slot(
                facility=booking.facility,
                date=booking.target_date,
                slot_time=booking.slot_time,
                facility_id=booking.facility_id,
                dry_run=False
            )

            if success:
                booking.status = "success"
                logger.info(f"✅ Booking successful!")
            else:
                booking.status = "failed"
                booking.error = "Booking returned False"
                logger.error(f"❌ Booking failed")

        except Exception as e:
            booking.status = "failed"
            booking.error = str(e)
            logger.error(f"❌ Booking error: {e}", exc_info=True)

    def list_scheduled_bookings(self) -> List[ScheduledBooking]:
        """Get all scheduled bookings."""
        # Reload from file to get latest status (updated by daemon process)
        self._load_schedule()
        return self.scheduled_bookings

    def cancel_booking(self, index: int) -> bool:
        """
        Cancel a scheduled booking by index.

        Args:
            index: Index in scheduled_bookings list

        Returns:
            True if cancelled, False if not found
        """
        if 0 <= index < len(self.scheduled_bookings):
            booking = self.scheduled_bookings[index]
            if booking.status == "pending":
                self.scheduled_bookings.pop(index)
                self._save_schedule()
                logger.info(f"Cancelled booking: {booking.facility} - {booking.slot_time}")
                return True
        return False

    def _save_schedule(self):
        """Save scheduled bookings to file."""
        data = {
            "bookings": [
                {
                    "facility": b.facility,
                    "target_date": b.target_date.isoformat(),
                    "slot_time": b.slot_time,
                    "execute_at": b.execute_at.isoformat(),
                    "facility_id": b.facility_id,
                    "status": b.status,
                    "error": b.error,
                    "booking_id": b.booking_id
                }
                for b in self.scheduled_bookings
            ]
        }

        self.schedule_file.write_text(json.dumps(data, indent=2))
        logger.debug(f"Schedule saved to {self.schedule_file}")

    def _load_schedule(self):
        """Load scheduled bookings from file."""
        if not self.schedule_file.exists():
            logger.debug(f"No existing schedule file at {self.schedule_file}")
            return

        try:
            data = json.loads(self.schedule_file.read_text())

            self.scheduled_bookings = [
                ScheduledBooking(
                    facility=b["facility"],
                    target_date=datetime.fromisoformat(b["target_date"]),
                    slot_time=b["slot_time"],
                    execute_at=datetime.fromisoformat(b["execute_at"]),
                    facility_id=b.get("facility_id"),
                    status=b.get("status", "pending"),
                    error=b.get("error"),
                    booking_id=b.get("booking_id")
                )
                for b in data.get("bookings", [])
            ]

            logger.info(f"Loaded {len(self.scheduled_bookings)} scheduled bookings from {self.schedule_file}")

        except Exception as e:
            logger.error(f"Error loading schedule: {e}")
            self.scheduled_bookings = []
