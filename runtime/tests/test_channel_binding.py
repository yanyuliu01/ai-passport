import threading
import unittest

from xiaoyu_runtime.channels.binding import Binding


class FakeClient:
    base = 'https://ilinkai.weixin.qq.com'

    def qr(self):
        return {'qrcode': 'private-qr', 'qrcode_img_content': 'private-image'}

    def qr_status(self, qr, verify_code=None):
        return {'status': 'confirmed', 'bot_token': 'private-token',
                'ilink_bot_id': 'private-bot', 'ilink_user_id': 'private-owner'}


class BindingTest(unittest.TestCase):
    def test_rebinding_does_not_accept_a_different_owner(self):
        binding = Binding(FakeClient(), expected_owner='another-owner')
        attempt = binding.start()['id']
        self.assertEqual(binding.poll(attempt), {'state': 'wrong_account'})
        with self.assertRaises(ValueError):
            binding.persist_credentials(attempt, lambda _: None)

    def test_verification_input_is_bounded(self):
        binding = Binding(FakeClient())
        attempt = binding.start()['id']
        for value in ('', 'x', '1' * 17, 123):
            with self.assertRaises(ValueError):
                binding.poll(attempt, value)

    def test_credentials_are_not_returned_in_public_status_and_consumed_once(self):
        binding = Binding(FakeClient())
        attempt = binding.start()['id']
        self.assertEqual(binding.poll(attempt), {'state': 'confirmed'})
        saved = []
        binding.persist_credentials(attempt, saved.append)
        self.assertEqual(saved[0]['token'], 'private-token')
        with self.assertRaises(ValueError):
            binding.persist_credentials(attempt, saved.append)

    def test_cancelled_attempt_cannot_commit_late_credentials(self):
        started, release = threading.Event(), threading.Event()
        class Slow(FakeClient):
            def qr_status(self, qr, verify_code=None):
                started.set()
                release.wait(2)
                return super().qr_status(qr, verify_code)
        binding = Binding(Slow())
        attempt = binding.start()['id']
        errors = []
        def poll():
            try:
                binding.poll(attempt)
            except ValueError as error:
                errors.append(str(error))
        thread = threading.Thread(target=poll)
        thread.start()
        self.assertTrue(started.wait(2))
        self.assertTrue(binding.cancel(attempt))
        release.set()
        thread.join(2)
        self.assertTrue(errors)
        with self.assertRaises(ValueError):
            binding.persist_credentials(attempt, lambda _: None)

    def test_untrusted_credential_destination_is_rejected(self):
        class Bad(FakeClient):
            def qr_status(self, qr, verify_code=None):
                return dict(super().qr_status(qr, verify_code), baseurl='https://evil.example')
        binding = Binding(Bad())
        attempt = binding.start()['id']
        with self.assertRaises(ValueError):
            binding.poll(attempt)
        with self.assertRaises(ValueError):
            binding.persist_credentials(attempt, lambda _: None)

    def test_late_wait_cannot_overwrite_confirmed_state(self):
        started, release = threading.Event(), threading.Event()
        class Reordered(FakeClient):
            calls = 0
            def qr_status(self, qr, verify_code=None):
                self.calls += 1
                if self.calls == 1:
                    started.set()
                    release.wait(2)
                    return {'status': 'wait'}
                return super().qr_status(qr, verify_code)
        binding = Binding(Reordered())
        attempt = binding.start()['id']
        first, second = [], []
        thread = threading.Thread(target=lambda: first.append(binding.poll(attempt)))
        thread.start()
        self.assertTrue(started.wait(2))
        later = threading.Thread(target=lambda: second.append(binding.poll(attempt)))
        later.start()
        release.set()
        thread.join(2)
        later.join(2)
        self.assertEqual(first, [{'state': 'waiting'}])
        self.assertEqual(second, [{'state': 'confirmed'}])
        saved = []
        binding.persist_credentials(attempt, saved.append)
        self.assertEqual(saved[0]['token'], 'private-token')

    def test_confirmation_is_not_lost_to_a_concurrent_wait_poll(self):
        started, release = threading.Event(), threading.Event()
        class ConfirmFirst(FakeClient):
            calls = 0
            def qr_status(self, qr, verify_code=None):
                self.calls += 1
                started.set()
                release.wait(2)
                return super().qr_status(qr, verify_code)
        client = ConfirmFirst()
        binding = Binding(client)
        attempt = binding.start()['id']
        results = []
        threads = [threading.Thread(target=lambda: results.append(binding.poll(attempt))) for _ in range(2)]
        threads[0].start()
        self.assertTrue(started.wait(2))
        threads[1].start()
        release.set()
        for thread in threads:
            thread.join(2)
        self.assertEqual(results, [{'state': 'confirmed'}, {'state': 'confirmed'}])
        self.assertEqual(client.calls, 1)
        saved = []
        binding.persist_credentials(attempt, saved.append)
        self.assertEqual(saved[0]['token'], 'private-token')

    def test_failed_credential_save_keeps_attempt_recoverable(self):
        binding = Binding(FakeClient())
        attempt = binding.start()['id']
        binding.poll(attempt)
        def fail(_):
            raise OSError('disk unavailable')
        with self.assertRaises(OSError):
            binding.persist_credentials(attempt, fail)
        saved = []
        binding.persist_credentials(attempt, saved.append)
        self.assertEqual(saved[0]['token'], 'private-token')

    def test_scanned_redirect_uses_only_trusted_weixin_host(self):
        class Redirect(FakeClient):
            def __init__(self):
                self.redirected = None
            def qr_status(self, qr, verify_code=None):
                if self.redirected:
                    return super().qr_status(qr, verify_code)
                return {'status': 'scaned_but_redirect', 'redirect_host': 'region.weixin.qq.com'}
            def for_base(self, base):
                self.redirected = base
                return self
        client = Redirect()
        binding = Binding(client)
        attempt = binding.start()['id']
        self.assertEqual(binding.poll(attempt), {'state': 'scanned'})
        self.assertEqual(client.redirected, 'https://region.weixin.qq.com')
        self.assertEqual(binding.poll(attempt), {'state': 'confirmed'})
        class Bad(Redirect):
            def qr_status(self, qr, verify_code=None):
                return {'status': 'scaned_but_redirect', 'redirect_host': 'evil.example'}
        binding = Binding(Bad())
        attempt = binding.start()['id']
        with self.assertRaises(ValueError):
            binding.poll(attempt)
