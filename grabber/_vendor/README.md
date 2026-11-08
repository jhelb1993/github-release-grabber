# Bundled Markdown parser

Mistune 3.4.0, by Hsiaoming Yang, BSD-3-Clause.
Source: https://github.com/lepture/mistune/tree/v3.4.0
Distribution: https://pypi.org/project/mistune/3.4.0/
Wheel: `mistune-3.4.0-py3-none-any.whl`
SHA256: `d03cec4c8620f21517a996d82d14f3b59c30d79bda2cc6c56bd32b839f18ce6b`

The wheel's `mistune/` package is included with its LICENSE. Only the built-in
plugin registry in `mistune/plugins/__init__.py` is changed: its absolute import
paths point to `grabber._vendor.mistune.plugins` to avoid a global installation.
The terminal renderer consumes the AST; it does not execute HTML or scripts.
