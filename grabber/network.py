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
        if req.get_method() not in ('GET', 'HEAD'):
            raise Error('Write request redirected. Refresh the repository before trying again.')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected:
            # Never forward credentials, even to another HTTPS host.
            redirected.remove_header('Authorization')
            redirected.remove_header('Private-token')
        return redirected


class Client:
    def __init__(self):
        self.opener = build_opener(Redirects())

    def open(self, url, accept='application/json', auth=False, method='GET', payload=None):
        https_url(url)
        headers = {'User-Agent': 'github-release-grabber/2', 'Accept': accept,
                   'Accept-Encoding': 'identity' if accept == 'application/octet-stream' else 'gzip'}
        host = urlsplit(url).netloc
        body = None
        if payload is not None:
            body = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if auth and host == 'api.github.com' and os.environ.get('GITHUB_TOKEN'):
            headers['Authorization'] = 'Bearer ' + os.environ['GITHUB_TOKEN']
        if auth and host == 'gitlab.com' and os.environ.get('GITLAB_TOKEN'):
            headers['PRIVATE-TOKEN'] = os.environ['GITLAB_TOKEN']
        try:
            return self.opener.open(Request(url, headers=headers, data=body, method=method), timeout=25)
        except HTTPError as exc:
            message = {403: 'Access denied or API rate limit reached',
                       404: 'Not found', 429: 'API rate limit reached; try again later'}.get(exc.code, exc.reason)
            try:
                details = json.loads(exc.read(8192))
                message = details.get('message') or details.get('error') or message
                if details.get('errors'):
                    message = f"{message}: {details['errors']}"
            except (ValueError, OSError, AttributeError):
                pass
            if exc.code == 401 and auth:
                token = 'GITHUB_TOKEN' if host == 'api.github.com' else 'GITLAB_TOKEN' if host == 'gitlab.com' else 'an API token'
                message = f'{message}. Authentication required; check {token}.'
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

    def write_json(self, url, method, payload):
        parts = urlsplit(https_url(url))
        if parts.netloc == 'api.github.com' and (parts.path.startswith('/repos/') or parts.path == '/graphql'):
            token = 'GITHUB_TOKEN'
        elif parts.netloc == 'gitlab.com' and parts.path.startswith('/api/v4/projects/'):
            token = 'GITLAB_TOKEN'
        else:
            raise Error('Write requests are restricted to GitHub and GitLab project APIs.')
        if method not in ('POST', 'PATCH', 'PUT', 'DELETE'):
            raise Error('Unsupported write method.')
        if not os.environ.get(token):
            raise Error(f'Set {token} with the required repository permissions first.')
        with self.open(url, auth=True, method=method, payload=payload) as response:
            stream = gzip.GzipFile(fileobj=response) if response.headers.get('Content-Encoding') == 'gzip' else response
            data = stream.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise Error('Write response too large. Refresh to check whether the action succeeded.')
            if not data:
                return {}
            try:
                return json.loads(data)
            except (ValueError, UnicodeError) as exc:
                raise Error('Invalid write response. Refresh to check whether the action succeeded.') from exc

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
