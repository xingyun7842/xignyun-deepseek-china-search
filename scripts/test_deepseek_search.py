"""Offline contract tests. Uses fake credentials and mocked HTTP only."""
import argparse
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import deepseek_search as client


class SearchClientTests(unittest.TestCase):
    def test_parameter_boundaries(self):
        self.assertEqual(client.bounded_text('中' * 128), '中' * 128)
        for value in ('', '  ', '中' * 129):
            with self.assertRaises(argparse.ArgumentTypeError):
                client.bounded_text(value)
        self.assertEqual(client.url_value('https://example.com/正文'), 'https://example.com/正文')
        for value in ('file:///tmp/a', 'https://u:p@example.com', 'https://', 'https://[bad'):
            with self.assertRaises(argparse.ArgumentTypeError):
                client.url_value(value)

    def test_request_and_empty_results(self):
        response = io.BytesIO(b'{"results": []}')
        with patch.object(client.request, 'build_opener') as build:
            build.return_value.open.return_value = response
            self.assertEqual(client.call_api('search', {'query': '中国'}, 'fake-key'), {'results': []})
            req = build.return_value.open.call_args.args[0]
            self.assertEqual(req.full_url, client.BASE + 'search')
            self.assertEqual(req.get_header('Authorization'), 'Bearer fake-key')
            self.assertEqual(json.loads(req.data)['query'], '中国')

    def test_retry_bound_and_no_error_body_disclosure(self):
        with patch.object(client.request, 'build_opener') as build, patch.object(client.time, 'sleep') as sleep:
            build.return_value.open.side_effect = lambda *a, **k: (_ for _ in ()).throw(
                HTTPError(client.BASE, 429, 'busy', {}, io.BytesIO(b'private-value')))
            with self.assertRaisesRegex(ValueError, 'HTTP 429; attempts=3') as context:
                client.call_api('search', {'query': 'test'}, 'fake-key')
            self.assertNotIn('private-value', str(context.exception))
            self.assertEqual(build.return_value.open.call_count, 3)
            self.assertEqual(sleep.call_count, 2)

    def test_no_retry_for_credential_or_balance_errors(self):
        for code in (400, 401, 402, 403, 404, 302):
            with self.subTest(code=code), patch.object(client.request, 'build_opener') as build:
                build.return_value.open.side_effect = HTTPError(client.BASE, code, '', {}, None)
                with self.assertRaisesRegex(ValueError, f'HTTP {code}'):
                    client.call_api('search', {'query': 'test'}, 'fake-key')
                self.assertEqual(build.return_value.open.call_count, 1)

    def test_redirect_disabled(self):
        self.assertIsNone(client.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com'))

    def test_fetch_response_contract(self):
        with patch.object(client.request, 'build_opener') as build:
            build.return_value.open.return_value = io.BytesIO(b'{"title":"page", "content":"# Body"}')
            self.assertEqual(client.call_api('fetch', {'url': 'https://example.com'}, 'fake-key')['content'], '# Body')

    def test_dpapi_and_environment_priority(self):
        if client.os.name != 'nt':
            self.skipTest('Windows-only DPAPI')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.dpapi'
            with patch.object(client, 'KEY_FILE', path), patch.dict(client.os.environ, {}, clear=True), \
                 patch.object(client, 'windows_user_key', return_value=''), \
                 patch.object(client.sys.stdin, 'isatty', return_value=True), \
                 patch.object(client.getpass, 'getpass', return_value='fake-test-key'):
                self.assertTrue(client.configure()['configured'])
                self.assertNotIn(b'fake-test-key', path.read_bytes())
                self.assertEqual(client.load_key(), 'fake-test-key')
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    client.configure()
                with patch.dict(client.os.environ, {'DEEPSEEK_API_KEY': 'fake-env-key'}):
                    self.assertEqual(client.load_key(), 'fake-env-key')
                with patch.object(client.getpass, 'getpass', return_value='fake-replacement'):
                    client.configure(replace=True)
                self.assertEqual(client.load_key(), 'fake-replacement')

    def test_windows_user_environment_without_process_refresh(self):
        with patch.dict(client.os.environ, {}, clear=True), \
             patch.object(client, 'windows_user_key', return_value='fake-ui-key'):
            self.assertEqual(client.key_source(), 'Windows user environment')
            self.assertEqual(client.load_key(), 'fake-ui-key')
            with patch.dict(client.os.environ, {'DEEPSEEK_API_KEY': 'fake-process-key'}):
                self.assertEqual(client.key_source(), 'process environment')
                self.assertEqual(client.load_key(), 'fake-process-key')


if __name__ == '__main__':
    unittest.main()
