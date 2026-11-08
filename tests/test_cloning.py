"""Exercise cloning against a real local Git repository without network access."""
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from grabber.cloning import clone, repository_name
from grabber.network import Error


class CloneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        (self.source / 'README').write_text('clone fixture\n')
        subprocess.run(['git', '-C', str(self.source), 'add', 'README'], check=True)
        subprocess.run(['git', '-C', str(self.source), '-c', 'user.name=Test', '-c',
                        'user.email=test@example.org', 'commit', '-qm', 'Initial'], check=True)
        self.env = patch.dict(os.environ, {
            'GIT_CONFIG_COUNT': '1',
            'GIT_CONFIG_KEY_0': f'url.{self.source.as_uri()}.insteadOf',
            'GIT_CONFIG_VALUE_0': 'https://clone-test.invalid/repo.git',
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_clone_and_existing_destination(self):
        destination = self.root / 'checkout with spaces'
        result = clone('https://clone-test.invalid/repo.git', destination, lambda _: None, threading.Event())
        self.assertIn('Cloned to:', result)
        self.assertEqual((destination / 'README').read_text(), 'clone fixture\n')
        with self.assertRaisesRegex(Error, 'already exists'):
            clone('https://clone-test.invalid/repo.git', destination, lambda _: None, threading.Event())
        self.assertTrue((destination / 'README').exists())

    def test_failure_cleans_destination(self):
        destination = self.root / 'failed'
        with self.assertRaisesRegex(Error, 'Clone failed'):
            clone('https://clone-test.invalid/repo.git/missing', destination, lambda _: None, threading.Event())
        self.assertFalse(destination.exists())

    def test_cancellation_cleans_destination(self):
        destination = self.root / 'canceled'
        cancel = threading.Event()
        real_popen = subprocess.Popen
        def started(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            cancel.set()
            return process
        with patch('grabber.cloning.subprocess.Popen', side_effect=started):
            with self.assertRaisesRegex(Error, 'canceled'):
                clone('https://clone-test.invalid/repo.git', destination, lambda _: None, cancel)
        self.assertFalse(destination.exists())

    def test_url_formats(self):
        for url in ['https://github.com/owner/repo.git', 'git@gitlab.com:group/repo.git',
                    'ssh://git@example.org/group/repo.git', 'git://example.org/repo.git']:
            self.assertEqual(repository_name(url), 'repo')
        for url in ['--upload-pack=bad', 'ext::bad', 'file:///tmp/repo',
                    'https://example.org/%2Ftmp', 'https://user:secret@example.org/repo']:
            with self.subTest(url=url), self.assertRaises(Error):
                repository_name(url)

    def test_cli_clone(self):
        script = Path(__file__).resolve().parents[1] / 'github-release-grabber'
        destination = self.root / 'cli-clone'
        result = subprocess.run([str(script), 'clone', 'https://clone-test.invalid/repo.git', str(destination)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((destination / '.git').is_dir())

    def test_ui_clone_prompts(self):
        from grabber.providers import Item, View
        from grabber.tui import App
        app = App(None, self.root)
        app.stack.append(View('Results', [Item('owner/repo', 'repo', {'repo': {
            'clone_url': 'https://clone-test.invalid/repo.git'}})]))
        app.key('c')
        self.assertEqual(app.prompt_label, 'Clone to')
        self.assertEqual(app.prompt_value, str(self.root / 'repo'))
        app.key('\x1b')
        app.key('C')
        self.assertEqual(app.prompt_label, 'Git URL')


if __name__ == '__main__':
    unittest.main()
