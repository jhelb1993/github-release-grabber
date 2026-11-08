"""Editable forms and explicit, single-use write previews for the terminal UI."""
from dataclasses import dataclass
import json

from .network import Error
from .providers import Item, View


@dataclass
class Field:
    key: str
    label: str
    value: str = ''
    multiline: bool = False


def form(title, fields, submit, button='Review changes…'):
    def render():
        items = [Item(f'{field.label}: {field.value.splitlines()[0] if field.value else "(empty)"}',
                      'field', {'field': field}, 'Enter to edit; Ctrl-S saves the field, Esc discards edits.') for field in fields]
        items.append(Item(button, 'submit'))
        return View(title, items, context={'fields': fields, 'submit': submit, 'render': render})
    return render()


def confirmation(title, target, payload, send):
    # Snapshot the exact payload shown to the user; editing the draft afterward
    # cannot change the request behind an already-open preview.
    snapshot = json.loads(json.dumps(payload))
    used = False

    def commit():
        nonlocal used
        if used:
            raise Error('This confirmation has already been used. Refresh before starting another action.')
        used = True  # Never automatically repeat a possibly successful write.
        try:
            return send(snapshot)
        except Exception as exc:
            raise Error(f'{exc} Check the remote state before retrying; this confirmation will not send twice.') from exc

    def describe(values, indent=''):
        lines = []
        for key, value in values.items():
            label = key.replace('_', ' ').capitalize()
            if isinstance(value, dict):
                lines.append(indent + label + ':')
                lines.extend(describe(value, indent + '  '))
            elif isinstance(value, list):
                lines.append(indent + label + ': ' + (', '.join(map(str, value)) or '(clear)'))
            else:
                shown = '(clear)' if value is None or value == '' else 'yes' if value is True else 'no' if value is False else str(value)
                lines.append(indent + label + ':\n' + shown if '\n' in shown else indent + label + ': ' + shown)
        return lines

    # GraphQL syntax is not useful when deciding whether to change draft status;
    # the action and repository identify that change. Other actions show every field.
    fields = {} if 'query' in snapshot else snapshot
    preview = f'{title}\nTarget: {target}\n\n' + '\n\n'.join(describe(fields))
    return View('Confirm: ' + title, [
        Item('Preview changes', 'text', {'text': preview}, target),
        Item('SEND: ' + title, 'call', {'callback': commit}, 'Sends this action to the remote service. Esc goes back without sending.')])
