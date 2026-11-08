"""Markdown AST to terminal cells; no HTML execution or ANSI escape sequences."""
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import urljoin, urlsplit

from ._vendor.mistune import create_markdown

BOLD, ITALIC, CODE, QUOTE, STRIKE, LINK, HEADING = (1 << index for index in range(7))
DIFF_ADD, DIFF_REMOVE, DIFF_HUNK, DIFF_META = (1 << index for index in range(7, 11))


def safe(text):
    return ''.join(char if char in ('\n', '\t') or not unicodedata.category(char).startswith('C') else ' '
                   for char in str(text)).expandtabs(4)


def cells(text):
    return sum(0 if unicodedata.combining(char) or unicodedata.category(char) in ('Mn', 'Me') else
               2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1 for char in text)


@dataclass
class Span:
    text: str
    style: int = 0


@dataclass
class Link:
    label: str
    href: str
    url: str


@dataclass
class Document:
    lines: list = field(default_factory=list)
    links: list = field(default_factory=list)
    anchors: dict = field(default_factory=dict)


def compact(chars):
    spans = []
    for char, style in chars:
        if spans and spans[-1].style == style:
            spans[-1].text += char
        else:
            spans.append(Span(char, style))
    return spans


def wrap(spans, width, preserve=False):
    """Wrap styled text in terminal cells, preserving explicit/code line breaks."""
    width = max(2, width)
    chars = [(char, span.style) for span in spans for char in safe(span.text)]
    result, start = [], 0
    while start < len(chars):
        end, used, space = start, 0, -1
        while end < len(chars):
            char = chars[end][0]
            if char == '\n' or used + cells(char) > width:
                break
            used += cells(char)
            if char == ' ':
                space = end
            end += 1
        newline = end < len(chars) and chars[end][0] == '\n'
        if not newline and end < len(chars) and not preserve and space > start:
            end = space
        if end == start and not newline:
            end += 1
        row = chars[start:end]
        if not preserve:
            while row and row[-1][0] == ' ':
                row.pop()
        result.append(compact(row))
        start = end + 1 if newline else end
        if not preserve:
            while start < len(chars) and chars[start][0] == ' ':
                start += 1
    if not result or (chars and chars[-1][0] == '\n'):
        result.append([])
    return result


def diff_spans(source):
    """Style unified patches without interpreting their contents as Markdown."""
    old = new = 0
    for line in source.split('\n'):
        hunk = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
        if hunk:
            old, new = int(hunk[2] or 1), int(hunk[4] or 1)
            style = DIFF_HUNK | BOLD
        elif line.startswith('diff ') or (not (old or new) and line.startswith((
                '--- ', '+++ ', 'index ', 'new file ', 'deleted file ', 'old mode ',
                'new mode ', 'rename ', 'copy ', 'similarity ', 'dissimilarity ',
                'Binary files ', 'GIT binary patch'))):
            old = new = 0
            style = DIFF_META | BOLD
        elif line.startswith('+'):
            style = DIFF_ADD
            new = max(0, new - 1)
        elif line.startswith('-'):
            style = DIFF_REMOVE
            old = max(0, old - 1)
        elif line.startswith('@@'):
            style = DIFF_HUNK | BOLD
        else:
            style = 0
            if line.startswith(' '):
                old, new = max(0, old - 1), max(0, new - 1)
        yield Span(line, style)


def render_diff(source, width, preamble=''):
    document = Document()
    if preamble:
        document.lines.extend(wrap([Span(preamble + '\n')], width, preserve=True))
    for span in diff_spans(source):
        document.lines.extend(wrap([span], width, preserve=True))
    return document


class HTMLText(HTMLParser):
    """Readable fallback for embedded HTML; scripts and styling stay hidden."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'template'):
            self.hidden.append(tag)
        if self.hidden:
            return
        if tag in ('p', 'div', 'br', 'li', 'summary', 'details', 'pre', 'tr'):
            self.parts.append('\n')
        if tag == 'img':
            self.parts.append('[Image: ' + (dict(attrs).get('alt') or 'image') + ']')

    def handle_endtag(self, tag):
        if self.hidden:
            if self.hidden[-1] == tag:
                self.hidden.pop()
        elif tag in ('p', 'div', 'li', 'summary', 'details', 'pre', 'tr'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


class Renderer:
    def __init__(self, width, base_url=''):
        self.width = max(4, width)
        self.base_url = base_url
        self.document = Document()
        self.link_ids = {}
        self.slug_counts = {}
        self.parser = create_markdown(renderer='ast', plugins=['table', 'strikethrough', 'task_lists', 'url', 'footnotes'])

    def register_link(self, label, href):
        href = safe(unescape(href)).strip()
        url = urljoin(self.base_url, href)
        try:
            parts = urlsplit(url)
        except ValueError:
            return ''
        # HTTP/mailto links remain visible as text. The app's reader supports HTTPS.
        if not (href.startswith('#') or (parts.scheme == 'https' and parts.hostname and not parts.username)):
            return ''
        key = (label, href)
        if key not in self.link_ids:
            self.document.links.append(Link(label, href, url))
            self.link_ids[key] = len(self.document.links)
        return f' [{self.link_ids[key]}]'

    def inline(self, tokens, style=0):
        result = []
        html_styles = []
        hidden = False
        for token in tokens:
            kind = token['type']
            children = token.get('children', [])
            current = style
            for value in html_styles:
                current |= value
            if kind == 'inline_html':
                raw = token.get('raw', '')
                match = re.match(r'</?([\w]+)', raw)
                tag = match.group(1).lower() if match else ''
                if tag in ('script', 'style'):
                    hidden = not raw.startswith('</')
                elif tag in ('strong', 'b', 'em', 'i', 'code', 'kbd', 's', 'del'):
                    if raw.startswith('</'):
                        if html_styles:
                            html_styles.pop()
                    else:
                        html_styles.append(BOLD if tag in ('strong', 'b') else CODE if tag in ('code', 'kbd') else STRIKE if tag in ('s', 'del') else ITALIC)
                elif tag == 'br':
                    result.append(Span('\n', current))
                continue
            if hidden:
                continue
            if kind in ('strong', 'emphasis', 'strikethrough'):
                extra = {'strong': BOLD, 'emphasis': ITALIC, 'strikethrough': STRIKE}[kind]
                result += self.inline(children, current | extra)
            elif kind == 'codespan':
                result.append(Span(token['raw'], current | CODE))
            elif kind in ('link', 'image'):
                content = self.inline(children, current | LINK)
                label = ''.join(span.text for span in content) or token['attrs']['url']
                if kind == 'image':
                    result.append(Span('[Image: ' + label + ']', current | ITALIC))
                else:
                    result += content or [Span(label, current | LINK)]
                result.append(Span(self.register_link(label, token['attrs']['url']), current | LINK))
            elif kind == 'softbreak':
                result.append(Span(' ', current))
            elif kind == 'linebreak':
                result.append(Span('\n', current))
            elif kind == 'footnote_ref':
                result.append(Span(f"[{token['attrs']['index']}]", current | LINK))
            elif children:
                result += self.inline(children, current)
            else:
                result.append(Span(unescape(token.get('raw', '')), current))
        return result

    def plain_inline(self, tokens):
        result = []
        for token in tokens:
            if token.get('children'):
                result.append(self.plain_inline(token['children']))
            elif token['type'] in ('softbreak', 'linebreak'):
                result.append(' ')
            elif token['type'] != 'inline_html':
                result.append(unescape(token.get('raw', '')))
        return ''.join(result)

    def emit(self, spans, prefix='', preserve=False, continuation=None):
        continuation = prefix if continuation is None else continuation
        width = self.width - max(cells(prefix), cells(continuation))
        for index, row in enumerate(wrap(spans, width, preserve)):
            self.document.lines.append(([Span(prefix if index == 0 else continuation, QUOTE)] if prefix or continuation else []) + row)

    def blank(self):
        if self.document.lines and self.document.lines[-1]:
            self.document.lines.append([])

    def blocks(self, tokens, prefix='', depth=0):
        if depth > 32:
            self.emit([Span('(Nested content omitted; use raw view.)')], prefix)
            return
        for token in tokens:
            kind, children = token['type'], token.get('children', [])
            if kind in ('paragraph', 'block_text'):
                self.emit(self.inline(children), prefix)
                if kind == 'paragraph':
                    self.blank()
            elif kind == 'heading':
                self.blank()
                spans = self.inline(children, HEADING | BOLD)
                label = self.plain_inline(children)
                slug = re.sub(r'[^\w\- ]', '', label.lower()).replace(' ', '-')
                duplicate = self.slug_counts.get(slug, 0)
                self.slug_counts[slug] = duplicate + 1
                self.document.anchors[slug + (f'-{duplicate}' if duplicate else '')] = len(self.document.lines)
                self.emit(spans, prefix)
                if token.get('attrs', {}).get('level', 1) <= 2:
                    length = min(cells(''.join(span.text for span in spans)), self.width - cells(prefix))
                    self.emit([Span(('═' if token['attrs']['level'] == 1 else '─') * max(1, length), HEADING)], prefix)
                self.blank()
            elif kind == 'block_code':
                self.blank()
                language = token.get('attrs', {}).get('info', '').split(' ', 1)[0]
                self.emit([Span('┌─' + (' ' + language if language else ' code'), CODE)], prefix)
                raw = token.get('raw', '').removesuffix('\n')
                if language.lower() in ('diff', 'patch'):
                    for span in diff_spans(raw):
                        self.emit([span], prefix + '│ ', preserve=True)
                else:
                    self.emit([Span(raw, CODE)], prefix + '│ ', preserve=True)
                self.emit([Span('└─', CODE)], prefix)
                self.blank()
            elif kind == 'block_quote':
                self.blocks(children, prefix + '│ ', depth + 1)
                self.blank()
            elif kind == 'list':
                ordered = token.get('attrs', {}).get('ordered', False)
                start = token.get('attrs', {}).get('start', 1)
                for index, item in enumerate(children):
                    marker = f'{start + index}. ' if ordered else '• '
                    if item['type'] == 'task_list_item':
                        marker = '[x] ' if item.get('attrs', {}).get('checked') else '[ ] '
                    begin = len(self.document.lines)
                    self.blocks(item.get('children', []), prefix + ' ' * cells(marker), depth + 1)
                    if len(self.document.lines) == begin:
                        self.document.lines.append([Span(prefix + marker)])
                    else:
                        row = self.document.lines[begin]
                        # Replace the reserved indentation with the list marker.
                        if row and row[0].text.startswith(prefix + ' ' * cells(marker)):
                            row[0].text = prefix + marker + row[0].text[len(prefix) + cells(marker):]
                self.blank()
            elif kind == 'thematic_break':
                self.emit([Span('─' * max(1, self.width - cells(prefix)), QUOTE)], prefix)
                self.blank()
            elif kind == 'table':
                self.table(children, prefix)
                self.blank()
            elif kind == 'footnotes':
                self.blank()
                self.emit([Span('Footnotes', BOLD)], prefix)
                self.blocks(children, prefix, depth + 1)
            elif kind == 'footnote_item':
                self.emit([Span(f"[{token['attrs']['index']}]", BOLD)], prefix)
                self.blocks(children, prefix + '  ', depth + 1)
            elif kind == 'block_html':
                html = HTMLText()
                html.feed(token.get('raw', ''))
                value = ''.join(html.parts).strip()
                if value:
                    self.emit([Span(value)], prefix)
                    self.blank()
            elif kind == 'blank_line':
                self.blank()
            elif children:
                self.blocks(children, prefix, depth + 1)
            elif token.get('raw'):
                self.emit([Span(token['raw'])], prefix)
        # Do not grow whitespace between nested list items.
        while self.document.lines and not self.document.lines[-1]:
            self.document.lines.pop()

    def table(self, children, prefix):
        header = children[0]['children']
        rows = [header] + [row['children'] for child in children[1:] for row in child.get('children', [])]
        styled = [[self.inline(cell.get('children', []), BOLD if row_index == 0 else 0) for cell in row] for row_index, row in enumerate(rows)]
        columns = len(header)
        if not columns:
            return
        budget = self.width - cells(prefix) - (columns + 1) - 2 * columns
        if budget < 4 * columns:
            # Narrow screens use labeled rows rather than clipping table columns.
            for row in styled[1:]:
                for index, value in enumerate(row):
                    self.emit(styled[0][index] + [Span(': ')] + value, prefix)
                self.blank()
            return
        widths = [min(budget, max(4, max(cells(''.join(span.text for span in row[index])) for row in styled))) for index in range(columns)]
        while sum(widths) > budget:
            index = max(range(columns), key=lambda i: widths[i])
            widths[index] -= 1
        border = '+' + '+'.join('-' * (width + 2) for width in widths) + '+'
        self.emit([Span(border, QUOTE)], prefix)
        for row_index, row in enumerate(styled):
            wrapped_cells = [wrap(value, widths[index]) for index, value in enumerate(row)]
            for line in range(max(map(len, wrapped_cells))):
                spans = [Span('| ', QUOTE)]
                for index, cell in enumerate(wrapped_cells):
                    content = cell[line] if line < len(cell) else []
                    padding = widths[index] - sum(cells(span.text) for span in content)
                    align = header[index].get('attrs', {}).get('align')
                    left = padding if align == 'right' else padding // 2 if align == 'center' else 0
                    spans += [Span(' ' * left)] + content + [Span(' ' * (padding - left)), Span(' | ' if index < columns - 1 else ' |', QUOTE)]
                self.emit(spans, prefix, preserve=True)
            if row_index == 0:
                self.emit([Span(border, QUOTE)], prefix)
        self.emit([Span(border, QUOTE)], prefix)

    def render(self, source, preamble=''):
        if preamble:
            self.emit([Span(preamble)])
            self.blank()
        self.blocks(self.parser(safe(source.replace('\r\n', '\n').replace('\r', '\n'))))
        if not self.document.lines:
            self.document.lines = [[]]
        return self.document


def render(source, width, base_url='', preamble=''):
    return Renderer(width, base_url).render(source, preamble)
