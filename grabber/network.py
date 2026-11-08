"""HTTPS transport and atomic downloads, shared by all terminal providers."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class Error(Exception):
    """An error suitable for display in the terminal."""


def https_url(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise Error('Only HTTPS URLs without embedded credentials are supported.')
    return url


class Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected:
            # Never forward credentials, even to another HTTPS host.
            redirected.remove_header('Authorization')
            redirected.remove_header('Private-token')
        return redirected


class Client:
    def __init__(self):
        self.opener = build_opener(Redirects())

    def open(self, url, accept='application/json', auth=False):
        https_url(url)
        headers = {'User-Agent': 'github-release-grabber/2', 'Accept': accept,
                   'Accept-Encoding': 'identity' if accept == 'application/octet-stream' else 'gzip'}
        host = urlsplit(url).netloc
        if auth and host == 'api.github.com' and os.environ.get('GITHUB_TOKEN'):
            headers['Authorization'] = 'Bearer ' + os.environ['GITHUB_TOKEN']
        if auth and host == 'gitlab.com' and os.environ.get('GITLAB_TOKEN'):
            headers['PRIVATE-TOKEN'] = os.environ['GITLAB_TOKEN']
        try:
            return self.opener.open(Request(url, headers=headers), timeout=25)
        except HTTPError as exc:
            message = {403: 'Access denied or API rate limit reached',
                       404: 'Not found', 429: 'API rate limit reached; try again later'}.get(exc.code, exc.reason)
            exc.close()
            raise Error(f'HTTP {exc.code}: {message}') from exc
        except (URLError, OSError) as exc:
            raise Error(f'Network error: {exc}') from exc

    def read(self, url, accept='application/json', auth=False, limit=8 * 1024 * 1024):
        with self.open(url, accept, auth) as response:
            stream = gzip.GzipFile(fileobj=response) if response.headers.get('Content-Encoding') == 'gzip' else response
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise Error('Response too large for the text viewer.')
            return data, response.headers, response.geturl()

    def json(self, url, accept='application/json', limit=8 * 1024 * 1024):
        data, _, _ = self.read(url, accept, auth=True, limit=limit)
        try:
            return json.loads(data)
        except (ValueError, UnicodeError) as exc:
            raise Error('The server returned invalid JSON.') from exc

    def download(self, url, name, directory, progress, cancel, sha256=''):
        if not name or name in ('.', '..') or any(c in '/\\' or ord(c) < 32 or ord(c) == 127 for c in name):
            raise Error('Invalid filename.')
        target = Path(directory) / name
        if os.path.lexists(target):
            raise Error(f'File already exists: {target}')
        temporary = None
        try:
            with self.open(url, 'application/octet-stream') as response:
                # Downloads preserve the exact bytes supplied by the artifact endpoint.
                with tempfile.NamedTemporaryFile(dir=directory, prefix='.grabber-', delete=False) as output:
                    temporary = Path(output.name)
                    digest = hashlib.sha256()
                    count = 0
                    while True:
                        if cancel.is_set():
                            raise Error('Download canceled.')
                        block = response.read(128 * 1024)
                        if not block:
                            break
                        output.write(block)
                        digest.update(block)
                        count += len(block)
                        progress(f'Download: {name} — {count // 1024} KiB')
                expected = response.headers.get('Content-Length')
                if expected and count != int(expected):
                    raise Error('Incomplete download.')
                if sha256 and digest.hexdigest().lower() != sha256.lower():
                    raise Error('SHA256 checksum mismatch.')
                if cancel.is_set():
                    raise Error('Download canceled.')
                os.link(temporary, target)  # Atomic; fails if another file appeared meanwhile.
            return f'Saved: {target}'
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
