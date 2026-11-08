"""Offline provider, transport and curses navigation regression tests."""
import curses
from email.message import Message
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request

from grabber.network import Client, Error, Redirects
from grabber.providers import Browser, Item, PageText, View
from grabber.tui import App, clean


class Response(io.BytesIO):
    def __init__(self, data, content_type='application/json', url='https://example.github.io/'):
        super().__init__(data)
        self.headers = Message()
        self.headers['Content-Type'] = content_type
        self.headers['Content-Length'] = str(len(data))
        self.url = url

    def geturl(self):
        return self.url


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class ProviderTests(unittest.TestCase):
    def test_repository_description_and_readme_action(self):
        client = FakeClient({'path': 'docs/README.md'})
        browser = Browser(client)
        repo = {'full_name': 'a/b', 'clone_url': 'https://github.com/a/b.git',
                'description': 'Project description'}
        menu = browser.repository('GitHub', repo)
        self.assertEqual(menu.preamble, 'Project description')
        self.assertEqual(menu.items[0].title, 'View README')
        with patch.object(browser, 'file', return_value=View('README')) as read:
            browser.activate(menu.items[0])
            read.assert_called_once_with('GitHub', repo, 'docs/README.md')
        self.assertTrue(client.calls[0][0].endswith('/repos/a/b/readme'))

    def test_gitlab_readme_discovery_and_missing_readme(self):
        browser = Browser(FakeClient([]))
        repo = {'id': 1}
        first = View('/', [Item('README.txt', 'file', {'path': 'README.txt'})], more=True)
        second = View('/', [Item('ReadMe.md', 'file', {'path': 'ReadMe.md'})])
        with patch.object(browser, 'tree', side_effect=[first, second]) as tree, \
                patch.object(browser, 'file') as read:
            browser.readme('GitLab', repo)
            self.assertEqual(tree.call_count, 2)
            read.assert_called_once_with('GitLab', repo, 'ReadMe.md')
        with self.assertRaisesRegex(Error, 'No README found'):
            browser.readme('GitLab', repo)

    def test_search_query_encoding_and_pagination(self):
        client = FakeClient({'items': [{'full_name': 'a/b', 'description': 'A project'}], 'total_count': 31})
        browser = Browser(client)
        view = browser.search('GitHub', 'C++ topic:test')
        self.assertTrue(view.more)
        self.assertIn('q=C%2B%2B+topic%3Atest', client.calls[0][0])
        self.assertEqual(view.items[0].kind, 'repo')
        self.assertFalse(view.loader(2).more)

    def test_gitlab_search_and_repo(self):
        project = {'id': 17, 'path_with_namespace': 'group/sub/repo',
                   'http_url_to_repo': 'https://gitlab.com/group/sub/repo.git'}
        client = FakeClient([project])
        browser = Browser(client)
        view = browser.search('GitLab', 'hello world')
        self.assertIn('search=hello+world', client.calls[0][0])
        detail = browser.activate(view.items[0])
        self.assertIn(project['http_url_to_repo'], detail.items[1].title)
        self.assertEqual(browser.repo_api('GitLab', project), 'https://gitlab.com/api/v4/projects/17')

    def test_pages_search_and_website(self):
        client = FakeClient({'items': [], 'total_count': 0})
        browser = Browser(client)
        browser.search('github.io', 'docs')
        self.assertIn('github.io+in%3Aname', client.calls[0][0])
        view = browser.repository('GitHub', {'full_name': 'alice/alice.github.io', 'clone_url': 'https://github.com/alice/alice.github.io.git'})
        self.assertEqual(view.items[-1].data['url'], 'https://alice.github.io/')

    def test_repository_files(self):
        client = FakeClient([{'name': 'README.md', 'path': 'a b/README.md', 'type': 'file'},
                             {'name': 'src', 'path': 'src', 'type': 'dir'}])
        view = Browser(client).tree('GitHub', {'full_name': 'a/b'}, 'a b')
        self.assertEqual(view.items[0].kind, 'tree')
        self.assertTrue(client.calls[0][0].endswith('/contents/a%20b'))

    def test_release_assets(self):
        browser = Browser(FakeClient(None))
        github = browser.release('GitHub', {'tag_name': 'v1', 'body': 'Notes', 'assets': [
            {'name': 'app.zip', 'browser_download_url': 'https://example.org/app.zip'}],
            'zipball_url': 'https://example.org/src.zip', 'tarball_url': 'https://example.org/src.tar.gz'})
        self.assertEqual([item.kind for item in github.items], ['text', 'download', 'download', 'download'])
        gitlab = browser.release('GitLab', {'tag_name': 'v2', 'assets': {'links': [
            {'name': 'build', 'url': 'https://example.org/old', 'direct_asset_url': 'https://example.org/new'}],
            'sources': [{'format': 'tar.gz', 'url': 'https://example.org/source'}]}})
        self.assertEqual(gitlab.items[1].data['url'], 'https://example.org/new')

    def test_pypi_name_search_cache_and_ranking(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient({'projects': [{'name': name} for name in ['my_Requests', 'requests-tool', 'requests']]})
            browser = Browser(client, cache=directory)
            view = browser.search('PyPI', 'requests')
            self.assertEqual([item.title for item in view.items], ['requests', 'requests-tool', 'my_Requests'])
            browser.search('PyPI', 'my.requests')
            self.assertEqual(len(client.calls), 1)
            second = FakeClient(None)
            self.assertEqual(Browser(second, cache=directory).package_names(), browser.names)
            self.assertEqual(second.calls, [])

    def test_pypi_version_files_include_hash_and_yanked(self):
        client = FakeClient({'urls': [{'filename': 'pkg.whl', 'url': 'https://files.pythonhosted.org/pkg.whl',
            'digests': {'sha256': 'abcdef'}, 'packagetype': 'bdist_wheel', 'yanked': True}]})
        view = Browser(client).package_version('pkg', '1.0+test')
        self.assertEqual(view.items[0].data['sha256'], 'abcdef')
        self.assertIn('YANKED', view.items[0].title)
        self.assertIn('1.0%2Btest', client.calls[0][0])

    def test_html_text_and_links(self):
        parser = PageText('https://alice.github.io/docs/start')
        parser.feed('<h1>Hello</h1><script>bad()</script><style>hidden</style><p>Visit <a href="../guide">Guide</a></p><a href="javascript:bad()">no</a>')
        self.assertIn('Hello', parser.text())
        self.assertNotIn('bad()', parser.text())
        self.assertEqual(len(parser.links), 1)
        self.assertEqual(parser.links[0].data['url'], 'https://alice.github.io/guide')
        self.assertIn('[1]', parser.text())

    def test_webpage_uses_redirected_url_and_charset(self):
        client = Client()
        client.opener.open = lambda request, timeout: Response('<a href="next">Weiter</a>'.encode(), 'text/html; charset=utf-8', 'https://alice.github.io/docs/')
        view = Browser(client).web('alice.github.io')
        self.assertEqual(view.links[0].data['url'], 'https://alice.github.io/docs/next')
        self.assertEqual(view.title, 'https://alice.github.io/docs/')

    def test_binary_pages_rejected(self):
        client = Client()
        client.opener.open = lambda request, timeout: Response(b'PDF', 'application/pdf')
        with self.assertRaises(Error):
            Browser(client).web('https://example.org/file.pdf')


class NetworkTests(unittest.TestCase):
    def test_auth_scoping_and_redirects(self):
        seen = []
        client = Client()
        def opened(request, timeout):
            seen.append(dict(request.header_items()))
            return Response(b'{}')
        client.opener.open = opened
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'gh-secret', 'GITLAB_TOKEN': 'gl-secret'}):
            client.json('https://api.github.com/repos/a/b')
            client.json('https://gitlab.com/api/v4/projects')
            client.json('https://example.github.io/')
            client.read('https://gitlab.com/public-page', auth=False)
        self.assertEqual(seen[0]['Authorization'], 'Bearer gh-secret')
        self.assertEqual(seen[1]['Private-token'], 'gl-secret')
        self.assertNotIn('Authorization', seen[2])
        self.assertNotIn('Private-token', seen[3])
        request = Request('https://api.github.com/test', headers={'Authorization': 'secret'})
        redirected = Redirects().redirect_request(request, None, 302, '', {}, 'https://example.org/download')
        self.assertFalse(redirected.has_header('Authorization'))
        with self.assertRaises(Error):
            Redirects().redirect_request(request, None, 302, '', {}, 'http://example.org/download')

    def test_download_integrity_overwrite_cleanup_and_cancel(self):
        client = Client()
        client.opener.open = lambda request, timeout: Response(b'package bytes')
        expected = hashlib.sha256(b'package bytes').hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            cancel = threading.Event()
            client.download('https://example.org/pkg', 'pkg.whl', directory, lambda msg: None, cancel, expected)
            target = Path(directory) / 'pkg.whl'
            self.assertEqual(target.read_bytes(), b'package bytes')
            with self.assertRaises(Error):
                client.download('https://example.org/pkg', 'pkg.whl', directory, lambda msg: None, cancel)
            with self.assertRaises(Error):
                client.download('https://example.org/pkg', 'bad.whl', directory, lambda msg: None, cancel, 'incorrect')
            cancel.set()
            with self.assertRaises(Error):
                client.download('https://example.org/pkg', 'cancel.whl', directory, lambda msg: None, cancel)
            self.assertEqual([path.name for path in Path(directory).iterdir()], ['pkg.whl'])

    def test_unsafe_paths_and_urls(self):
        client = Client()
        for name in ['../escape', '.', '..', '/tmp/file', 'back\\slash', 'line\nbreak']:
            with self.subTest(name=name), self.assertRaises(Error):
                client.download('https://example.org/pkg', name, '/tmp', lambda msg: None, threading.Event())
        for url in ['file:///etc/passwd', 'http://example.org', 'https://user:password@example.org']:
            with self.subTest(url=url), self.assertRaises(Error):
                client.open(url)

    def test_response_size_limit(self):
        client = Client()
        client.opener.open = lambda request, timeout: Response(b'a' * 100)
        with self.assertRaises(Error):
            client.read('https://example.org', limit=20)


class Screen:
    def __init__(self):
        self.size = (24, 100)
        self.lines = {}
    def getmaxyx(self):
        return self.size
    def erase(self):
        self.lines = {}
    def addnstr(self, row, column, text, width, attr):
        self.lines[row] = text[:width]
    def refresh(self):
        pass


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.screen = Screen()
        self.app = App(self.screen, Path('/tmp'))

    def test_repository_description_above_menu_and_small_terminal(self):
        self.app.stack.append(View('repo', [Item('View README')] + [Item(str(i)) for i in range(20)],
                                   preamble='Project description'))
        self.app.draw()
        self.assertEqual(self.screen.lines[2].strip(), 'Project description')
        self.assertEqual(self.screen.lines[4].strip(), '> View README')
        self.screen.size = (10, 35)
        self.app.view.preamble = 'A long description ' * 100
        self.app.key(curses.KEY_END)
        self.app.draw()
        self.assertIn('…', self.screen.lines[2])
        self.assertTrue(any('> 19' in line for line in self.screen.lines.values()))

    def test_tabs_prompt_and_back(self):
        self.app.key('\t')
        self.assertEqual(self.app.provider, 1)
        self.app.key('3')
        self.assertEqual(self.app.provider, 2)
        self.app.key('/')
        for char in 'requests':
            self.app.key(char)
        self.assertEqual(self.app.prompt_value, 'requests')
        self.app.key('\x1b')
        self.assertIsNone(self.app.prompt_label)
        self.app.key('?')
        self.assertEqual(len(self.app.stack), 2)
        self.app.key('b')
        self.assertEqual(len(self.app.stack), 1)

    def test_list_and_text_navigation_and_resize(self):
        self.app.stack.append(View('results', [Item(str(i)) for i in range(80)]))
        self.app.key(curses.KEY_END)
        self.app.draw()
        self.assertEqual(self.app.view.selected, 79)
        self.assertGreater(self.app.view.scroll, 0)
        self.app.stack.append(View('text', text='\n'.join(str(i) for i in range(100))))
        self.app.key(curses.KEY_NPAGE)
        self.app.draw()
        self.assertGreater(self.app.view.scroll, 0)
        self.screen.size = (3, 20)
        self.app.draw()
        self.assertIn('Terminal', self.screen.lines[0])

    def test_web_links_and_download_action(self):
        link = Item('next', 'web', {'url': 'https://example.org/next'})
        self.app.stack.append(View('page', text='content', links=[link]))
        self.app.key('l')
        self.assertEqual(self.app.view.items, [link])
        item = Item('pkg.whl', 'download', {'name': 'pkg.whl', 'url': 'https://example.org/pkg.whl'})
        self.app.stack.append(View('files', [item]))
        with patch.object(self.app, 'download') as download:
            self.app.key('\n')
            download.assert_not_called()
            self.app.key('b')
            self.app.key('d')
            download.assert_called_once_with(item)

    def test_background_success_and_error_keep_ui_alive(self):
        self.app.start(lambda: View('loaded', [Item('entry')]))
        self.app.worker.join()
        self.app.poll()
        self.assertFalse(self.app.busy)
        self.assertEqual(self.app.view.title, 'loaded')
        def failed():
            raise Error('Rate limited')
        self.app.start(failed)
        self.app.worker.join()
        self.app.poll()
        self.assertIn('Rate limited', self.app.status)
        self.assertEqual(self.app.view.title, 'loaded')

    def test_control_characters_sanitized(self):
        self.assertNotIn('\x1b', clean('bad\x1b[31m'))
        self.assertNotIn('\u202e', clean('name\u202e'))


if __name__ == '__main__':
    unittest.main()
