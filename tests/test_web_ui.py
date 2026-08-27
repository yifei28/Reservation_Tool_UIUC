import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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


if __name__ == '__main__':
    unittest.main()
