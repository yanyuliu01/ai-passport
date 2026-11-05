import base64
import io
import urllib.error
import unittest
from unittest.mock import patch

from xiaoyu_runtime.entertainment import browser
from xiaoyu_runtime.entertainment.network import Unavailable


class BrowserTransportTest(unittest.TestCase):
    def test_login_status_does_not_make_a_platform_request(self):
        with patch.object(browser, '_call', return_value={'logged_in': True}) as call:
            self.assertTrue(browser.logged_in())
            call.assert_called_once_with({'op': 'status'})
        with patch.object(browser, '_call', side_effect=Unavailable('offline')):
            self.assertFalse(browser.logged_in())

    def test_session_response_and_restriction_status(self):
        url = 'https://api.bilibili.com/x/web-interface/view'
        with patch.object(browser, '_call', return_value={'status': 200, 'body': base64.b64encode(b'hello').decode(), 'location': ''}):
            with browser.open_session(url) as result:
                self.assertEqual(result.read(), b'hello')
        with patch.object(browser, '_call', return_value={'status': 412, 'body': '', 'location': ''}):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                browser.open_session(url)
            self.assertEqual(caught.exception.code, 412)

    def test_missing_session_fails_without_reading_content(self):
        with patch.object(browser, '_call', return_value={'error': 'login_required'}):
            with self.assertRaisesRegex(Unavailable, '登录'):
                browser.open_session('https://api.bilibili.com/x/web-interface/view')


if __name__ == '__main__':
    unittest.main()
