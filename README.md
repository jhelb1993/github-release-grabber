# github-release-grabber

Browse GitHub, GitLab, PyPI packages and github.io pages in a curses terminal.
Search repositories, read source files, inspect releases and download artifacts.
Web pages are rendered as text with navigable links, entirely inside the terminal.
The original OpenBSD shell commands remain available.

## Requirements

The curses interface requires Python 3.10 or newer with its standard `curses` module.
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
project. No additional Python packages or external browser need to be installed.
The Markdown parser is bundled with its license under `grabber/_vendor/`.
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
| `r` | Refresh issue/PR details, lists, comments and CI views |
| `g` | Enter an HTTPS URL to read in the terminal |
| `l` | List and follow links from the current web page or Markdown document |
| `m` | Toggle rendered Markdown and raw source |
| `p` in a form | Preview the selected saved multiline Markdown field |
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

## Markdown in the terminal

Markdown files (`.md`, `.markdown`, `.mdown`, `.mkd`, `.mkdn`), release notes,
issue/PR descriptions, comments, reviews, discussions and check summaries render
automatically. PyPI descriptions render as Markdown when the package declares
`text/markdown`; reStructuredText and plain-text descriptions stay as text.

Supported formatting includes headings, bold and italic text, inline code,
blockquotes, ordered/nested lists, task checkboxes, horizontal rules, fenced and
indented code blocks, tables, reference links, autolinks, images as alt text,
and footnotes. Deleted text is dimmed because curses has no portable strikeout
attribute. Italics fall back to underline when unavailable.

Text wraps to the current terminal width using character cell widths, including
wide characters and combining marks. Tables wrap their cells; very narrow
screens show labeled rows instead. Code blocks keep indentation and literal
Markdown symbols, with long lines wrapped to fit the screen.

Press **m** to inspect the original Markdown. Press **l** for numbered links;
heading links jump within the document, and relative repository file links stay
inside the API file browser. HTTPS links open in the built-in text reader.
Source code, diffs, metadata and request previews remain plain text. In a form,
select a saved multiline field and press **p** for a formatted preview without
sending it; the editor itself continues to edit the raw Markdown.

The bundled Mistune parser handles Markdown structure; curses handles display.
HTML blocks have a text fallback, and images are represented by alt text rather
than rendered graphically. JavaScript and styles are never executed. Math,
Mermaid diagrams and provider-specific widgets are not rendered. Other link
schemes, such as `mailto:`, remain text because the reader supports HTTPS.

## Issues, pull requests and merge requests

Open a repository, then select **Issues**, **Pull requests** (GitHub) or
**Merge requests** (GitLab). These features stay inside the terminal:

- Paginated lists with state, search text, label, author and assignee filters.
  States include open, closed, all, and merged for PRs/MRs. GitHub text searches
  use a phrase and are limited to the first 1,000 search results.
- Descriptions, authors, timestamps, labels, assignees, milestones, branch names,
  draft status and mergeability. GitHub's mergeability can initially be unknown;
  press `r` to refresh it.
- Comments, review discussions, commits and changed files with individual diffs.
- GitHub reviews, check runs and commit statuses; GitLab approvals, pipelines
  and pipeline jobs.
- Create issues and draft or ready PRs/MRs; edit titles and descriptions; close
  and reopen; change labels, assignees and milestones.
- Add comments, reply to review discussions, comment on individual diff lines,
  request reviewers, and resolve/unresolve GitLab review discussions.
- GitHub reviews support COMMENT, APPROVE and REQUEST_CHANGES. GitLab supports
  approving and withdrawing approval. Both support draft/ready transitions.
- Merge GitHub PRs using merge, squash or rebase; merge GitLab MRs with optional
  squash, subject to the project's merge settings and rules.

### Editing and sending

Select a field with Enter. The built-in editor supports multiline Markdown,
arrow keys, Home/End, Backspace/Delete, and Ctrl-U to clear to the beginning of
the line. **Ctrl-S saves the field**, and **Esc discards edits to that field**.
Enter also saves single-line fields. The draft remains in the navigation stack
while you browse other views; drafts are not saved after quitting the app.

Select **Review changes…**, open **Preview changes**, and then select
**SEND** to publish. Editing fields and reading previews do not send anything.
Each confirmation can only be sent once, including after an uncertain network
failure. Check the remote state before creating a replacement request. After a
successful action, go back to the list/detail view and press `r` to refresh.

Merge requests include the displayed head commit SHA. A newer pushed commit
causes the provider to reject that merge until you refresh and review again.
GitHub reviews and inline comments, and GitLab approvals and inline discussions,
also refer to the displayed commit or diff references. The provider enforces
branch protections, review requirements and access permissions.

### Authentication and provider differences

Set `GITHUB_TOKEN` or `GITLAB_TOKEN` before launching the app. Tokens are read
from the environment and are never included in request previews. Write actions
require the relevant repository permissions. For GitHub fine-grained tokens,
issue actions need Issues write, PR/review actions need Pull requests write,
and merging needs Contents write; check-run access may require Checks read.
GitLab tokens need API access for writes. Repository membership, project policy
and service tier can further restrict actions. Some GitLab comment/discussion
endpoints require authentication even for public projects.

GitHub assignees/reviewers use usernames. GitLab uses numeric user IDs for these
write forms and the assignee filter. Metadata forms replace the complete label
and assignee lists; empty values clear them. The repository label/milestone lists
show the available milestone numbers (GitHub) or IDs (GitLab).

GitHub PR creation accepts `owner:branch` for a fork's source branch. GitLab MR
creation currently uses source and target branches in the selected project.
Branches must already exist on the server. This feature does not push local
commits or create branches automatically.

Diffs are subject to provider limits: unavailable binary or truncated patches are
identified in the viewer. GitHub limits PR file lists to 3,000 files and commit
lists to 250 commits. Inline comments require a line present in the displayed
diff; the provider validates it. GitHub review comments are listed individually
with their reply context; GitLab discussions are grouped and can be resolved.
GitHub draft transitions use its GraphQL API; the remaining operations use REST.

API references: [GitHub issues](https://docs.github.com/en/rest/issues/issues),
[GitHub PRs](https://docs.github.com/en/rest/pulls/pulls),
[GitHub reviews](https://docs.github.com/en/rest/pulls/reviews),
[GitLab issues](https://docs.gitlab.com/api/issues/),
[GitLab MRs](https://docs.gitlab.com/api/merge_requests/) and
[GitLab discussions](https://docs.gitlab.com/api/discussions/).

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
issue/PR filters and payloads, draft editing, single-use confirmations,
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
