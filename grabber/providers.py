"""Provider adapters returning a common navigation model for the curses UI."""
from dataclasses import dataclass, field
from html.parser import HTMLParser
import json
import os
import posixpath
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import quote, urlencode, urljoin, urlsplit, urldefrag, unquote

from .network import Error

PAGE_SIZE = 30
GH = 'https://api.github.com'
GL = 'https://gitlab.com/api/v4'


@dataclass
class Item:
    title: str
    kind: str = 'text'
    data: dict = field(default_factory=dict)
    summary: str = ''


@dataclass
class View:
    title: str
    items: list = field(default_factory=list)
    text: str = ''
    links: list = field(default_factory=list)
    page: int = 1
    more: bool = False
    loader: object = None
    selected: int = 0
    scroll: int = 0
    context: dict = field(default_factory=dict)
    refresh: object = None
    markdown: bool = False
    preamble: str = ''
    base_url: str = ''
    diff: bool = False


def is_markdown(path):
    return path.lower().endswith(('.md', '.markdown', '.mdown', '.mkd', '.mkdn'))


def normalize(name):
    return re.sub(r'[-_.]+', '-', name).lower()


class PageText(HTMLParser):
    """Render static HTML as text, preserving selectable HTTPS links."""
    BLOCKS = {'p', 'div', 'br', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'pre',
              'section', 'article', 'header', 'footer', 'tr', 'blockquote', 'hr'}
    HIDDEN = {'script', 'style', 'noscript', 'template', 'svg'}

    def __init__(self, url):
        super().__init__(convert_charrefs=True)
        self.url = url
        self.parts = []
        self.links = []
        self.hidden = []
        self.anchor = None
        self.pre = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in self.HIDDEN:
            self.hidden.append(tag)
        if self.hidden:
            return
        if tag in self.BLOCKS:
            self.parts.append('\n')
        if tag == 'pre':
            self.pre = True
        if tag == 'a' and attrs.get('href'):
            target = urldefrag(urljoin(self.url, attrs['href']))[0]
            if urlsplit(target).scheme == 'https':
                self.anchor = (target, len(self.parts))
        if tag == 'img' and attrs.get('alt'):
            self.parts.append('[' + attrs['alt'] + ']')

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag == 'a' and self.anchor:
            target, start = self.anchor
            label = ''.join(self.parts[start:]).strip() or target
            self.links.append(Item(label, 'web', {'url': target}, target))
            self.parts.append(f' [{len(self.links)}]')
            self.anchor = None
        if tag in self.BLOCKS:
            self.parts.append('\n')
        if tag == 'pre':
            self.pre = False

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text if self.pre else re.sub(r'\s+', ' ', text))

    def text(self):
        return re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', ''.join(self.parts)).strip()


class Browser:
    def __init__(self, client, cache=None, progress=lambda message: None):
        self.client = client
        self.progress = progress
        self.cache = Path(cache) if cache else Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'github-release-grabber'
        self.names = None

    def search(self, provider, query, page=1):
        if not query.strip():
            raise Error('Please enter a search query.')
        loader = lambda number: self.search(provider, query, number)
        title = f'{provider}: {query} — Page {page}'
        if provider == 'PyPI':
            names = self.package_names()
            needle = normalize(query)
            matches = [name for name in names if needle in normalize(name)]
            matches.sort(key=lambda name: (normalize(name) != needle, not normalize(name).startswith(needle), name.lower()))
            start = (page - 1) * PAGE_SIZE
            return View(title, [Item(name, 'package', {'name': name}) for name in matches[start:start + PAGE_SIZE]],
                        page=page, more=start + PAGE_SIZE < len(matches), loader=loader)
        params = {'per_page': PAGE_SIZE, 'page': page}
        if provider == 'GitLab':
            params.update(search=query, simple='true', order_by='star_count', sort='desc')
            records = self.client.json(GL + '/projects?' + urlencode(params))
            items = [Item(record['path_with_namespace'], 'repo', {'provider': provider, 'repo': record}, record.get('description') or '') for record in records]
            more = len(records) == PAGE_SIZE
        else:
            # Pages has no global search API. Search repositories naming github.io;
            # direct URLs remain available through the UI's g command.
            params['q'] = query + (' github.io in:name' if provider == 'github.io' else '')
            response = self.client.json(GH + '/search/repositories?' + urlencode(params))
            records = response['items']
            items = [Item(record['full_name'], 'repo', {'provider': 'GitHub', 'repo': record}, record.get('description') or '') for record in records]
            more = page * PAGE_SIZE < min(response['total_count'], 1000)
            if response.get('incomplete_results'):
                title += ' (incomplete GitHub results)'
        return View(title, items, page=page, more=more, loader=loader)

    def package_names(self):
        if self.names is not None:
            return self.names
        path = self.cache / 'pypi-names.json'
        try:
            if time.time() - path.stat().st_mtime < 86400:
                names = json.loads(path.read_text())
                if isinstance(names, list) and all(isinstance(name, str) for name in names):
                    self.names = names
                    return names
        except (OSError, ValueError):
            pass
        self.progress('Loading PyPI package index (several MB on first use; cached for 24 hours) …')
        data = self.client.json('https://pypi.org/simple/',
                                accept='application/vnd.pypi.simple.v1+json', limit=128 * 1024 * 1024)
        self.names = [project['name'] for project in data['projects']]
        temporary = None
        try:
            self.cache.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', dir=self.cache, delete=False) as output:
                temporary = Path(output.name)
                json.dump(self.names, output)
            temporary.replace(path)
        except OSError:
            pass  # Searching still works with a read-only cache directory.
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        return self.names

    def repository(self, provider, repo):
        github = provider == 'GitHub'
        name = repo['full_name'] if github else repo['path_with_namespace']
        clone = repo['clone_url'] if github else repo['http_url_to_repo']
        data = {'provider': provider, 'repo': repo}
        items = [Item('View README', 'readme', data),
                 Item('Clone URL: ' + clone, 'text', {'text': clone}, clone),
                 Item('Browse files', 'tree', dict(data, path='')),
                 Item('Releases and downloads', 'releases', data)]
        from .forge import Forge
        forge = Forge(self, provider, repo)
        items.extend([forge.link('Issues', forge.listing, 'issue'),
                      forge.link('Pull requests' if github else 'Merge requests', forge.listing, 'pr')])
        items.append(Item('Clone repository…', 'clone', {'url': clone}, clone))
        homepage = repo.get('homepage')
        if github and name.split('/')[-1].lower().endswith('.github.io') and not homepage:
            homepage = 'https://' + name.split('/')[-1] + '/'
        if homepage:
            items.append(Item('Read website in terminal', 'web', {'url': homepage}, homepage))
        return View(name, items, preamble=repo.get('description') or 'No description.')

    def readme(self, provider, repo):
        if provider == 'GitHub':
            try:
                entry = self.client.json(self.repo_api(provider, repo) + '/readme')
            except Error as exc:
                if str(exc).startswith('HTTP 404:'):
                    raise Error('No README found, or repository is not accessible.') from exc
                raise
            return self.file(provider, repo, entry['path'])
        # GitLab search results do not include readme_url. Inspect all root
        # directory pages so repositories with many files work as well.
        page, candidates = 1, []
        while True:
            view = self.tree(provider, repo, page=page)
            candidates.extend(item.data['path'] for item in view.items
                              if item.kind == 'file' and
                              re.fullmatch(r'readme(?:\..+)?', item.data['path'], re.I))
            if not view.more:
                break
            page += 1
        if not candidates:
            raise Error('No README found in the repository root.')
        candidates.sort(key=lambda path: (not is_markdown(path), path.lower(), path))
        return self.file(provider, repo, candidates[0])

    def repo_api(self, provider, repo):
        return GH + '/repos/' + quote(repo['full_name'], safe='/') if provider == 'GitHub' else GL + '/projects/' + str(repo['id'])

    def releases(self, provider, repo, page=1):
        records = self.client.json(self.repo_api(provider, repo) + '/releases?' + urlencode({'per_page': PAGE_SIZE, 'page': page}))
        items = [Item((record.get('name') or record['tag_name']) + (' [prerelease]' if record.get('prerelease') else ''),
                      'release', {'provider': provider, 'release': record}, record['tag_name']) for record in records]
        return View('Releases', items, page=page, more=len(items) == PAGE_SIZE,
                    loader=lambda number: self.releases(provider, repo, number))

    def release(self, provider, release):
        items = [Item('Release description', 'text', {'text': release.get('body') or release.get('description') or 'No description.', 'markdown': True, 'base_url': release.get('html_url') or release.get('_links', {}).get('self', '')})]
        if provider == 'GitHub':
            assets = [(asset['name'], asset['browser_download_url']) for asset in release['assets']]
            assets += [('source.zip', release['zipball_url']), ('source.tar.gz', release['tarball_url'])]
        else:
            assets = [(asset['name'], asset.get('direct_asset_url') or asset['url']) for asset in release['assets'].get('links', [])]
            assets += [('source.' + asset['format'], asset['url']) for asset in release['assets'].get('sources', [])]
        items.extend(Item(name, 'download', {'name': name, 'url': url}, url) for name, url in assets if url)
        return View(release['tag_name'], items)

    def tree(self, provider, repo, path='', page=1):
        base = self.repo_api(provider, repo)
        if provider == 'GitHub':
            entries = self.client.json(base + '/contents/' + quote(path, safe='/'))
        else:
            entries = self.client.json(base + '/repository/tree?' + urlencode({'path': path, 'per_page': PAGE_SIZE, 'page': page}))
        if not isinstance(entries, list):
            raise Error('Not a directory.')
        items = []
        for entry in entries:
            folder = entry['type'] in ('dir', 'tree')
            data = {'provider': provider, 'repo': repo, 'path': entry['path']}
            items.append(Item(('[+] ' if folder else '    ') + entry['name'], 'tree' if folder else 'file', data))
        items.sort(key=lambda item: (item.kind != 'tree', item.title.lower()))
        title = path or '/'
        if provider == 'GitHub' and len(items) >= 1000:
            title += ' (GitHub limit: 1,000 entries)'
        return View(title, items, page=page, more=provider == 'GitLab' and len(items) == PAGE_SIZE,
                    loader=lambda number: self.tree(provider, repo, path, number))

    def file(self, provider, repo, path, anchor=''):
        base = self.repo_api(provider, repo)
        if provider == 'GitHub':
            url = base + '/contents/' + quote(path, safe='/')
            accept = 'application/vnd.github.raw+json'
        else:
            url = base + '/repository/files/' + quote(path, safe='') + '/raw?ref=HEAD'
            accept = 'text/plain'
        data, _, _ = self.client.read(url, accept, auth=True, limit=1024 * 1024)
        if b'\x00' in data:
            raise Error('Cannot display a binary file as text.')
        branch = quote(repo.get('default_branch') or 'HEAD', safe='')
        web_base = ('https://github.com/' + repo['full_name'] + '/blob/' if provider == 'GitHub' else
                    'https://gitlab.com/' + repo['path_with_namespace'] + '/-/blob/')
        return View(path, text=data.decode('utf-8', errors='replace'), markdown=is_markdown(path),
                    diff=path.lower().endswith(('.diff', '.patch')),
                    base_url=web_base + branch + '/' + quote(path, safe='/'),
                    context={'repo_file': (provider, repo, path), 'anchor': anchor})

    def package(self, name):
        record = self.client.json('https://pypi.org/pypi/' + quote(name, safe='') + '/json')
        info = record['info']
        preamble = f"{info['name']} {info['version']}\n{info.get('summary') or ''}\nPython: {info.get('requires_python') or 'not specified'}"
        items = [Item('Description', 'text', {'text': info.get('description') or '(No description.)',
                 'preamble': preamble, 'markdown': (info.get('description_content_type') or '').split(';')[0].strip() == 'text/markdown',
                 'base_url': info.get('package_url') or 'https://pypi.org/project/' + quote(name, safe='') + '/'}),
                 Item('Downloads: ' + info['version'], 'package-version', {'name': name, 'version': info['version']}),
                 Item('All versions', 'versions', {'name': name, 'releases': record.get('releases', {})})]
        for label, url in (info.get('project_urls') or {}).items():
            if url.startswith('https://'):
                items.append(Item(label, 'web', {'url': url}, url))
        return View(f"{info['name']} {info['version']}", items)

    def versions(self, name, releases):
        # Upload timestamps correctly order prereleases without a packaging dependency.
        versions = sorted(releases, key=lambda version: max((file.get('upload_time_iso_8601', '') for file in releases[version]), default=''), reverse=True)
        return View(name + ' — Versions', [Item(version, 'package-version', {'name': name, 'version': version}) for version in versions])

    def package_version(self, name, version):
        record = self.client.json('https://pypi.org/pypi/' + quote(name, safe='') + '/' + quote(version, safe='') + '/json')
        return View(f'{name} {version}', [Item(file['filename'] + (' [YANKED]' if file.get('yanked') else ''), 'download',
            {'name': file['filename'], 'url': file['url'], 'sha256': file.get('digests', {}).get('sha256', '')},
            f"{file['packagetype']} | {file.get('size', 0)} bytes | Python: {file.get('requires_python') or 'not specified'}") for file in record['urls']])

    def web(self, url):
        if '://' not in url:
            url = 'https://' + url
        data, headers, final_url = self.client.read(url, 'text/html,text/plain;q=0.9', limit=2 * 1024 * 1024)
        content_type = headers.get('Content-Type', '').lower()
        if not any(kind in content_type for kind in ('text/', 'application/xhtml+xml')):
            raise Error('This link is not a text or HTML page.')
        charset = headers.get_content_charset() or 'utf-8'
        try:
            text = data.decode(charset, errors='replace')
        except LookupError:
            text = data.decode('utf-8', errors='replace')
        if 'html' in content_type:
            parser = PageText(final_url)
            parser.feed(text)
            return View(final_url, text=parser.text() or '(No static text; this page may require JavaScript.)', links=parser.links)
        return View(final_url, text=text, markdown='text/markdown' in content_type or is_markdown(urlsplit(final_url).path), base_url=final_url)

    def markdown_link(self, view, link):
        parts = urlsplit(link.href)
        context = view.context.get('repo_file')
        if context and not parts.scheme and not parts.netloc and parts.path:
            provider, repo, path = context
            target = unquote(parts.path)
            target = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(posixpath.dirname(path), target))
            if target != '..' and not target.startswith('../'):
                kind = 'tree' if parts.path.endswith('/') else 'file'
                data = {'provider': provider, 'repo': repo, 'path': target}
                if kind == 'file':
                    data['anchor'] = unquote(parts.fragment)
                return Item(link.label, kind, data, target)
        return Item(link.label, 'web', {'url': link.url}, link.url)

    def activate(self, item):
        data = item.data
        if item.kind == 'call':
            return data['callback']()
        actions = {'repo': self.repository, 'tree': self.tree, 'file': self.file, 'readme': self.readme,
                   'releases': self.releases, 'release': self.release,
                   'package': self.package, 'versions': self.versions,
                   'package-version': self.package_version, 'web': self.web}
        if item.kind == 'text':
            return View(item.title, text=data.get('text') or item.title, markdown=data.get('markdown', False),
                        preamble=data.get('preamble', ''), base_url=data.get('base_url', ''),
                        diff=data.get('diff', False))
        if item.kind not in actions:
            raise Error('No action available.')
        return actions[item.kind](**data)
