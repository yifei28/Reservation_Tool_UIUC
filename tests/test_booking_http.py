import pickle
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.booking_http import FastBookingClient


class BookingHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.session_file = Path(self.temp_dir.name) / 'account.session'
        with open(self.session_file, 'wb') as handle:
            pickle.dump({
                'cookies': {'original': 'cookie'},
                'authenticated': True,
                'auth_time': 1234.0,
            }, handle)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_keep_alive_persists_server_renewed_cookies(self):
        client = FastBookingClient(str(self.session_file))
        client.session.cookies.set('renewed', 'new-value', domain='active.illinois.edu')
        response = Mock(
            status_code=200,
            url='https://active.illinois.edu/booking/test/facilities',
            text='<button data-facility-id="court"></button>'
        )

        with patch.object(client.session, 'get', return_value=response):
            self.assertTrue(client.keep_alive())

        with open(self.session_file, 'rb') as handle:
            saved = pickle.load(handle)
        self.assertEqual(saved['cookies']['renewed'], 'new-value')
        self.assertEqual(saved['auth_time'], 1234.0)
        self.assertIn('refreshed_at', saved)

    def test_facility_ids_are_cached_before_critical_execution(self):
        client = FastBookingClient(str(self.session_file))
        facility_id = '11111111-1111-1111-1111-111111111111'
        response = Mock(
            status_code=200,
            text=f'<button data-facility-id="{facility_id}"></button>'
        )
        response.raise_for_status = Mock()

        with patch.object(client.session, 'get', return_value=response) as get:
            first = client._get_all_facility_ids('product')
            second = client._get_all_facility_ids('product')

        self.assertEqual(first, [facility_id])
        self.assertEqual(second, [facility_id])
        self.assertEqual(get.call_count, 1)

    def test_keep_alive_rejects_login_html_at_a_200_url(self):
        client = FastBookingClient(str(self.session_file))
        response = Mock(
            status_code=200,
            url='https://active.illinois.edu/booking',
            text='<html><form>Campus Login</form></html>'
        )
        with patch.object(client.session, 'get', return_value=response):
            self.assertFalse(client.keep_alive())
        self.assertIn('expired', client.last_keep_alive_error.lower())


if __name__ == '__main__':
    unittest.main()
