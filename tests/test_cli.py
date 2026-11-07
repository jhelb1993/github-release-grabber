"""Offline CLI integration tests using real sh/jq and a fake GitHub transport."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'github-release-grabber'
CURL = r'''#!/usr/local/bin/python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as log:
    log.write(json.dumps(args) + '\n')
output = Path(args[args.index('--output') + 1])
if '--write-out' in args:
    output.write_text(os.environ['RESPONSE'])
    print(os.environ.get('HTTP_STATUS', '200'), end='')
else:
    output.write_bytes(b'release contents')
    if os.environ.get('FAIL_DOWNLOAD'):
        sys.exit(22)
'''


class CliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        binary = self.root / 'bin'
        binary.mkdir()
        curl = binary / 'curl'
        # Use the same Python interpreter as the test runner on any OS.
        import sys
        curl.write_text(CURL.replace('/usr/local/bin/python3', sys.executable, 1))
        curl.chmod(0o755)
        self.env = dict(os.environ, PATH=str(binary) + ':' + os.environ['PATH'],
                        CALLS=str(self.root / 'calls'), GITHUB_TOKEN='')
        self.release = {'assets': [
            {'name': 'app.zip', 'browser_download_url': 'https://github.com/a/b/app.zip'},
            {'name': 'app source.tar.gz', 'browser_download_url': 'https://github.com/a/b/source.tar.gz'}],
            'zipball_url': 'https://api.github.com/repos/a/b/zipball/v1',
            'tarball_url': 'https://api.github.com/repos/a/b/tarball/v1'}

    def run_cli(self, *args, response=None, stdin='', ok=True):
        self.env['RESPONSE'] = json.dumps(self.release if response is None else response)
        result = subprocess.run([str(SCRIPT), *args], cwd=self.root, env=self.env,
                                input=stdin, text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        self.assertEqual(list(self.root.glob('.github-release-grabber.*')), [])
        return result

    def calls(self):
        return [json.loads(line) for line in (self.root / 'calls').read_text().splitlines()]

    def test_search(self):
        result = self.run_cli('search', 'hello world', 'language:C', response={
            'items': [{'full_name': 'a/b', 'description': None,
                       'clone_url': 'https://github.com/a/b.git'}]})
        self.assertIn('a/b', result.stdout)
        self.assertIn('https://github.com/a/b.git', result.stdout)
        self.assertIn('q=hello world language:C', self.calls()[0])

    def test_empty_search(self):
        self.assertIn('No repositories', self.run_cli('search', 'nothing', response={'items': []}).stdout)

    def test_clone_url(self):
        self.assertEqual(self.run_cli('clone-url', 'a/b', response={
            'clone_url': 'https://github.com/a/b.git'}).stdout, 'https://github.com/a/b.git\n')

    def test_legacy_urls(self):
        self.assertEqual(self.run_cli('a/b').stdout.splitlines(),
                         [asset['browser_download_url'] for asset in self.release['assets']])

    def test_tag_encoding(self):
        self.run_cli('urls', 'a/b', 'release/v1+test')
        self.assertTrue(self.calls()[0][-1].endswith('/tags/release%2Fv1%2Btest'))

    def test_interactive_download(self):
        self.run_cli('download', 'a/b', stdin='2\n')
        self.assertEqual((self.root / 'app source.tar.gz').read_bytes(), b'release contents')

    def test_named_download_and_token(self):
        self.env['GITHUB_TOKEN'] = 'test-token'
        self.run_cli('download', 'a/b', 'app.zip', 'v1')
        calls = self.calls()
        self.assertIn('Authorization: Bearer test-token', calls[0])
        self.assertNotIn('Authorization: Bearer test-token', calls[1])
        self.assertTrue(calls[0][-1].endswith('/tags/v1'))

    def test_single_asset(self):
        self.release['assets'] = self.release['assets'][:1]
        self.run_cli('download', 'a/b')
        self.assertTrue((self.root / 'app.zip').exists())

    def test_source_fallback(self):
        self.release['assets'] = []
        self.run_cli('download', 'a/b', 'source.zip')
        self.assertTrue((self.root / 'source.zip').exists())

    def test_no_overwrite(self):
        target = self.root / 'app.zip'
        target.write_text('keep me')
        self.run_cli('download', 'a/b', 'app.zip', ok=False)
        self.assertEqual(target.read_text(), 'keep me')
        self.assertEqual(len(self.calls()), 1)

    def test_failed_download_cleanup(self):
        self.env['FAIL_DOWNLOAD'] = '1'
        self.run_cli('download', 'a/b', 'app.zip', ok=False)
        self.assertFalse((self.root / 'app.zip').exists())

    def test_invalid_selection(self):
        for value in ['0', '3', '-1', 'abc', '999999999999999999999999', '']:
            with self.subTest(value=value):
                self.run_cli('download', 'a/b', stdin=value + '\n', ok=False)

    def test_missing_asset(self):
        self.run_cli('download', 'a/b', 'absent.zip', ok=False)

    def test_unsafe_names(self):
        for name in ['../escape', '.', '..', 'bad\\name', 'line\nbreak']:
            self.release['assets'][0]['name'] = name
            with self.subTest(name=name):
                self.run_cli('download', 'a/b', name, ok=False)

    def test_http_errors(self):
        for status in ['404', '403', '429', '500']:
            self.env['HTTP_STATUS'] = status
            result = self.run_cli('urls', 'a/b', response={'message': 'Request denied'}, ok=False)
            self.assertIn(status, result.stderr)
            self.assertIn('Request denied', result.stderr)

    def test_invalid_arguments(self):
        for args in [[], ['unknown'], ['search'], ['clone-url', 'a/b', 'extra'],
                     ['urls', 'https://github.com/a/b'], ['urls', 'a/b/c'], ['urls', 'a/..']]:
            with self.subTest(args=args):
                self.run_cli(*args, ok=False)


if __name__ == '__main__':
    unittest.main()
