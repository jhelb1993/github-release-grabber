"""Patch styling, wrapping and terminal color fallback."""
import curses
from pathlib import Path
import unittest
from unittest.mock import patch

from grabber import markdown as md
from grabber.providers import Browser
from grabber.forge import Forge
from grabber.tui import App


class DiffTests(unittest.TestCase):
    def test_headers_and_header_like_content(self):
        spans = list(md.diff_spans('--- a/x\n+++ b/x\n@@ -1 +1 @@\n--- content\n+++ content\n context'))
        self.assertTrue(spans[0].style & md.DIFF_META)
        self.assertTrue(spans[1].style & md.DIFF_META)
        self.assertTrue(spans[2].style & md.DIFF_HUNK)
        self.assertEqual(spans[3].style, md.DIFF_REMOVE)
        self.assertEqual(spans[4].style, md.DIFF_ADD)
        self.assertEqual(spans[5].style, 0)

    def test_wrapping_preserves_indentation_and_color(self):
        doc = md.render_diff('+    abcdefghijk', 6)
        spans = [span for row in doc.lines for span in row]
        self.assertEqual(''.join(span.text for span in spans), '+    abcdefghijk')
        self.assertTrue(all(span.style == md.DIFF_ADD for span in spans))
        self.assertTrue(all(sum(md.cells(span.text) for span in row) <= 6 for row in doc.lines))

    def test_only_diff_fences_are_colored(self):
        for language, colored in [('diff', True), ('patch', True), ('python', False)]:
            doc = md.render(f'```{language}\n+added\n-removed\n```', 40)
            spans = [span for row in doc.lines for span in row]
            self.assertEqual(any(span.style & md.DIFF_ADD for span in spans), colored)
            self.assertEqual(any(span.style & md.DIFF_REMOVE for span in spans), colored)

    def test_forge_views_reach_diff_renderer(self):
        browser = Browser(None)
        for provider, repo, file in [
                ('GitHub', {'full_name': 'a/b'}, {'filename': 'x', 'patch': '+added'}),
                ('GitLab', {'id': 1, 'path_with_namespace': 'a/b'}, {'new_path': 'x', 'diff': '+added'})]:
            view = browser.activate(Forge(browser, provider, repo).file_diff(1, {}, file).items[0])
            self.assertTrue(view.diff)
            app = App(None, Path('/tmp'))
            app.stacks[app.provider].append(view)
            app.prepare_text(80)
            self.assertTrue(any(span.style & md.DIFF_ADD for row in app.text_lines for span in row))

    def test_palette_and_monochrome_fallback(self):
        app = App(None, Path('/tmp'))
        with patch('grabber.tui.curses.has_colors', return_value=True), \
                patch('grabber.tui.curses.start_color'), \
                patch('grabber.tui.curses.use_default_colors'), \
                patch('grabber.tui.curses.init_pair') as init, \
                patch('grabber.tui.curses.color_pair', side_effect=lambda n: n << 8):
            app.init_colors()
            init.assert_any_call(1, curses.COLOR_GREEN, -1)
            init.assert_any_call(2, curses.COLOR_RED, -1)
            self.assertEqual(app.diff_colors[md.DIFF_ADD], 1 << 8)
        with patch('grabber.tui.curses.has_colors', return_value=False):
            app.init_colors()
        self.assertEqual(app.diff_colors, {})
        with patch('grabber.tui.curses.has_colors', side_effect=curses.error):
            app.init_colors()
        self.assertEqual(app.diff_colors, {})
