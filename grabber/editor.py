"""Small multiline draft editor. Editing never performs network requests."""
import curses


class Editor:
    def __init__(self, field, save):
        self.field = field
        self.text = field.value
        self.position = len(self.text)
        self.save = save
        self.top = 0

    def handle(self, key):
        if key == '\x1b':
            return 'cancel'
        if key == '\x13':  # Ctrl-S
            self.save(self.text)
            return 'save'
        start = self.text.rfind('\n', 0, self.position) + 1
        end = self.text.find('\n', self.position)
        if end < 0:
            end = len(self.text)
        if key == curses.KEY_LEFT:
            self.position = max(0, self.position - 1)
        elif key == curses.KEY_RIGHT:
            self.position = min(len(self.text), self.position + 1)
        elif key == curses.KEY_HOME:
            self.position = start
        elif key == curses.KEY_END:
            self.position = end
        elif key == curses.KEY_UP and start:
            previous = self.text.rfind('\n', 0, start - 1) + 1
            self.position = min(start - 1, previous + self.position - start)
        elif key == curses.KEY_DOWN and end < len(self.text):
            following = self.text.find('\n', end + 1)
            self.position = min(len(self.text) if following < 0 else following, end + 1 + self.position - start)
        elif key in (curses.KEY_BACKSPACE, '\x7f', '\b') and self.position:
            self.text = self.text[:self.position - 1] + self.text[self.position:]
            self.position -= 1
        elif key == curses.KEY_DC:
            self.text = self.text[:self.position] + self.text[self.position + 1:]
        elif key == '\x15':
            self.text = self.text[:start] + self.text[self.position:]
            self.position = start
        elif key in ('\n', '\r', curses.KEY_ENTER) and not self.field.multiline:
            self.save(self.text)
            return 'save'
        elif len(self.text) < 65536:
            char = '\n' if key in ('\n', '\r', curses.KEY_ENTER) else '    ' if key == '\t' else key
            if isinstance(char, str) and (char.isprintable() or char == '\n'):
                self.text = self.text[:self.position] + char + self.text[self.position:]
                self.position += len(char)
        return None

    def draw(self, app):
        height, width = app.screen.getmaxyx()
        app.screen.erase()
        app.put(0, 'Edit: ' + self.field.label, curses.A_REVERSE)
        lines = self.text.split('\n')
        line = self.text[:self.position].count('\n')
        column = self.position - (self.text.rfind('\n', 0, self.position) + 1)
        rows = max(1, height - 4)
        self.top = min(self.top, line)
        self.top = max(self.top, line - rows + 1)
        left = max(0, column - max(1, width - 3))
        for index, value in enumerate(lines[self.top:self.top + rows], 1):
            app.put(index, value[left:])
        app.put(height - 3, f'Line {line + 1}, column {column + 1} · {len(self.text)}/65536 characters')
        app.put(height - 2, 'Ctrl-S Save field · Esc Discard edits · Arrows Move · Ctrl-U Clear to line start')
        app.put(height - 1, 'Enter: ' + ('new line' if self.field.multiline else 'save field') + ' · Saving a field does not send it.', curses.A_REVERSE)
        # An inverse cursor also works when the terminal hides its hardware cursor.
        if height > 4 and width > 1:
            cursor = lines[line][column:column + 1] or ' '
            try:
                app.screen.addnstr(1 + line - self.top, min(column - left, width - 1), cursor, 1, curses.A_REVERSE)
            except curses.error:
                pass
        app.screen.refresh()
