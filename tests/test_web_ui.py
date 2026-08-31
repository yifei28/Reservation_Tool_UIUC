import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch
from src.account_pool import AccountLease


# Isolate web_ui's module-level application state before importing it.
WEB_TEMP = tempfile.TemporaryDirectory()
WEB_ROOT = Path(WEB_TEMP.name)
os.environ['ACCOUNTS_DIR'] = str(WEB_ROOT / 'accounts')
os.environ['SESSION_FILE'] = str(WEB_ROOT / 'legacy.session')
os.environ['SCHEDULE_FILE'] = str(WEB_ROOT / 'schedule.json')
os.environ['PID_FILE'] = str(WEB_ROOT / 'scheduler.pid')
os.environ['RELOAD_SIGNAL_FILE'] = str(WEB_ROOT / 'reload.signal')

import web_ui


class WebUiAccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = web_ui.app.test_client()

    @classmethod
    def tearDownClass(cls):
        WEB_TEMP.cleanup()

    def setUp(self):
        web_ui.SCHEDULER_PID_FILE.unlink(missing_ok=True)
        for index in reversed(range(len(web_ui.scheduler.scheduled_bookings))):
            web_ui.scheduler.cancel_booking(index)
        web_ui.scheduler.scheduled_bookings = []
        web_ui.scheduler._save_schedule()
        for account in web_ui.account_pool.list_accounts():
            web_ui.account_pool.remove_account(account['id'])

    def test_account_api_lists_and_removes_accounts(self):
        account_id = web_ui.account_pool.store_cookies(
            'UI account', {'session': 'secret-value'}
        )

        status = self.client.get('/api/check-session').get_json()
        self.assertTrue(status['has_session'])
        self.assertEqual(status['account_count'], 1)

        accounts = self.client.get('/api/accounts?date=2026-09-01').get_json()
        self.assertEqual(accounts[0]['label'], 'UI account')
        self.assertNotIn('session_file', accounts[0])
        self.assertNotIn('cookies', accounts[0])

        response = self.client.delete(f'/api/accounts/{account_id}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(web_ui.account_pool.account_count(), 0)

    def test_cookie_extraction_requires_an_account_label(self):
        response = self.client.post('/api/extract-cookies-start', json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn('label', response.get_json()['error'].lower())

    def test_scheduling_does_not_consume_an_account(self):
        web_ui.account_pool.store_cookies('scheduled', {'session': 'value'})
        payload = {
            'facility': 'ARC_MP1',
            'date': '2026-09-01',
            'time': '10 AM - 11 AM',
            'execute_at': '2026-08-29T10:00',
        }
        with patch.object(web_ui, 'ensure_scheduler_running', return_value=True):
            response = self.client.post('/api/schedule', json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        account = web_ui.account_pool.list_accounts('2026-09-01')[0]
        self.assertFalse(account['used_for_date'])
        self.assertFalse(account['leased_for_date'])
        self.assertTrue(account['assigned_for_date'])

    def test_multi_court_schedule_assigns_distinct_accounts_immediately(self):
        web_ui.account_pool.store_cookies('one', {'session': 'one'})
        web_ui.account_pool.store_cookies('two', {'session': 'two'})
        with patch.object(web_ui, 'ensure_scheduler_running', return_value=True):
            response = self.client.post('/api/schedule', json={
                'facility': 'ARC_PICKLEBALL_BADMINTON',
                'date': '2026-09-01',
                'time': '5 - 6 PM',
                'quantity': 2,
                'execute_at': '2026-08-29T17:00',
            })

        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(data['assignments']), 2)
        self.assertEqual(
            len({item['account_id'] for item in data['assignments']}), 2
        )
        self.assertTrue(all(
            account['assigned_for_date']
            for account in web_ui.account_pool.list_accounts('2026-09-01')
        ))

    def test_multi_court_schedule_rejects_partial_assignment(self):
        web_ui.account_pool.store_cookies('only', {'session': 'one'})
        with patch.object(web_ui, 'ensure_scheduler_running', return_value=True):
            response = self.client.post('/api/schedule', json={
                'facility': 'ARC_PICKLEBALL_BADMINTON',
                'date': '2026-09-01',
                'time': '5 - 6 PM',
                'quantity': 2,
            })

        self.assertEqual(response.status_code, 409)
        self.assertEqual(web_ui.scheduler.scheduled_bookings, [])
        self.assertFalse(
            web_ui.account_pool.list_accounts('2026-09-01')[0]['assigned_for_date']
        )

    def test_immediate_booking_response_includes_court_details(self):
        class ImmediateClient:
            last_booking_result = {
                'facility_id': 'court-id',
                'court_name': 'Court 8 BM/PB',
                'participant_id': 'participant-id',
            }

            def prepare_booking(self, **kwargs):
                return 'court-id'

            def book_slot(self, **kwargs):
                return True

        lease = AccountLease(
            account_id='account-id',
            label='Account One',
            target_date='2026-09-01',
            lease_id='lease-id',
            client=ImmediateClient(),
        )
        with patch.object(web_ui.account_pool, 'acquire', return_value=lease), \
             patch.object(web_ui.account_pool, 'mark_used', return_value=True):
            response = self.client.post('/api/book', json={
                'facility': 'ARC_PICKLEBALL_BADMINTON',
                'date': '2026-09-01',
                'time': '4 - 5 PM',
            })

        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data['success'])
        self.assertEqual(data['court_name'], 'Court 8 BM/PB')
        self.assertEqual(data['participant_id'], 'participant-id')

    def test_scheduler_uses_web_server_python_environment(self):
        process = Mock(pid=1234)
        with patch.object(web_ui.subprocess, 'Popen', return_value=process) as popen:
            self.assertTrue(web_ui.start_scheduler_process())

        self.assertEqual(popen.call_args.args[0][0], web_ui.sys.executable)
        self.assertEqual(popen.call_args.kwargs['cwd'], web_ui.PROJECT_ROOT)
        self.assertEqual(web_ui.SCHEDULER_PID_FILE.read_text(), '1234')

    def test_scheduler_restart_stops_old_process_before_starting(self):
        web_ui.SCHEDULER_PID_FILE.write_text('1234')
        with patch.object(web_ui.os, 'kill', side_effect=[None, ProcessLookupError]) as kill, \
             patch.object(web_ui, 'start_scheduler_process', return_value=True) as start:
            self.assertTrue(web_ui.restart_scheduler_process())

        self.assertEqual(kill.call_args_list, [
            call(1234, web_ui.signal.SIGTERM),
            call(1234, 0),
        ])
        start.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
