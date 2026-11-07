# github-release-grabber

Search public GitHub repositories, show clone URLs, and download release files
from the terminal, without a JavaScript browser. Works with OpenBSD `/bin/sh`.

## Requirements

Install `curl` and `jq` (on OpenBSD, as root):

```sh
pkg_add curl jq
```

## Usage

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

The offline integration tests require Python 3 and jq. They replace curl with a
local test transport; no network access or GitHub token is needed.

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
