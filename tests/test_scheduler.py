import pickle
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from src.account_pool import AccountPool
from src.scheduler import BookingScheduler, ScheduledBooking


class TimedFakeClient:
    starts = []
    starts_lock = threading.Lock()
    result_by_account = {}

    def __init__(self, session_file):
        self.session_file = Path(session_file)
        with open(self.session_file, 'rb') as handle:
            self.name = pickle.load(handle)['cookies']['account']
        self.last_booking_result = None

    def keep_alive(self):
        return True

    def prepare_booking(self, facility, date, facility_id=None):
        return facility_id or f'court-{self.name}'

    def book_slot(self, **kwargs):
        with self.starts_lock:
            self.starts.append((self.name, time.perf_counter()))
        time.sleep(0.03)
        success = self.result_by_account.get(self.name, True)
        if success:
            self.last_booking_result = {
                'facility_id': kwargs.get('facility_id'),
                'court_name': f'Named {kwargs.get("facility_id")}',
                'participant_id': f'participant-{self.name}',
            }
        return success


class SchedulerMultiAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        TimedFakeClient.starts = []
        TimedFakeClient.result_by_account = {}
        self.pool = AccountPool(
            accounts_dir=str(self.root / 'accounts'),
            legacy_session_file=None,
            client_class=TimedFakeClient,
        )
        self.pool.store_cookies('one', {'account': 'one'})
        self.pool.store_cookies('two', {'account': 'two'})
        self.scheduler = BookingScheduler(
            account_pool=self.pool,
            schedule_file=str(self.root / 'schedule.json'),
            reload_signal_file=str(self.root / 'reload.signal'),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_booking(self, slot, execute_at):
        return ScheduledBooking(
            facility='ARC_MP1',
            target_date=datetime(2026, 9, 1, 10),
            slot_time=slot,
            execute_at=execute_at,
            booking_id=slot,
        )

    def test_simultaneous_bookings_use_distinct_accounts_and_start_together(self):
        execute_at = datetime.now() + timedelta(seconds=0.15)
        bookings = [
            self.make_booking('9 AM - 10 AM', execute_at),
            self.make_booking('10 AM - 11 AM', execute_at),
        ]
        prepared = self.scheduler._prepare_bookings(bookings)
        self.assertEqual(len(prepared), 2)
        self.assertEqual(len({item.lease.account_id for item in prepared}), 2)

        # Account acquisition must be complete before execution begins.
        original_acquire = self.pool.acquire
        self.pool.acquire = lambda *args, **kwargs: self.fail('acquire ran on critical path')
        try:
            self.scheduler._execute_prepared_concurrently(prepared)
        finally:
            self.pool.acquire = original_acquire

        self.assertEqual([booking.status for booking in bookings], ['success', 'success'])
        self.assertEqual(bookings[0].court_name, 'Named court-one')
        self.assertEqual(bookings[1].participant_id, 'participant-two')
        starts = [started for _, started in TimedFakeClient.starts]
        self.assertLess(max(starts) - min(starts), 0.05)
        accounts = self.pool.list_accounts(datetime(2026, 9, 1))
        self.assertTrue(all(account['used_for_date'] for account in accounts))

        self.scheduler.scheduled_bookings = bookings
        self.scheduler._save_schedule()
        reloaded = BookingScheduler(
            account_pool=self.pool,
            schedule_file=str(self.root / 'schedule.json'),
            reload_signal_file=str(self.root / 'reload.signal'),
        )
        self.assertEqual(reloaded.scheduled_bookings[0].court_name, 'Named court-one')
        self.assertEqual(reloaded.scheduled_bookings[1].participant_id, 'participant-two')

    def test_failed_booking_releases_account(self):
        TimedFakeClient.result_by_account['one'] = False
        booking = self.make_booking('9 AM - 10 AM', datetime.now())
        prepared = self.scheduler._prepare_bookings([booking])
        self.scheduler._execute_prepared_concurrently(prepared)

        self.assertEqual(booking.status, 'failed')
        replacement = self.pool.acquire(booking.target_date)
        self.assertEqual(replacement.label, 'one')

    def test_background_refresh_is_suppressed_near_deadline(self):
        calls = []
        original = self.pool.refresh_due_accounts
        self.pool.refresh_due_accounts = lambda *args, **kwargs: calls.append(True) or {}
        try:
            self.scheduler._refresh_accounts_if_safe(30)
            self.assertEqual(calls, [])
            self.scheduler._refresh_accounts_if_safe(300)
            self.assertEqual(calls, [True])
        finally:
            self.pool.refresh_due_accounts = original

    def test_manual_reload_signal_is_deferred_near_deadline(self):
        self.scheduler.reload_signal_file.touch()
        calls = []
        original = self.scheduler.reload_cookies
        self.scheduler.reload_cookies = lambda force=False: calls.append(force) or True
        try:
            self.scheduler._check_reload_signal(30)
            self.assertEqual(calls, [])
            self.assertTrue(self.scheduler.reload_signal_file.exists())

            self.scheduler._check_reload_signal(300)
            self.assertEqual(calls, [True])
            self.assertFalse(self.scheduler.reload_signal_file.exists())
        finally:
            self.scheduler.reload_cookies = original


if __name__ == '__main__':
    unittest.main()
