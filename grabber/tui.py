"""Curses navigation; all network work runs outside the terminal event loop."""
import argparse
import curses
import locale
from pathlib import Path
import queue
import sys
import textwrap
import threading
import time
import unicodedata

from .network import Client, Error
from .cloning import clone, repository_name
from .providers import Browser, View

PROVIDERS = ('GitHub', 'GitLab', 'PyPI', 'github.io')


def clean(text):
    return ''.join(char if char == '\n' or not unicodedata.category(char).startswith('C') else ' ' for char in str(text)).expandtabs(4)


def wrapped(text, width):
    return [part for line in clean(text).split('\n') for part in (textwrap.wrap(line, max(1, width), replace_whitespace=False) or [''])]


class App:
    def __init__(self, screen, directory, provider='GitHub'):
        self.screen = screen
        self.directory = directory
        self.provider = PROVIDERS.index(provider)
        self.events = queue.Queue()
        self.client = Client()
        self.browser = Browser(self.client, progress=lambda message: self.events.put(('status', message)))
        self.stacks = [[self.home(name)] for name in PROVIDERS]
        self.status = '/ Search · g Read HTTPS page · ? Help'
        self.busy = False
        self.cancel = threading.Event()
        self.worker = None
        self.prompt_label = None
        self.prompt_value = ''
        self.prompt_action = None
        self.replace = False
        self.text_view = None
        self.text_width = 0
        self.text_lines = []

    def home(self, provider):
        hints = {'GitHub': 'Search repositories, read files and download releases.',
                 'GitLab': 'Browse public projects on gitlab.com.',
                 'PyPI': 'Search package names; browse descriptions, versions and files.\nThe first search loads the PyPI package index (several MB; cached for 24 hours).',
                 'github.io': 'Press g to read a github.io URL directly.\nPress / to search repositories with github.io in their names.\nProject sites with other repository names can be opened by URL.'}
        return View(provider, text=hints[provider] + '\n\n/ Search   Tab Switch provider   g Open URL   ? Help')

    @property
    def stack(self):
        return self.stacks[self.provider]

    @property
    def view(self):
        return self.stack[-1]

    def start(self, work, replace=False):
        if self.busy:
            return
        self.busy = True
        self.replace = replace
        self.cancel.clear()
        self.status = 'Loading … (q quits, Esc cancels downloads/clones)'

        def run():
            try:
                self.events.put(('result', work()))
            except Exception as exc:
                self.events.put(('error', str(exc) or type(exc).__name__))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def poll(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'status':
                self.status = value
            else:
                self.busy = False
                if kind == 'error':
                    self.status = 'Error: ' + value
                elif isinstance(value, View):
                    if self.replace:
                        self.stack[-1] = value
                    else:
                        self.stack.append(value)
                    self.status = 'No results found.' if not value.text and not value.items else 'Ready.'
                else:
                    self.status = value

    def put(self, row, text, attr=0):
        height, width = self.screen.getmaxyx()
        if 0 <= row < height and width > 0:
            # Pad by terminal cells, not Python characters (CJK can occupy two).
            cells = 0
            visible = []
            for char in clean(text).replace('\n', ' '):
                size = 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
                if cells + size > width:
                    break
                visible.append(char)
                cells += size
            line = ''.join(visible) + ' ' * (width - cells)
            try:
                self.screen.addnstr(row, 0, line, len(line), attr)
            except curses.error:
                # Curses reports ERR after writing the bottom-right cell; the
                # cell is still painted. Keep the full final column usable.
                pass

    def draw(self):
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        if height < 10 or width < 35:
            self.put(0, 'Terminal too small (min. 35x10).')
            self.put(1, 'q quits')
            self.screen.refresh()
            return
        labels = [f'[{name}]' if index == self.provider else name for index, name in enumerate(PROVIDERS)]
        extra, remainder = divmod(max(0, width - sum(map(len, labels))), len(labels))
        tabs = ''.join(label.center(len(label) + extra + (index < remainder))
                       for index, label in enumerate(labels))
        self.put(0, tabs, curses.A_REVERSE)
        self.put(1, self.view.title, curses.A_BOLD)
        available = height - 6
        if self.view.text:
            if self.text_view is not self.view or self.text_width != width:
                self.text_lines = wrapped(self.view.text, width)
                self.text_view, self.text_width = self.view, width
            lines = self.text_lines
            self.view.scroll = max(0, min(self.view.scroll, max(0, len(lines) - available)))
            for row, line in enumerate(lines[self.view.scroll:self.view.scroll + available], 2):
                self.put(row, line)
            self.put(height - 4, f'Line {self.view.scroll + 1}/{len(lines)} · {len(self.view.links)} Links (l)')
        else:
            count = len(self.view.items)
            self.view.selected = max(0, min(self.view.selected, count - 1))
            if self.view.selected < self.view.scroll:
                self.view.scroll = self.view.selected
            if self.view.selected >= self.view.scroll + available:
                self.view.scroll = self.view.selected - available + 1
            for index in range(self.view.scroll, min(count, self.view.scroll + available)):
                item = self.view.items[index]
                selected = index == self.view.selected
                self.put(2 + index - self.view.scroll, ('> ' if selected else '  ') + item.title, curses.A_REVERSE if selected else 0)
            summary = self.view.items[self.view.selected].summary if count else 'No entries.'
            self.put(height - 4, summary)
        if self.prompt_label:
            self.put(height - 3, self.prompt_label + ': ' + self.prompt_value[-max(5, width - len(self.prompt_label) - 4):], curses.A_BOLD)
        else:
            prefix = '|/-\\'[int(time.monotonic() * 5) % 4] + ' ' if self.busy else ''
            self.put(height - 3, prefix + self.status)
        self.put(height - 2, '↑↓/jk Move · Enter Open · Esc Back · / Search · Tab Provider')
        self.put(height - 1, 'n/p Page · l Links · g URL · c Clone · d Download · ? Help · q Quit', curses.A_REVERSE)
        self.screen.refresh()

    def prompt(self, label, action, value=''):
        self.prompt_label, self.prompt_action, self.prompt_value = label, action, value

    def edit_prompt(self, key):
        if key == '\x1b':
            self.prompt_label = None
        elif key in ('\n', '\r', curses.KEY_ENTER):
            value = self.prompt_value.strip()
            action = self.prompt_action
            self.prompt_label = None
            if value:
                action(value)
        elif key in (curses.KEY_BACKSPACE, '\x7f', '\b'):
            self.prompt_value = self.prompt_value[:-1]
        elif key == '\x15':
            self.prompt_value = ''
        elif isinstance(key, str) and key.isprintable() and len(self.prompt_value) < 2048:
            self.prompt_value += key

    def search(self, query):
        self.start(lambda: self.browser.search(PROVIDERS[self.provider], query))

    def download(self, item):
        data = item.data
        self.start(lambda: self.client.download(data['url'], data['name'], self.directory,
            lambda message: self.events.put(('status', message)), self.cancel, data.get('sha256', '')))

    def clone_url(self, url):
        try:
            destination = str(self.directory / repository_name(url))
        except (Error, ValueError) as exc:
            self.status = 'Error: ' + str(exc)
            return
        self.prompt('Clone to', lambda target: self.start(lambda: clone(
            url, target, lambda message: self.events.put(('status', message)), self.cancel)), destination)

    def clone_selected(self):
        if self.view.items:
            item = self.view.items[self.view.selected]
            if item.kind == 'repo':
                repo = item.data['repo']
                self.clone_url(repo.get('clone_url') or repo['http_url_to_repo'])
                return
            if item.kind == 'clone':
                self.clone_url(item.data['url'])
                return
        self.prompt('Git URL', self.clone_url)

    def key(self, key):
        if self.prompt_label:
            self.edit_prompt(key)
            return True
        if key in ('q', '\x03'):
            self.cancel.set()
            return False
        if self.busy:
            if key == '\x1b':
                self.cancel.set()
                self.status = 'Cancellation requested; waiting for the current network response.'
            return True
        if key in ('\t', curses.KEY_BTAB):
            self.provider = (self.provider + (-1 if key == curses.KEY_BTAB else 1)) % len(PROVIDERS)
        elif key in ('1', '2', '3', '4'):
            self.provider = int(key) - 1
        elif key in ('\x1b', curses.KEY_BACKSPACE, '\x7f', 'b'):
            if len(self.stack) > 1:
                self.stack.pop()
        elif key == '/':
            self.prompt('Search', self.search)
        elif key == 'c':
            self.clone_selected()
        elif key == 'C':
            self.prompt('Git URL', self.clone_url)
        elif key == 'g':
            self.prompt('HTTPS URL', lambda url: self.start(lambda: self.browser.web(url)))
        elif key == 'l' and self.view.links:
            self.stack.append(View('Links: ' + self.view.title, self.view.links))
        elif key == '?':
            self.stack.append(View('Help', text=(
                'Tab / Shift-Tab or 1–4: switch provider\n'
                '/: enter query, Enter to search, Esc to cancel, Ctrl-U to clear\n'
                '↑/↓ or j/k: move selection or scroll text\n'
                'Page Up/Down: scroll a page; Home/End: jump to start/end\n'
                'Enter: open project, directory, file, version or link\n'
                'Enter on a downloadable file: show download details\n'
                'd on a downloadable file: save to the download directory\n'
                'c: clone selected repository; C: enter a Git URL\n'
                'Choose a new destination directory, then Enter to clone.\n'
                'n/p: next/previous results page\n'
                'Esc, b or Backspace: go back\n'
                'g: read an HTTPS page as text; l: list page links\n'
                'q: quit; Esc during a download or clone: cancel\n\n'
                f'Download directory: {self.directory}\n'
                'Existing files are never overwritten. PyPI files are verified using SHA256.\n'
                'Packages are downloaded, not installed.\n'
                'Web pages: static HTML/text, no JavaScript or forms.\n'
                'GitHub/GitLab: default branch files; public downloads.\n'
                'github.io search: repository names, not a complete website index.')))
        elif key in ('n', 'p') and self.view.loader:
            page = self.view.page + (1 if key == 'n' else -1)
            if page > 0 and (key == 'p' or self.view.more):
                loader = self.view.loader
                self.start(lambda: loader(page), replace=True)
        elif key in (curses.KEY_UP, 'k', curses.KEY_DOWN, 'j', curses.KEY_NPAGE, curses.KEY_PPAGE, curses.KEY_HOME, curses.KEY_END):
            step = max(1, self.screen.getmaxyx()[0] - 6)
            delta = {curses.KEY_UP: -1, 'k': -1, curses.KEY_DOWN: 1, 'j': 1,
                     curses.KEY_NPAGE: step, curses.KEY_PPAGE: -step}.get(key, 0)
            if self.view.text:
                self.view.scroll = (0 if key == curses.KEY_HOME else 10**9 if key == curses.KEY_END else max(0, self.view.scroll + delta))
            else:
                self.view.selected = max(0, min(len(self.view.items) - 1,
                    0 if key == curses.KEY_HOME else len(self.view.items) - 1 if key == curses.KEY_END else self.view.selected + delta))
        elif key in ('\n', '\r', curses.KEY_ENTER, 'd') and self.view.items:
            item = self.view.items[self.view.selected]
            if item.kind == 'clone' and key != 'd':
                self.clone_url(item.data['url'])
            elif item.kind == 'download':
                if key == 'd':
                    self.download(item)
                else:
                    self.stack.append(View(item.title, text=f"File: {item.data['name']}\nURL: {item.data['url']}\nDestination: {self.directory}\n\nPress Esc to go back, then d to download."))
            elif key != 'd':
                self.start(lambda: self.browser.activate(item))
        return True

    def run(self):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.screen.keypad(True)
        self.screen.timeout(100)
        try:
            while True:
                self.poll()
                self.draw()
                try:
                    key = self.screen.get_wch()
                except curses.error:
                    continue
                if not self.key(key):
                    break
        finally:
            self.cancel.set()
            if self.worker and self.worker.is_alive():
                # Downloads clean their staging file on cancellation. Network operations
                # have a 25-second socket timeout; don't leave a daemon writing on exit.
                self.status = 'Waiting for the current request to finish …'
                self.draw()
                self.worker.join()


def main():
    parser = argparse.ArgumentParser(description='Browse GitHub, GitLab, PyPI and github.io in a curses terminal.')
    parser.add_argument('--provider', choices=PROVIDERS, default='GitHub')
    parser.add_argument('--download-dir', type=Path, default=Path.cwd())
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(1, 'The curses interface requires an interactive terminal.\n')
    if not args.download_dir.is_dir():
        parser.exit(1, 'The download directory must already exist.\n')
    locale.setlocale(locale.LC_ALL, '')
    try:
        curses.wrapper(lambda screen: App(screen, args.download_dir.resolve(), args.provider).run())
    except KeyboardInterrupt:
        pass
    except (curses.error, Error, OSError) as exc:
        parser.exit(1, f'Terminal error: {exc}\n')


if __name__ == '__main__':
    main()
