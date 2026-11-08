"""Clone Git URLs without blocking the terminal or overwriting directories."""
import argparse
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from urllib.parse import unquote, urlsplit

from .network import Error


def repository_name(url):
    if not url or any(ord(char) < 32 for char in url) or url.startswith('-'):
        raise Error('Enter a valid Git URL.')
    if '://' in url:
        parts = urlsplit(url)
        if parts.scheme not in ('https', 'http', 'ssh', 'git') or not parts.hostname:
            raise Error('Use an HTTPS, SSH or git:// repository URL.')
        if parts.password:
            raise Error('Use a Git credential helper instead of a password in the URL.')
        path = parts.path
    else:
        match = re.fullmatch(r'(?:[\w.-]+@)?[\w.-]+:(.+)', url)
        if not match or match.group(1).startswith(':'):
            raise Error('Use a Git URL, for example https://github.com/owner/repo.git or git@host:owner/repo.git.')
        path = match.group(1)
    name = unquote(path.rstrip('/').rsplit('/', 1)[-1])
    if name.endswith('.git'):
        name = name[:-4]
    if not name or name in ('.', '..') or any(char in '/\\' or ord(char) < 32 for char in name):
        raise Error('Cannot determine a directory name from this Git URL.')
    return name


def clone(url, destination, progress, cancel):
    repository_name(url)
    if not shutil.which('git'):
        raise Error('Git is required to clone repositories (OpenBSD: pkg_add git).')
    destination = Path(destination).expanduser().absolute()
    if os.path.lexists(destination):
        raise Error(f'Destination already exists: {destination}')
    if not destination.parent.is_dir():
        raise Error('The parent directory must already exist.')
    if cancel.is_set():
        raise Error('Clone canceled.')
    # Reserve the destination. Git accepts an empty directory; we only remove
    # this directory on failure, never an existing user directory.
    destination.mkdir()
    succeeded = False
    process = None
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    # Avoid credential/host-key prompts taking over curses; configured SSH keys
    # and agents still work. Known-host verification remains enabled.
    env['GIT_SSH_COMMAND'] = env.get('GIT_SSH_COMMAND', 'ssh') + ' -oBatchMode=yes'
    try:
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen(['git', 'clone', '--progress', '--', url, str(destination)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env, start_new_session=True)
            while process.poll() is None:
                if cancel.wait(0.1):
                    raise Error('Clone canceled.')
                # pread leaves Git's output offset intact.
                size = os.fstat(log.fileno()).st_size
                tail = os.pread(log.fileno(), min(size, 4096), max(0, size - 4096))
                lines = tail.decode('utf-8', errors='replace').replace('\r', '\n').splitlines()
                if lines:
                    progress(lines[-1])
            if cancel.is_set():
                raise Error('Clone canceled.')
            if process.returncode:
                size = os.fstat(log.fileno()).st_size
                tail = os.pread(log.fileno(), min(size, 4096), max(0, size - 4096)).decode('utf-8', errors='replace')
                lines = tail.replace('\r', '\n').splitlines()
                detail = next((line for line in reversed(lines) if line.startswith(('fatal:', 'error:'))), lines[-1] if lines else 'Git failed.')
                raise Error(f'Clone failed: {detail}')
            succeeded = True
            return f'Cloned to: {destination}'
    finally:
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        if not succeeded and destination.exists():
            shutil.rmtree(destination)


def main():
    parser = argparse.ArgumentParser(description='Clone a Git URL into a new directory.')
    parser.add_argument('url')
    parser.add_argument('destination', nargs='?')
    args = parser.parse_args()
    try:
        destination = args.destination or repository_name(args.url)
        print(clone(args.url, destination, lambda message: None, threading.Event()))
    except (Error, OSError, ValueError) as exc:
        parser.exit(1, f'Error: {exc}\n')
    except KeyboardInterrupt:
        parser.exit(130, 'Clone canceled.\n')


if __name__ == '__main__':
    main()
