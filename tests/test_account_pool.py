import json
import multiprocessing
import pickle
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from src.account_pool import AccountPool, NoAvailableAccountError


class FakeClient:
    keep_alive_calls = []

    def __init__(self, session_file):
        self.session_file = Path(session_file)
        with open(self.session_file, 'rb') as handle:
            data = pickle.load(handle)
        self.cookies = data['cookies']
        self.name = self.cookies.get('account', 'unknown')

    def keep_alive(self):
        self.keep_alive_calls.append(self.name)
        state = self.cookies.get('valid', 'yes')
        self.last_keep_alive_error = (
            'Network error: temporary outage' if state == 'network' else None
        )
        return state == 'yes'


def acquire_in_process(accounts_dir, output_queue):
    pool = AccountPool(
        accounts_dir=accounts_dir,
        legacy_session_file=None,
        client_class=FakeClient,
    )
    lease = pool.acquire(datetime(2026, 9, 3, 10))
    output_queue.put(lease.account_id)


class AccountPoolTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        FakeClient.keep_alive_calls = []
        self.pool = AccountPool(
            accounts_dir=str(self.root / 'accounts'),
            legacy_session_file=None,
            client_class=FakeClient,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def add_account(self, label, valid='yes'):
        return self.pool.store_cookies(label, {'account': label, 'valid': valid})

    def test_rotates_accounts_and_resets_on_next_target_date(self):
        first_id = self.add_account('first')
        second_id = self.add_account('second')
        target = datetime(2026, 9, 1, 10)

        first = self.pool.acquire(target)
        self.assertEqual(first.account_id, first_id)
        self.assertTrue(self.pool.mark_used(first))

        second = self.pool.acquire(target)
        self.assertEqual(second.account_id, second_id)
        self.assertTrue(self.pool.mark_used(second))

        with self.assertRaises(NoAvailableAccountError):
            self.pool.acquire(target)

        next_day = self.pool.acquire(datetime(2026, 9, 2, 10))
        self.assertEqual(next_day.account_id, first_id)

    def test_failed_attempt_release_makes_account_available_again(self):
        account_id = self.add_account('reusable')
        target = datetime(2026, 9, 1, 10)
        lease = self.pool.acquire(target)
        self.assertTrue(self.pool.release(lease))
        replacement = self.pool.acquire(target)
        self.assertEqual(replacement.account_id, account_id)

    def test_invalid_account_is_excluded_and_next_account_is_used(self):
        invalid_id = self.add_account('expired', valid='no')
        valid_id = self.add_account('valid')

        lease = self.pool.acquire(datetime(2026, 9, 1, 10))

        self.assertEqual(lease.account_id, valid_id)
        accounts = {item['id']: item for item in self.pool.list_accounts()}
        self.assertEqual(accounts[invalid_id]['status'], 'invalid')

    def test_transient_network_failure_does_not_permanently_invalidate_account(self):
        transient_id = self.add_account('temporary', valid='network')
        valid_id = self.add_account('backup')
        lease = self.pool.acquire(datetime(2026, 9, 1, 10))
        self.assertEqual(lease.account_id, valid_id)
        accounts = {item['id']: item for item in self.pool.list_accounts()}
        self.assertEqual(accounts[transient_id]['status'], 'unknown')

    def test_concurrent_acquires_never_return_the_same_account(self):
        ids = {self.add_account(f'account-{index}') for index in range(4)}
        barrier = threading.Barrier(4)

        def acquire_one():
            barrier.wait()
            return self.pool.acquire(datetime(2026, 9, 1, 10)).account_id

        with ThreadPoolExecutor(max_workers=4) as executor:
            acquired = list(executor.map(lambda _: acquire_one(), range(4)))

        self.assertEqual(set(acquired), ids)
        self.assertEqual(len(acquired), len(set(acquired)))

    def test_separate_processes_cannot_lease_the_same_account(self):
        ids = {self.add_account('process-one'), self.add_account('process-two')}
        context = multiprocessing.get_context('fork')
        output = context.Queue()
        processes = [
            context.Process(
                target=acquire_in_process,
                args=(str(self.root / 'accounts'), output),
            )
            for _ in range(2)
        ]
        for process in processes:
            process.start()
        acquired = [output.get(timeout=5) for _ in processes]
        for process in processes:
            process.join(timeout=5)
            self.assertEqual(process.exitcode, 0)

        self.assertEqual(set(acquired), ids)

    def test_legacy_session_is_migrated_once(self):
        legacy = self.root / 'legacy.session'
        with open(legacy, 'wb') as handle:
            pickle.dump({'cookies': {'account': 'legacy'}, 'authenticated': True}, handle)

        pool = AccountPool(
            accounts_dir=str(self.root / 'migrated'),
            legacy_session_file=str(legacy),
            client_class=FakeClient,
        )
        self.assertEqual(pool.account_count(), 1)
        self.assertEqual(pool.list_accounts()[0]['label'], 'Default account')

        # Reconstructing the pool must not duplicate the imported account.
        second_pool = AccountPool(
            accounts_dir=str(self.root / 'migrated'),
            legacy_session_file=str(legacy),
            client_class=FakeClient,
        )
        self.assertEqual(second_pool.account_count(), 1)

    def test_registry_contains_no_cookie_secrets(self):
        self.add_account('private')
        registry = json.loads(self.pool.registry_file.read_text())
        self.assertNotIn('cookies', json.dumps(registry))

    def test_cross_process_refresh_pause_blocks_even_forced_refresh(self):
        self.add_account('quiet')
        self.pool.pause_background_refresh(self.pool.now_fn() + 60)
        self.assertEqual(self.pool.refresh_due_accounts(force=True), {})
        self.assertEqual(FakeClient.keep_alive_calls, [])

        self.pool.clear_background_refresh_pause()
        account_id = self.pool.list_accounts()[0]['id']
        self.assertEqual(
            self.pool.refresh_due_accounts(force=True),
            {account_id: True}
        )


if __name__ == '__main__':
    unittest.main()
