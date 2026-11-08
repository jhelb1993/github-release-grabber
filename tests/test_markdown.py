"""Markdown semantics, terminal layout and provider/view integration."""
from email.message import Message
from pathlib import Path
import unittest

from grabber.markdown import BOLD, ITALIC, CODE, STRIKE, LINK, cells, render
from grabber.providers import Browser, Item, View
from grabber.forms import Field, form
from grabber.forge import Forge
from grabber.tui import App


def text(document):
    return '\n'.join(''.join(span.text for span in row) for row in document.lines)


def styled(document, flag):
    return ''.join(span.text for row in document.lines for span in row if span.style & flag)


class MarkdownTests(unittest.TestCase):
    def test_headings_nested_emphasis_and_code(self):
        doc = render('# Heading\n\n**bold and *italic***, ~~old~~, `**literal**`.', 80)
        self.assertIn('Heading\n═══════', text(doc))
        self.assertIn('bold and italic', styled(doc, BOLD))
        self.assertIn('italic', styled(doc, ITALIC))
        self.assertIn('old', styled(doc, STRIKE))
        self.assertIn('**literal**', styled(doc, CODE))
        self.assertNotIn('**bold', text(doc))

    def test_setext_heading_and_reference_links(self):
        doc = render('Title\n=====\n\n[Docs][ref]\n\n[ref]: https://example.org/docs "Docs"', 80)
        self.assertIn('Title\n═════', text(doc))
        self.assertEqual(doc.links[0].url, 'https://example.org/docs')
        self.assertIn('Docs [1]', text(doc))

    def test_soft_and_hard_line_breaks(self):
        doc = render('one\ntwo  \nthree\\\nfour', 80)
        self.assertEqual(text(doc), 'one two\nthree\nfour')

    def test_lists_task_items_and_hanging_indent(self):
        doc = render('- [x] Done\n- [ ] Open\n  - nested child with words that wrap\n\n3. Third\n4. Fourth', 25)
        output = text(doc)
        self.assertIn('[x] Done', output)
        self.assertIn('[ ] Open', output)
        self.assertIn('    • nested child with', output)
        self.assertIn('      words that wrap', output)
        self.assertIn('3. Third', output)
        self.assertIn('4. Fourth', output)
        self.assertTrue(all(cells(line) <= 25 for line in output.splitlines()))

    def test_code_keeps_indentation_entities_and_markers(self):
        doc = render('```python\n    **not bold**\n\n\tprint("&amp;")\n```', 80)
        output = text(doc)
        self.assertIn('│     **not bold**', output)
        self.assertIn('│     print("&amp;")', output)
        self.assertIn('│ \n', output)
        self.assertNotIn('not bold', styled(doc, BOLD))

    def test_blockquotes_and_rules(self):
        output = text(render('> quoted **text**\n>\n> > nested\n\n---', 30))
        self.assertIn('│ quoted text', output)
        self.assertIn('│ │ nested', output)
        self.assertIn('─' * 30, output)

    def test_table_alignment_and_narrow_fallback(self):
        source = '| Name | Value |\n| :--- | ---: |\n| **A** | 你好 |\n| B | very long value |'
        doc = render(source, 30)
        self.assertIn('Name', styled(doc, BOLD))
        self.assertIn('你好', text(doc))
        self.assertTrue(all(cells(line) <= 30 for line in text(doc).splitlines()))
        narrow = text(render(source, 12))
        self.assertIn('Name: A', narrow)
        self.assertIn('Value: 你好', narrow)
        self.assertTrue(all(cells(line) <= 12 for line in narrow.splitlines()))

    def test_unicode_wrap_uses_cells_and_preserves_combining_marks(self):
        source = '你好你好你好 **cafe\u0301** more'
        doc = render(source, 10)
        self.assertTrue(all(cells(line) <= 10 for line in text(doc).splitlines()))
        self.assertIn('cafe\u0301', text(doc))

    def test_links_images_entities_anchors_and_duplicates(self):
        source = '# [Intro](https://example.org)\n\n# Intro\n\n[Jump](#intro-1) ![logo](img.png) &amp;'
        doc = render(source, 80, 'https://example.org/docs/readme.md')
        self.assertIn('intro', doc.anchors)
        self.assertIn('intro-1', doc.anchors)
        self.assertEqual(doc.links[-1].url, 'https://example.org/docs/img.png')
        self.assertIn('[Image: logo]', text(doc))
        self.assertIn('&', text(doc))
        self.assertNotIn('&amp;', text(doc))

    def test_unsafe_links_and_terminal_sequences_are_not_active(self):
        doc = render('[bad](javascript:alert) [bad2](file:///etc/passwd) [bad3](https://user:secret@example.org)\n\n\x1b[31mred\u202e', 80)
        self.assertEqual(doc.links, [])
        self.assertNotIn('\x1b', text(doc))
        self.assertNotIn('\u202e', text(doc))

    def test_html_script_hidden_and_code_literal(self):
        doc = render('<script>secret()</script>\n\n<b>bold</b> and <kbd>Enter</kbd><br>next\n\n```html\n<script>literal()</script>\n```', 80)
        self.assertNotIn('secret()', text(doc))
        self.assertIn('bold', styled(doc, BOLD))
        self.assertIn('Enter', styled(doc, CODE))
        self.assertIn('<script>literal()</script>', styled(doc, CODE))

    def test_footnotes_and_plain_preamble(self):
        doc = render('Body[^a].\n\n[^a]: Note.', 80, preamble='Title: **literal**\nAuthor: me')
        self.assertIn('Title: **literal**\nAuthor: me', text(doc))
        self.assertIn('Footnotes\n[1]', text(doc))
        self.assertIn('Note.', text(doc))


class FixtureClient:
    def __init__(self, response=None):
        self.response = response
    def json(self, url, **kwargs):
        return self.response
    def read(self, url, *args, **kwargs):
        headers = Message()
        headers['Content-Type'] = 'text/plain'
        return b'# Title\n\n**bold**', headers, url


class Screen:
    def __init__(self):
        self.size = (24, 80)
        self.calls = []
    def getmaxyx(self):
        return self.size
    def erase(self):
        self.calls = []
    def addnstr(self, row, column, text, width, attr):
        self.calls.append((row, column, text[:width], attr))
    def refresh(self):
        pass


class IntegrationTests(unittest.TestCase):
    def test_markdown_files_and_plain_source(self):
        browser = Browser(FixtureClient())
        repo = {'full_name': 'a/b', 'default_branch': 'main'}
        self.assertTrue(browser.file('GitHub', repo, 'README.md').markdown)
        self.assertFalse(browser.file('GitHub', repo, 'app.py').markdown)
        self.assertFalse(browser.file('GitHub', repo, 'change.diff').markdown)

    def test_pypi_content_type_and_release_notes(self):
        for mime, expected in [('text/markdown; charset=UTF-8', True), ('text/x-rst', False), ('text/plain', False)]:
            browser = Browser(FixtureClient({'info': {'name': 'pkg', 'version': '1', 'description': '# Heading', 'description_content_type': mime}}))
            details = browser.activate(browser.package('pkg').items[0])
            self.assertEqual(details.markdown, expected)
        view = browser.release('GitHub', {'tag_name': 'v1', 'body': '# Changes', 'assets': [], 'zipball_url': None, 'tarball_url': None})
        self.assertTrue(browser.activate(view.items[0]).markdown)

    def test_issue_comments_and_diffs_use_correct_mode(self):
        browser = Browser(FixtureClient([{'body': '**Comment**', 'user': {'login': 'me'}}]))
        forge = Forge(browser, 'GitHub', {'full_name': 'a/b'})
        comment = browser.activate(forge.comments('issue', 1).items[0])
        self.assertTrue(comment.markdown)
        self.assertIn('me', comment.preamble)
        diff = browser.activate(forge.file_diff(1, {}, {'filename': 'x', 'patch': '# raw diff'}).items[0])
        self.assertFalse(diff.markdown)

    def test_ui_styles_toggle_resize_and_anchor_navigation(self):
        screen = Screen()
        app = App(screen, Path('/tmp'))
        view = View('README', text='# Heading\n\n**bold** [Jump](#heading)\n' + '\n\n'.join('Paragraph ' + str(i) for i in range(40)), markdown=True)
        app.stack.append(view)
        app.draw()
        self.assertTrue(any(call[2] == 'bold' and call[3] for call in screen.calls))
        self.assertEqual(len(view.links), 1)
        app.key('m')
        app.draw()
        self.assertTrue(view.context['raw_markdown'])
        self.assertTrue(any('**bold**' in call[2] for call in screen.calls))
        app.key('m')
        app.draw()
        old_width = app.text_width
        screen.size = (18, 40)
        app.draw()
        self.assertNotEqual(app.text_width, old_width)
        app.key('l')
        self.assertEqual(app.view.items[0].kind, 'anchor')
        app.key('\n')
        self.assertIs(app.view, view)
        self.assertEqual(view.scroll, 0)

    def test_relative_repo_links_stay_in_api_browser(self):
        browser = Browser(FixtureClient())
        repo = {'full_name': 'a/b', 'default_branch': 'main'}
        view = browser.file('GitHub', repo, 'docs/README.md')
        doc = render('[Guide](../guide.md#setup)', 80, view.base_url)
        item = browser.markdown_link(view, doc.links[0])
        self.assertEqual(item.kind, 'file')
        self.assertEqual(item.data['path'], 'guide.md')
        self.assertEqual(item.data['anchor'], 'setup')

    def test_saved_field_preview_does_not_submit(self):
        calls = []
        app = App(Screen(), Path('/tmp'))
        app.stack.append(form('Draft', [Field('body', 'Body', '**Draft**', True)], lambda values: calls.append(values)))
        app.key('p')
        self.assertTrue(app.view.markdown)
        self.assertEqual(app.view.text, '**Draft**')
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
