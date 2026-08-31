"""Booking scheduler for Active Illini facilities.

Accounts are leased and connections are prepared before the reservation opens.
Bookings sharing an execution timestamp are released concurrently.
"""

import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .account_pool import AccountLease, AccountPool, NoAvailableAccountError
from .booking_http import FastBookingClient

logger = logging.getLogger(__name__)


@dataclass
class ScheduledBooking:
    """Represents a scheduled booking attempt."""

    facility: str
    target_date: datetime
    slot_time: str
    execute_at: datetime
    facility_id: Optional[str] = None
    status: str = "pending"
    error: Optional[str] = None
    booking_id: Optional[str] = None
    account_id: Optional[str] = None
    court_name: Optional[str] = None
    participant_id: Optional[str] = None
    batch_id: Optional[str] = None
    batch_index: int = 1
    batch_size: int = 1


@dataclass
class PreparedScheduledBooking:
    """Everything needed at execution time, already loaded in memory."""

    booking: ScheduledBooking
    lease: AccountLease
    preferred_facility_id: Optional[str] = None


class BookingScheduler:
    """Execute bookings at the opening time with one account per target day."""

    BOOKING_WINDOW_HOURS = 72
    PREP_SECONDS = 60
    REFRESH_GUARD_SECONDS = 120
    SIMULTANEOUS_TOLERANCE_SECONDS = 0.05

    def __init__(
        self,
        booking_client: Optional[FastBookingClient] = None,
        schedule_file: str = "bookings_schedule.json",
        account_pool: Optional[AccountPool] = None,
        accounts_dir: str = ".accounts",
        legacy_session_file: str = ".session",
        reload_signal_file: str = ".reload_cookies_signal",
    ):
        self.schedule_file = Path(schedule_file)
        self.reload_signal_file = Path(reload_signal_file)
        self.scheduled_bookings: List[ScheduledBooking] = []

        # booking_client remains as a compatibility path for explicit callers.
        # Production uses AccountPool.
        self._legacy_client = booking_client
        self.account_pool = account_pool
        if not self._legacy_client and not self.account_pool:
            self.account_pool = AccountPool(
                accounts_dir=accounts_dir,
                legacy_session_file=legacy_session_file,
            )
        self._load_schedule()

    def schedule_booking(
        self,
        facility: str,
        target_date: datetime,
        slot_time: str,
        facility_id: Optional[str] = None,
        execute_at: Optional[datetime] = None,
    ) -> ScheduledBooking:
        return self.schedule_batch(
            facility=facility,
            target_date=target_date,
            slot_time=slot_time,
            quantity=1,
            facility_id=facility_id,
            execute_at=execute_at,
        )[0]

    def schedule_batch(
        self,
        facility: str,
        target_date: datetime,
        slot_time: str,
        quantity: int,
        facility_id: Optional[str] = None,
        execute_at: Optional[datetime] = None,
    ) -> List[ScheduledBooking]:
        """Atomically schedule multiple attempts with distinct assigned accounts."""
        if quantity < 1:
            raise ValueError("Quantity must be at least 1")
        if self._legacy_client and quantity > 1:
            raise NoAvailableAccountError("Multiple bookings require an account pool")
        if execute_at is None:
            execute_at = target_date - timedelta(hours=self.BOOKING_WINDOW_HOURS)
        # The web process and daemon have separate scheduler instances.
        self._load_schedule()

        batch_id = uuid.uuid4().hex
        bookings = [
            ScheduledBooking(
                facility=facility,
                target_date=target_date,
                slot_time=slot_time,
                execute_at=execute_at,
                facility_id=facility_id,
                status="pending",
                booking_id=uuid.uuid4().hex,
                batch_id=batch_id,
                batch_index=index + 1,
                batch_size=quantity,
            )
            for index in range(quantity)
        ]

        assignments = {}
        if not self._legacy_client:
            assignments = self.account_pool.assign_accounts(
                target_date,
                [booking.booking_id for booking in bookings],
                batch_id=batch_id,
            )
            for booking in bookings:
                booking.account_id = assignments[booking.booking_id]

        try:
            self.scheduled_bookings.extend(bookings)
            self._save_schedule()
        except Exception:
            self.scheduled_bookings = [
                existing for existing in self.scheduled_bookings
                if existing.batch_id != batch_id
            ]
            if not self._legacy_client:
                for booking in bookings:
                    self.account_pool.release_assignment(
                        booking.account_id, target_date, booking.booking_id
                    )
            raise

        logger.info(
            "Scheduled %d booking(s): %s on %s at %s (executes at %s)",
            quantity,
            facility,
            target_date.strftime("%Y-%m-%d"),
            slot_time,
            execute_at.strftime("%Y-%m-%d %H:%M:%S"),
        )
        return bookings

    def reload_cookies(self, force: bool = False) -> bool:
        """Validate and refresh every stored account session."""
        if self._legacy_client:
            try:
                self._legacy_client._load_cookies()
                return True
            except Exception as exc:
                logger.error("Failed to reload cookies: %s", exc)
                return False

        results = self.account_pool.refresh_due_accounts(force=force)
        if not results:
            return self.account_pool.account_count() > 0
        return any(results.values())

    def _check_reload_signal(self, time_until_next: Optional[float] = None) -> None:
        if not self.reload_signal_file.exists():
            return
        if time_until_next is not None and time_until_next <= self.REFRESH_GUARD_SECONDS:
            logger.info("Deferring cookie reload signal until after the imminent booking")
            return
        logger.info("Reload signal detected; refreshing all account sessions")
        self.reload_cookies(force=True)
        self.reload_signal_file.unlink(missing_ok=True)

    def _refresh_accounts_if_safe(self, time_until_next: Optional[float]) -> None:
        """Never perform background refresh close to a booking deadline."""
        if self._legacy_client:
            return
        if time_until_next is not None and time_until_next <= self.REFRESH_GUARD_SECONDS:
            return
        results = self.account_pool.refresh_due_accounts()
        if results:
            valid_count = sum(1 for valid in results.values() if valid)
            logger.info("Refreshed %d accounts (%d valid)", len(results), valid_count)

    def run_scheduler(self, daemon: bool = False) -> None:
        logger.info("Scheduler started (daemon=%s)", daemon)

        while True:
            self._load_schedule()
            pending = [booking for booking in self.scheduled_bookings if booking.status == "pending"]

            if not pending:
                self._check_reload_signal()
                self._refresh_accounts_if_safe(None)
                if daemon:
                    logger.info("No pending bookings. Sleeping for 60 seconds...")
                    time.sleep(60)
                    continue
                logger.info("No pending bookings. Exiting.")
                break

            next_booking = min(pending, key=lambda booking: booking.execute_at)
            time_until = (next_booking.execute_at - datetime.now()).total_seconds()
            self._check_reload_signal(time_until)
            self._refresh_accounts_if_safe(time_until)

            if time_until > self.PREP_SECONDS:
                if daemon:
                    sleep_time = min(time_until - self.PREP_SECONDS, 60)
                    logger.info(
                        "Next booking in %.0fs. Sleeping for %.0fs...",
                        time_until,
                        sleep_time,
                    )
                    time.sleep(sleep_time)
                    continue
                logger.info(
                    "Next booking at %s (%.0fs from now). Exiting non-daemon mode.",
                    next_booking.execute_at.strftime("%Y-%m-%d %H:%M:%S"),
                    time_until,
                )
                break

            simultaneous = [
                booking
                for booking in pending
                if abs((booking.execute_at - next_booking.execute_at).total_seconds())
                <= self.SIMULTANEOUS_TOLERANCE_SECONDS
            ]
            if not self._legacy_client:
                quiet_until = max(
                    booking.execute_at.timestamp() for booking in simultaneous
                ) + 30
                self.account_pool.pause_background_refresh(quiet_until)
            try:
                prepared = self._prepare_bookings(simultaneous)
                self._save_schedule()
                self._execute_prepared_concurrently(prepared)
                self._save_schedule()
            finally:
                if not self._legacy_client:
                    self.account_pool.clear_background_refresh_pause()

            if not daemon:
                logger.info("Non-daemon mode - exiting after execution")
                break

    def _lease_account(self, booking: ScheduledBooking) -> AccountLease:
        if self._legacy_client:
            return AccountLease(
                account_id="legacy",
                label="Legacy account",
                target_date=booking.target_date.date().isoformat(),
                lease_id=booking.booking_id or uuid.uuid4().hex,
                client=self._legacy_client,
            )
        if not booking.booking_id:
            booking.booking_id = uuid.uuid4().hex
        if not booking.batch_id:
            booking.batch_id = booking.booking_id

        previous_account_id = booking.account_id
        if previous_account_id:
            try:
                return self.account_pool.activate_assignment(
                    previous_account_id,
                    booking.target_date,
                    booking.booking_id,
                    validate=True,
                )
            except NoAvailableAccountError:
                self.account_pool.release_assignment(
                    previous_account_id, booking.target_date, booking.booking_id
                )

        assignments = self.account_pool.assign_accounts(
            booking.target_date,
            [booking.booking_id],
            batch_id=booking.batch_id,
            exclude={previous_account_id} if previous_account_id else None,
        )
        booking.account_id = assignments[booking.booking_id]
        return self.account_pool.activate_assignment(
            booking.account_id,
            booking.target_date,
            booking.booking_id,
            validate=True,
        )

    def _prepare_bookings(
        self, bookings: List[ScheduledBooking]
    ) -> List[PreparedScheduledBooking]:
        """Validate accounts and warm network state concurrently before opening."""
        if not bookings:
            return []
        with ThreadPoolExecutor(max_workers=len(bookings)) as executor:
            prepared = [
                item for item in executor.map(self._prepare_booking, bookings) if item
            ]
        return self._assign_preferred_courts(prepared)

    def _prepare_booking(
        self, booking: ScheduledBooking
    ) -> Optional[PreparedScheduledBooking]:
        lease = None
        try:
            lease = self._lease_account(booking)
            booking.account_id = lease.account_id
            booking.status = "executing"

            cached_facility_id = lease.client.prepare_booking(
                facility=booking.facility,
                date=booking.target_date,
                facility_id=booking.facility_id,
            )
            if cached_facility_id:
                booking.facility_id = cached_facility_id
            logger.info("Prepared %s with account %s", booking.slot_time, lease.label)
            return PreparedScheduledBooking(booking, lease)
        except NoAvailableAccountError as exc:
            booking.status = "failed"
            booking.error = str(exc)
            logger.error("Cannot prepare booking: %s", exc)
        except Exception as exc:
            if lease and not self._legacy_client:
                self.account_pool.release(lease)
            booking.status = "failed"
            booking.error = f"Preparation failed: {exc}"
            logger.error("Booking preparation failed: %s", exc, exc_info=True)
        return None

    def _assign_preferred_courts(
        self, prepared: List[PreparedScheduledBooking]
    ) -> List[PreparedScheduledBooking]:
        """Give simultaneous attempts distinct first courts before the deadline."""
        groups: Dict[Tuple, List[PreparedScheduledBooking]] = {}
        for item in prepared:
            booking = item.booking
            key = (
                booking.facility,
                booking.target_date,
                booking.slot_time,
                booking.execute_at,
            )
            groups.setdefault(key, []).append(item)

        ready = []
        for group in groups.values():
            sample = group[0]
            getter = getattr(sample.lease.client, "get_prepared_facility_ids", None)
            facility_ids = getter(sample.booking.facility) if getter else []
            if not facility_ids:
                ready.extend(group)
                continue

            for index, item in enumerate(group):
                if index >= len(facility_ids):
                    item.booking.status = "failed"
                    item.booking.error = (
                        f"Requested more courts than the facility provides "
                        f"({len(facility_ids)})"
                    )
                    if not self._legacy_client:
                        self.account_pool.release(item.lease)
                    continue
                item.preferred_facility_id = facility_ids[index]
                ready.append(item)
        return ready

    def _execute_prepared(self, prepared: PreparedScheduledBooking) -> None:
        booking = prepared.booking
        lease = prepared.lease

        wait_time = (booking.execute_at - datetime.now()).total_seconds()
        if wait_time > 0:
            time.sleep(wait_time)

        try:
            logger.info(
                "BOOKING NOW: %s on %s at %s with account %s",
                booking.facility,
                booking.target_date.strftime("%Y-%m-%d"),
                booking.slot_time,
                lease.label,
            )
            success = lease.client.book_slot(
                facility=booking.facility,
                date=booking.target_date,
                slot_time=booking.slot_time,
                facility_id=booking.facility_id,
                preferred_facility_id=prepared.preferred_facility_id,
                dry_run=False,
            )
            if success:
                result = getattr(lease.client, "last_booking_result", None) or {}
                booking.status = "success"
                booking.error = None
                booking.facility_id = result.get("facility_id", booking.facility_id)
                booking.court_name = result.get("court_name")
                booking.participant_id = result.get("participant_id")
                if not self._legacy_client:
                    self.account_pool.mark_used(lease)
                logger.info(
                    "Booking successful with account %s on %s",
                    lease.label,
                    booking.court_name or booking.facility_id or "unknown court",
                )
            else:
                booking.status = "failed"
                booking.error = "Booking returned False"
                if not self._legacy_client:
                    self.account_pool.release(lease)
                logger.error("Booking failed with account %s", lease.label)
        except Exception as exc:
            booking.status = "failed"
            booking.error = str(exc)
            if not self._legacy_client:
                self.account_pool.release(lease)
            logger.error("Booking error: %s", exc, exc_info=True)

    def _execute_prepared_concurrently(
        self, prepared: List[PreparedScheduledBooking]
    ) -> None:
        if not prepared:
            return
        # Workers are created before the deadline and sleep independently until
        # execute_at, keeping account-pool and thread startup work off the deadline.
        with ThreadPoolExecutor(max_workers=len(prepared)) as executor:
            futures = [executor.submit(self._execute_prepared, item) for item in prepared]
            for future in futures:
                future.result()

    def _execute_booking(self, booking: ScheduledBooking) -> None:
        """Compatibility helper for executing one booking immediately."""
        prepared = self._prepare_bookings([booking])
        self._execute_prepared_concurrently(prepared)

    def list_scheduled_bookings(self) -> List[ScheduledBooking]:
        self._load_schedule()
        return self.scheduled_bookings

    def cancel_booking(self, index: int) -> bool:
        if 0 <= index < len(self.scheduled_bookings):
            booking = self.scheduled_bookings[index]
            if booking.status == "pending":
                if not self._legacy_client and booking.account_id and booking.booking_id:
                    self.account_pool.release_assignment(
                        booking.account_id, booking.target_date, booking.booking_id
                    )
                self.scheduled_bookings.pop(index)
                self._save_schedule()
                logger.info("Cancelled booking: %s - %s", booking.facility, booking.slot_time)
                return True
        return False

    def cancel_batch(self, batch_id: str) -> int:
        """Cancel every pending booking in a batch and release its assignments."""
        cancelled = []
        remaining = []
        for booking in self.scheduled_bookings:
            if booking.batch_id == batch_id and booking.status == "pending":
                cancelled.append(booking)
            else:
                remaining.append(booking)
        if not cancelled:
            return 0
        if not self._legacy_client:
            for booking in cancelled:
                if booking.account_id and booking.booking_id:
                    self.account_pool.release_assignment(
                        booking.account_id, booking.target_date, booking.booking_id
                    )
        self.scheduled_bookings = remaining
        self._save_schedule()
        return len(cancelled)

    def _save_schedule(self) -> None:
        data = {
            "bookings": [
                {
                    "facility": booking.facility,
                    "target_date": booking.target_date.isoformat(),
                    "slot_time": booking.slot_time,
                    "execute_at": booking.execute_at.isoformat(),
                    "facility_id": booking.facility_id,
                    "status": booking.status,
                    "error": booking.error,
                    "booking_id": booking.booking_id,
                    "account_id": booking.account_id,
                    "court_name": booking.court_name,
                    "participant_id": booking.participant_id,
                    "batch_id": booking.batch_id,
                    "batch_index": booking.batch_index,
                    "batch_size": booking.batch_size,
                }
                for booking in self.scheduled_bookings
            ]
        }
        self.schedule_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.schedule_file.with_name(
            f"{self.schedule_file.name}.tmp-{uuid.uuid4().hex}"
        )
        temp_file.write_text(json.dumps(data, indent=2))
        os.replace(temp_file, self.schedule_file)
        logger.debug("Schedule saved to %s", self.schedule_file)

    def _load_schedule(self) -> None:
        if not self.schedule_file.exists():
            return
        try:
            data = json.loads(self.schedule_file.read_text())
            self.scheduled_bookings = [
                ScheduledBooking(
                    facility=item["facility"],
                    target_date=datetime.fromisoformat(item["target_date"]),
                    slot_time=item["slot_time"],
                    execute_at=datetime.fromisoformat(item["execute_at"]),
                    facility_id=item.get("facility_id"),
                    status=item.get("status", "pending"),
                    error=item.get("error"),
                    booking_id=item.get("booking_id") or uuid.uuid4().hex,
                    account_id=item.get("account_id"),
                    court_name=item.get("court_name"),
                    participant_id=item.get("participant_id"),
                    batch_id=item.get("batch_id") or item.get("booking_id"),
                    batch_index=item.get("batch_index", 1),
                    batch_size=item.get("batch_size", 1),
                )
                for item in data.get("bookings", [])
            ]
        except Exception as exc:
            logger.error("Error loading schedule: %s", exc)
            self.scheduled_bookings = []
