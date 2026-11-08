# github-release-grabber

Browse GitHub, GitLab, PyPI packages and github.io pages in a curses terminal.
Search repositories, read source files, inspect releases and download artifacts.
Web pages are rendered as text with navigable links, entirely inside the terminal.
The original OpenBSD shell commands remain available.

## Requirements

The curses interface requires Python 3 with its standard `curses` module.
The shell commands require `curl` and `jq`. On OpenBSD, install as root:

```sh
pkg_add python3 curl jq git
```

## Curses interface

Run from an interactive terminal:

```sh
./github-release-grabber tui
# With no arguments, the interface also starts automatically in a terminal:
./github-release-grabber

./github-release-grabber tui --provider PyPI
./github-release-grabber tui --download-dir /path/to/existing/directory
```

Keep the `grabber/` Python package alongside the launcher when copying this
project. No third-party Python packages or external browser are required.
Use a UTF-8 terminal, preferably at least 80 columns wide.

| Key | Action |
| --- | --- |
| Tab / Shift-Tab, or 1–4 | Switch between GitHub, GitLab, PyPI, github.io |
| `/` | Search the current provider |
| Up/Down or `j`/`k` | Move selection or scroll text |
| Page Up/Down, Home/End | Scroll further or jump to beginning/end |
| Enter | Open selected project, file, release, package or link |
| `d` | Download the selected artifact |
| `c` / `C` | Clone the selected repository / enter a Git URL |
| `n` / `p` | Next / previous results page |
| `g` | Enter an HTTPS URL to read in the terminal |
| `l` | List and follow links from the current web page |
| Esc, Backspace or `b` | Go back; Esc cancels an input prompt |
| `?` | Help |
| `q` | Quit |

### What you can browse

- **GitHub:** repository search, clone URLs, descriptions, directories and text
  files on the default branch, releases including prereleases, assets and source
  archives. Searches offer up to GitHub's first 1,000 results. Directory listings
  are subject to GitHub's 1,000-entry limit.
- **GitLab:** public projects on gitlab.com, clone URLs, directories and files,
  releases, release links and source archives. Search and listings are paginated.
- **PyPI:** package-name substring search, package descriptions, versions, wheels
  and source distributions. The first search downloads the official package-name
  index (several MB). It is cached for 24 hours in
  `$XDG_CACHE_HOME/github-release-grabber/`, or
  `~/.cache/github-release-grabber/`. This is a name search, not a full-text search
  through package descriptions. Downloaded packages are not installed.
- **github.io:** search repository names containing `github.io`, open a project's
  website, or enter a URL directly with `g`, for example
  `https://jqlang.github.io/jq/`. This search is not a complete index of GitHub
  Pages: project sites whose repository names do not contain `github.io` can be
  opened by URL. Static HTML is displayed as text; `l` opens the numbered link
  list. No external browser is launched. JavaScript, forms, images and CSS layout
  are not rendered. Other HTTPS text pages can be followed too.

Downloads go into the directory from which you launched the program unless
`--download-dir` is supplied. Existing files are never overwritten. Files are
staged until complete, and PyPI's SHA256 digest is checked before saving. Esc
cancels an active download. On exit the application waits for the current network
operation to finish or time out so it can clean up temporary files. Text file
previews are limited to 1 MiB and web pages to 2 MiB.

Optional `GITHUB_TOKEN` and `GITLAB_TOKEN` environment variables authenticate API
requests. Tokens are not forwarded to websites, downloads or redirect targets.
Download support targets public artifacts. Network and API-limit errors appear
in the status line and leave navigation available.

Provider references: [GitLab projects](https://docs.gitlab.com/api/projects/),
[GitLab releases](https://docs.gitlab.com/api/releases/),
[PyPI index API](https://docs.pypi.org/api/index-api/) and
[PyPI JSON API](https://docs.pypi.org/api/json/).

## Cloning repositories

In the terminal interface, press `c` on a repository search result or open the
repository and select **Clone repository…**. Press `C` anywhere to enter a Git URL.
The next prompt lets you edit the destination directory; press Enter to clone.
The default is a new folder named after the repository inside the download
directory. Existing directories are never overwritten. Progress appears in the
status line; Esc cancels the clone and removes its incomplete directory.

Cloning requires Git. HTTPS, HTTP, SSH (`ssh://` and `git@host:owner/repo.git`),
and `git://` URLs are supported. Configure credentials using Git credential
helpers or an SSH agent in advance. Interactive password and SSH host-key prompts
are disabled in the app; SSH hosts must already be trusted in your known_hosts.

The command-line equivalent is:

```sh
./github-release-grabber clone https://github.com/jqlang/jq.git
./github-release-grabber clone git@gitlab.com:group/project.git ./project-copy
```

## Shell commands

Search GitHub (up to 20 results, including descriptions and HTTPS clone URLs):

```sh
./github-release-grabber search ghidra
./github-release-grabber search 'language:C topic:openbsd'
```

Show a repository's HTTPS clone URL:

```sh
./github-release-grabber clone-url NationalSecurityAgency/ghidra
```

Download from the latest stable release into the current directory:

```sh
./github-release-grabber download NationalSecurityAgency/ghidra
```

If the release contains multiple files, enter the number of the file to download.
A single attached file is downloaded automatically. If there are no attached
files, choose between `source.zip` and `source.tar.gz`.
Existing files are never overwritten; failed downloads are cleaned up.

For scripts, provide the exact asset filename. Optionally specify a release tag:

```sh
./github-release-grabber download OWNER/REPO 'ASSET_NAME'
./github-release-grabber download OWNER/REPO 'ASSET_NAME' 'v1.2.3'
```

To select interactively from a particular release, pass an empty asset name:

```sh
./github-release-grabber download OWNER/REPO '' 'v1.2.3'
```

Show attached release download URLs (the original invocation still works):

```sh
./github-release-grabber NationalSecurityAgency/ghidra
./github-release-grabber urls NationalSecurityAgency/ghidra
./github-release-grabber urls OWNER/REPO 'v1.2.3'
./github-release-grabber --help
```

Without a tag, release commands use GitHub's latest stable release. To download
a prerelease, specify its tag. A repository with no matching published release
produces an error.

Optionally set `GITHUB_TOKEN` in the environment to authenticate API requests
and use the corresponding rate limits. Tokens are only sent to the API, not to
asset download URLs. Asset downloads target public repositories.

The commands use GitHub's [repository search API](https://docs.github.com/en/rest/search/search#search-repositories)
and [release API](https://docs.github.com/en/rest/releases/releases).

## Tests

The offline tests require Python 3 (including curses), jq and Git. They cover provider
adapters, terminal navigation, HTML links, download integrity, API credentials,
and the original shell commands. Network transports are replaced with fixtures;
no network access or tokens are needed.

```sh
python3 -m unittest discover -s tests -v
```

## Copyright
By Jan Helbling <jhelb1993@gmail.com> || https://github.com/jhelb1993

## License
```

GNU GENERAL PUBLIC LICENSE
                       Version 3, 29 June 2007

 Copyright (C) 2007 Free Software Foundation, Inc. <https://fsf.org/>
 Everyone is permitted to copy and distribute verbatim copies
 of this license document, but changing it is not allowed.

                            Preamble


```
