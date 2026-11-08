"""GitHub issues/PRs and GitLab issues/MRs, including reviewable write actions."""
import re
from urllib.parse import quote, urlencode

from .forms import Field, confirmation, form
from .network import Error
from .providers import Item, View, PAGE_SIZE


def csv(value):
    return [part.strip() for part in value.split(',') if part.strip()]


def ids(value):
    try:
        result = [int(part) for part in csv(value)]
    except ValueError as exc:
        raise Error('User IDs must be comma-separated positive integers.') from exc
    if any(number <= 0 for number in result):
        raise Error('User IDs must be positive integers.')
    return result


def required(values, *keys):
    for key in keys:
        if not values.get(key, '').strip():
            raise Error(f'{key.replace("_", " ").capitalize()} is required.')


def choice(value, options):
    if value not in options:
        raise Error('Choose one of: ' + ', '.join(options))
    return value


def author(record):
    user = record.get('user') or record.get('author') or {}
    return user.get('login') or user.get('username') or user.get('name') or 'unknown'


def label_names(record):
    return [label['name'] if isinstance(label, dict) else label for label in record.get('labels', [])]


class Forge:
    def __init__(self, browser, provider, repo):
        self.browser = browser
        self.client = browser.client
        self.provider = provider
        self.repo = repo
        self.github = provider == 'GitHub'
        self.base = browser.repo_api(provider, repo)
        self.name = repo['full_name'] if self.github else repo['path_with_namespace']

    @staticmethod
    def link(title, method, *args, summary='', **kwargs):
        return Item(title, 'call', {'callback': lambda: method(*args, **kwargs)}, summary)

    def noun(self, kind):
        return 'Issues' if kind == 'issue' else 'Pull requests' if self.github else 'Merge requests'

    def endpoint(self, kind, number=None):
        suffix = 'issues' if kind == 'issue' else 'pulls' if self.github else 'merge_requests'
        return self.base + '/' + suffix + (f'/{int(number)}' if number is not None else '')

    def get_page(self, url, page, **params):
        return self.client.json(url + '?' + urlencode(dict(params, per_page=PAGE_SIZE, page=page)))

    def listing(self, kind, filters=None, page=1):
        filters = filters or {'state': 'open', 'query': '', 'labels': '', 'author': '', 'assignee': ''}
        state = filters['state']
        choice(state, ('open', 'closed', 'all', 'merged') if kind == 'pr' else ('open', 'closed', 'all'))
        if self.github:
            terms = [f'repo:{self.name}', 'is:issue' if kind == 'issue' else 'is:pr']
            if state != 'all':
                terms.append('is:' + state)
            for key, qualifier in [('author', 'author'), ('assignee', 'assignee')]:
                if filters.get(key):
                    terms.append(f'{qualifier}:{filters[key]}')
            terms.extend('label:' + '"' + label.replace('"', '') + '"' for label in csv(filters.get('labels', '')))
            if filters.get('query'):
                terms.append('"' + filters['query'].replace('"', '') + '"')
            data = self.get_page('https://api.github.com/search/issues', page, q=' '.join(terms), sort='updated', order='desc')
            records = data['items']
            more = page * PAGE_SIZE < min(data['total_count'], 1000)
            incomplete = data.get('incomplete_results', False)
        else:
            params = {'state': 'opened' if state == 'open' else state, 'scope': 'all', 'order_by': 'updated_at', 'sort': 'desc'}
            for key, api_key in [('query', 'search'), ('labels', 'labels'), ('author', 'author_username')]:
                if filters.get(key):
                    params[api_key] = filters[key]
            if filters.get('assignee'):
                values = ids(filters['assignee'])
                if len(values) != 1:
                    raise Error('Filter by one GitLab assignee ID.')
                params['assignee_id'] = values[0]
            records = self.get_page(self.endpoint(kind), page, **params)
            more, incomplete = len(records) == PAGE_SIZE, False
        items = [self.link('Filter / search…', self.filters, kind, filters),
                 self.link('Create new…', self.create, kind),
                 self.link('Repository labels', self.metadata, 'labels'),
                 self.link('Repository milestones', self.metadata, 'milestones')]
        for record in records:
            number = record['number'] if self.github else record['iid']
            badge = record.get('state', '') + (' · draft' if record.get('draft') else '')
            summary = f"{author(record)} · {record.get('updated_at', '')} · {', '.join(label_names(record))}"
            items.append(self.link(f"#{number} [{badge}] {record['title']}", self.detail, kind, number, summary=summary))
        title = f'{self.name} — {self.noun(kind)} [{state}] — Page {page}'
        if not records:
            title += ' — No results'
        if incomplete:
            title += ' — incomplete search results'
        return View(title, items, page=page, more=more,
                    loader=lambda p: self.listing(kind, filters, p), refresh=lambda: self.listing(kind, filters, page))

    def filters(self, kind, current):
        fields = [Field('state', 'State: open / closed / all' + (' / merged' if kind == 'pr' else ''), current.get('state', 'open')),
                  Field('query', 'Search text', current.get('query', '')),
                  Field('labels', 'Labels (comma-separated)', current.get('labels', '')),
                  Field('author', 'Author username', current.get('author', '')),
                  Field('assignee', 'Assignee ' + ('username' if self.github else 'ID'), current.get('assignee', ''))]
        return form('Filter ' + self.noun(kind), fields, lambda values: self.listing(kind, values), 'Apply filters')

    def detail(self, kind, number):
        record = self.client.json(self.endpoint(kind, number))
        # GitHub issue endpoints also accept PR numbers; keep navigation accurate.
        if kind == 'issue' and self.github and 'pull_request' in record:
            return self.detail('pr', number)
        assignees = ', '.join((person.get('login') or person.get('username', '')) for person in record.get('assignees', []))
        milestone = (record.get('milestone') or {}).get('title', 'none')
        lines = [record['title'], f"{self.name} #{number}", f"State: {record.get('state')}  Author: {author(record)}",
                 f"Created: {record.get('created_at', '')}  Updated: {record.get('updated_at', '')}",
                 'Labels: ' + ', '.join(label_names(record)), 'Assignees: ' + (assignees or 'none'),
                 'Milestone: ' + milestone, record.get('html_url') or record.get('web_url') or '']
        if kind == 'pr':
            if self.github:
                lines += [f"Branches: {record['head']['label']} -> {record['base']['label']}",
                          f"Head: {record['head']['sha']}", f"Draft: {record.get('draft')}  Merged: {record.get('merged')}",
                          f"Mergeable: {record.get('mergeable')}  Merge status: {record.get('mergeable_state')}",
                          'Reviewers: ' + ', '.join(person['login'] for person in record.get('requested_reviewers', []))]
            else:
                lines += [f"Branches: {record.get('source_branch')} -> {record.get('target_branch')}",
                          f"Head: {record.get('sha')}", f"Draft: {record.get('draft')}  Merge status: {record.get('detailed_merge_status')}",
                          'Reviewers: ' + ', '.join(person['username'] for person in record.get('reviewers', []))]
        body = record.get('body') or record.get('description') or '(No description.)'
        items = [Item('Description and metadata', 'text', {'text': body, 'preamble': '\n'.join(lines),
                 'markdown': True, 'base_url': record.get('html_url') or record.get('web_url') or ''}),
                 self.link('Comments', self.comments, kind, number),
                 self.link('Add comment…', self.comment_form, kind, number),
                 self.link('Edit title / description…', self.edit, kind, number, record),
                 self.link('Edit labels / assignees / milestone…', self.assign, kind, number, record)]
        if kind == 'pr':
            items += [self.link('Changed files and diffs', self.files, number, record),
                      self.link('Commits', self.commits, number),
                      self.link('Reviews / approvals', self.reviews, number),
                      self.link('Review discussions', self.discussions, number),
                      self.link('CI checks / pipelines', self.checks, number, record),
                      self.link('Request reviewers…', self.reviewers, number)]
        merged = record.get('merged') or record.get('state') == 'merged'
        if not merged:
            is_open = record.get('state') in ('open', 'opened')
            items.append(self.link('Close…' if is_open else 'Reopen…', self.change_state, kind, number, is_open))
            if kind == 'pr' and is_open:
                items.append(self.link('Mark ready for review…' if record.get('draft') or record.get('work_in_progress') else 'Convert to draft…', self.change_draft, number, record))
                items += [self.link('Submit review…' if self.github else 'Approve…', self.review, number, record),
                          self.link('Merge…', self.merge, number, record)]
                if not self.github:
                    items.append(self.link('Withdraw approval…', self.prepare, 'Withdraw approval', self.endpoint('pr', number) + '/unapprove', 'POST', {}))
        title = f"{self.name} #{number}: {record['title']}"
        return View(title, items, refresh=lambda: self.detail(kind, number))

    def prepare(self, title, url, method, payload):
        def send(snapshot):
            result = self.client.write_json(url, method, snapshot)
            if isinstance(result, dict) and result.get('errors'):
                raise Error(str(result['errors']))
            if isinstance(result, dict) and result.get('merged') is False:
                raise Error(result.get('message') or 'The server did not merge this request.')
            location = result.get('html_url') or result.get('web_url') or url if isinstance(result, dict) else url
            return View('Action completed', text=f'{title}\n{location}\n\nUse Esc to return, then r to refresh the list or detail view.')
        return confirmation(title, f'{self.provider}: {self.name}\n{method} {url}', payload, send)

    def create(self, kind):
        fields = [Field('title', 'Title'), Field('body', 'Description (Markdown)', multiline=True)]
        if kind == 'pr':
            fields += [Field('head', 'Source branch' + (' (owner:branch for forks)' if self.github else ' (same project)')),
                       Field('base', 'Target branch', self.repo.get('default_branch') or 'main'),
                       Field('draft', 'Draft: yes / no', 'yes')]
        def submit(values):
            required(values, 'title')
            body = {'title': values['title'], 'body' if self.github else 'description': values['body']}
            if kind == 'pr':
                required(values, 'head', 'base')
                draft = choice(values['draft'], ('yes', 'no')) == 'yes'
                if self.github:
                    body.update(head=values['head'], base=values['base'], draft=draft)
                else:
                    body.update(source_branch=values['head'], target_branch=values['base'])
                    if draft and not body['title'].lower().startswith('draft:'):
                        body['title'] = 'Draft: ' + body['title']
            return self.prepare('Create ' + self.noun(kind), self.endpoint(kind), 'POST', body)
        return form('Create ' + self.noun(kind), fields, submit)

    def edit(self, kind, number, record):
        fields = [Field('title', 'Title', record['title']), Field('body', 'Description (Markdown)', record.get('body') or record.get('description') or '', True)]
        def submit(values):
            required(values, 'title')
            return self.prepare(f'Edit #{number}', self.endpoint(kind, number), 'PATCH' if self.github else 'PUT',
                                {'title': values['title'], 'body' if self.github else 'description': values['body']})
        return form(f'Edit #{number}', fields, submit)

    def assign(self, kind, number, record):
        assignees = ', '.join(str(user['login'] if self.github else user['id']) for user in record.get('assignees', []))
        milestone = record.get('milestone') or {}
        fields = [Field('labels', 'Labels (comma-separated; empty clears)', ', '.join(label_names(record))),
                  Field('assignees', 'Assignee ' + ('usernames' if self.github else 'IDs') + ' (comma-separated; empty clears)', assignees),
                  Field('milestone', 'Milestone ' + ('number' if self.github else 'ID') + ' (empty clears)', str(milestone.get('number' if self.github else 'id', '')))]
        def submit(values):
            raw = values['milestone'].strip()
            if raw and (not raw.isdecimal() or int(raw) <= 0):
                raise Error('Milestone must be a positive integer or empty.')
            if self.github:
                body = {'labels': csv(values['labels']), 'assignees': csv(values['assignees']), 'milestone': int(raw) if raw else None}
                url = self.endpoint('issue', number)
            else:
                body = {'labels': ','.join(csv(values['labels'])), 'assignee_ids': ids(values['assignees']) or [0], 'milestone_id': int(raw) if raw else 0}
                url = self.endpoint(kind, number)
            return self.prepare(f'Update metadata #{number}', url, 'PATCH' if self.github else 'PUT', body)
        return form(f'Metadata #{number}', fields, submit)

    def change_state(self, kind, number, is_open):
        payload = {'state': 'closed' if is_open else 'open'} if self.github else {'state_event': 'close' if is_open else 'reopen'}
        return self.prepare(('Close' if is_open else 'Reopen') + f' #{number}', self.endpoint(kind, number), 'PATCH' if self.github else 'PUT', payload)

    def comment_form(self, kind, number, url=None):
        url = url or (self.endpoint('issue', number) + '/comments' if self.github else self.endpoint(kind, number) + '/notes')
        def submit(values):
            required(values, 'body')
            return self.prepare(f'Comment on #{number}', url, 'POST', {'body': values['body']})
        return form(f'Comment on #{number}', [Field('body', 'Comment (Markdown)', multiline=True)], submit)

    def comments(self, kind, number, page=1):
        url = self.endpoint('issue', number) + '/comments' if self.github else self.endpoint(kind, number) + '/notes'
        records = self.get_page(url, page, **({} if self.github else {'sort': 'asc', 'order_by': 'created_at'}))
        items = []
        for record in records:
            title = f"{author(record)} · {record.get('created_at', '')}" + (' [system]' if record.get('system') else '')
            text = record.get('body') or '(Empty comment.)'
            items.append(Item(title, 'text', {'text': text, 'preamble': title, 'markdown': True,
                              'base_url': record.get('html_url') or record.get('web_url') or ''}, text.split('\n')[0]))
        return View(f'Comments #{number} — Page {page}', items, page=page, more=len(records) == PAGE_SIZE,
                    loader=lambda p: self.comments(kind, number, p), refresh=lambda: self.comments(kind, number, page))

    def metadata(self, name, page=1):
        records = self.get_page(self.base + '/' + name, page, **({'state': 'all'} if name == 'milestones' else {}))
        items = []
        for record in records:
            title = record.get('name') or record.get('title') or str(record['id'])
            ident = record.get('number') if self.github and name == 'milestones' else record.get('id')
            preamble = f"{title}\nID/number: {ident}\nState: {record.get('state', '')}"
            items.append(Item(title, 'text', {'text': record.get('description') or '(No description.)',
                              'preamble': preamble, 'markdown': name == 'milestones'}, f'ID/number: {ident}'))
        return View(name.title(), items, page=page, more=len(records) == PAGE_SIZE, loader=lambda p: self.metadata(name, p))

    def files(self, number, record, page=1):
        endpoint = self.endpoint('pr', number) + ('/files' if self.github else '/diffs')
        records = self.get_page(endpoint, page)
        items = []
        for file in records:
            name = file['filename'] if self.github else file['new_path']
            status = file.get('status') or ('new' if file.get('new_file') else 'deleted' if file.get('deleted_file') else 'renamed' if file.get('renamed_file') else 'modified')
            summary = f"+{file.get('additions', '?')} -{file.get('deletions', '?')}" if self.github else status
            items.append(self.link(f'[{status}] {name}', self.file_diff, number, record, file, summary=summary))
        title = f'Changed files #{number} — Page {page} (server diff limits may apply)'
        return View(title, items, page=page, more=len(records) == PAGE_SIZE and (not self.github or page * PAGE_SIZE < 3000),
                    loader=lambda p: self.files(number, record, p))

    def file_diff(self, number, record, file):
        name = file.get('filename') or file['new_path']
        patch = file.get('patch') if self.github else file.get('diff')
        flags = ', '.join(key for key in ('collapsed', 'too_large', 'generated_file') if file.get(key))
        text = f'{name}\n{flags}\n\n{patch or "No patch supplied (binary, empty or server-limited diff)."}\n\nDiffs may be truncated by the provider.'
        return View(name, [Item('Read diff', 'text', {'text': text}),
                           self.link('Comment on a diff line…', self.line_comment, number, record, file)])

    def line_comment(self, number, record, file):
        fields = [Field('line', 'Line number in the diff'), Field('side', 'Side: RIGHT (new) / LEFT (old)', 'RIGHT'),
                  Field('body', 'Comment (Markdown)', multiline=True)]
        def submit(values):
            required(values, 'line', 'body')
            if not values['line'].isdecimal() or int(values['line']) <= 0:
                raise Error('Line must be a positive integer present in the diff.')
            side = choice(values['side'], ('LEFT', 'RIGHT'))
            line = int(values['line'])
            if self.github:
                payload = {'body': values['body'], 'path': file['filename'], 'line': line, 'side': side, 'commit_id': record['head']['sha']}
                endpoint = self.endpoint('pr', number) + '/comments'
            else:
                refs = record.get('diff_refs') or {}
                if not all(refs.get(key) for key in ('base_sha', 'start_sha', 'head_sha')):
                    raise Error('Diff references are not ready. Refresh the merge request.')
                position = dict(refs, position_type='text', old_path=file['old_path'], new_path=file['new_path'])
                position['new_line' if side == 'RIGHT' else 'old_line'] = line
                payload = {'body': values['body'], 'position': position}
                endpoint = self.endpoint('pr', number) + '/discussions'
            return self.prepare(f'Comment on diff #{number}', endpoint, 'POST', payload)
        return form(f'Line comment #{number}', fields, submit)

    def commits(self, number, page=1):
        records = self.get_page(self.endpoint('pr', number) + '/commits', page)
        items = []
        for record in records:
            commit = record.get('commit') or record
            sha = record.get('sha') or record.get('id')
            message = commit.get('message') or ''
            who = (commit.get('author') or {}).get('name', '') if self.github else record.get('author_name', '')
            items.append(Item(f'{sha[:10]} {message.splitlines()[0] if message else ""}', 'text',
                              {'text': f'{sha}\nAuthor: {who}\n\n{message}'}))
        return View(f'Commits #{number}' + (' (GitHub limit: 250)' if self.github else ''), items, page=page,
                    more=len(records) == PAGE_SIZE and (not self.github or page * PAGE_SIZE < 250), loader=lambda p: self.commits(number, p))

    def reviews(self, number, page=1):
        if not self.github:
            record = self.client.json(self.endpoint('pr', number) + '/approvals')
            approved = ', '.join(entry['user']['username'] for entry in record.get('approved_by', []))
            return View(f'Approvals #{number}', text=f"Approved by: {approved or 'none'}\nRequired: {record.get('approvals_required', 'not supplied')}\nRemaining: {record.get('approvals_left', 'not supplied')}", refresh=lambda: self.reviews(number))
        records = self.get_page(self.endpoint('pr', number) + '/reviews', page)
        items = [Item(f"{author(record)} [{record['state']}] {record.get('submitted_at') or ''}", 'text',
                      {'text': record.get('body') or '(No review body.)', 'markdown': True,
                       'preamble': f"{author(record)} · {record['state']}\nCommit: {record.get('commit_id')}",
                       'base_url': record.get('html_url') or ''}) for record in records]
        return View(f'Reviews #{number}', items, page=page, more=len(records) == PAGE_SIZE, loader=lambda p: self.reviews(number, p), refresh=lambda: self.reviews(number, page))

    def discussions(self, number, page=1):
        records = self.get_page(self.endpoint('pr', number) + ('/comments' if self.github else '/discussions'), page)
        items = []
        for record in records:
            first = record if self.github else record['notes'][0]
            title = f"{author(first)} · {first.get('path') or (first.get('position') or {}).get('new_path') or 'Discussion'}"
            if first.get('resolved'):
                title += ' [resolved]'
            items.append(self.link(title, self.thread, number, record, summary=(first.get('body') or '').split('\n')[0]))
        return View(f'Review discussions #{number}', items, page=page, more=len(records) == PAGE_SIZE,
                    loader=lambda p: self.discussions(number, p), refresh=lambda: self.discussions(number, page))

    def thread(self, number, record):
        notes = [record] if self.github else record['notes']
        def note_text(note):
            heading = f"### {author(note)} · {note.get('created_at', '')}\n\n"
            code = f"{note.get('path') or ''}:{note.get('line') or ''}\n{note.get('diff_hunk') or ''}"
            fence = '`' * max(3, max((len(run) for run in re.findall(r'`+', code)), default=0) + 1)
            diff = f'{fence}diff\n{code}\n{fence}\n\n' if note.get('diff_hunk') else ''
            return heading + diff + (note.get('body') or '')
        text = '\n\n---\n\n'.join(note_text(note) for note in notes)
        base = self.endpoint('pr', number)
        if self.github:
            # GitHub replies must target the root comment, not another reply.
            url = base + f"/comments/{int(record.get('in_reply_to_id') or record['id'])}/replies"
        else:
            url = base + '/discussions/' + quote(str(record['id']), safe='') + '/notes'
        items = [Item('Read discussion', 'text', {'text': text, 'markdown': True}), self.link('Reply…', self.comment_form, 'pr', number, url)]
        if not self.github and any(note.get('resolvable') for note in notes):
            resolved = all(note.get('resolved') for note in notes if note.get('resolvable'))
            items.append(self.link('Unresolve discussion…' if resolved else 'Resolve discussion…', self.prepare,
                                   'Unresolve discussion' if resolved else 'Resolve discussion', url.rsplit('/', 1)[0], 'PUT', {'resolved': not resolved}))
        return View(f'Discussion #{number}', items)

    def reviewers(self, number):
        field = Field('reviewers', 'Reviewer usernames (comma-separated)' if self.github else 'Reviewer IDs (comma-separated; replaces current reviewers)')
        def submit(values):
            required(values, 'reviewers')
            url = self.endpoint('pr', number) + ('/requested_reviewers' if self.github else '')
            payload = {'reviewers': csv(values['reviewers'])} if self.github else {'reviewer_ids': ids(values['reviewers'])}
            return self.prepare(f'Request reviewers #{number}', url, 'POST' if self.github else 'PUT', payload)
        return form(f'Reviewers #{number}', [field], submit)

    def review(self, number, record):
        sha = record['head']['sha'] if self.github else record.get('sha')
        if not sha:
            raise Error('Head commit is unavailable. Refresh the request.')
        if not self.github:
            return self.prepare(f'Approve #{number}', self.endpoint('pr', number) + '/approve', 'POST', {'sha': sha})
        fields = [Field('event', 'Review: COMMENT / APPROVE / REQUEST_CHANGES', 'COMMENT'), Field('body', 'Review body (Markdown)', multiline=True)]
        def submit(values):
            event = choice(values['event'], ('COMMENT', 'APPROVE', 'REQUEST_CHANGES'))
            if event != 'APPROVE':
                required(values, 'body')
            return self.prepare(f'{event} #{number}', self.endpoint('pr', number) + '/reviews', 'POST',
                                {'event': event, 'body': values['body'], 'commit_id': sha})
        return form(f'Review #{number}', fields, submit)

    def change_draft(self, number, record):
        ready = bool(record.get('draft') or record.get('work_in_progress'))
        title = ('Mark ready for review' if ready else 'Convert to draft') + f' #{number}'
        if self.github:
            node = record.get('node_id')
            if not node:
                raise Error('Pull request node ID missing. Refresh before changing draft status.')
            mutation = 'markPullRequestReadyForReview' if ready else 'convertPullRequestToDraft'
            payload = {'query': 'mutation($id: ID!) { ' + mutation + '(input: {pullRequestId: $id}) { pullRequest { id isDraft } } }',
                       'variables': {'id': node}}
            return self.prepare(title, 'https://api.github.com/graphql', 'POST', payload)
        # GitLab exposes draft status through its title prefix.
        current = re.sub(r'^(?:Draft:|WIP:|\[Draft\]|\[WIP\]|\(Draft\)|\(WIP\))\s*', '', record['title'], flags=re.I)
        return self.prepare(title, self.endpoint('pr', number), 'PUT', {'title': current if ready else 'Draft: ' + current})

    def merge(self, number, record):
        sha = record['head']['sha'] if self.github else record.get('sha')
        if not sha:
            raise Error('Head commit is unavailable. Refresh the request.')
        if record.get('draft') or record.get('work_in_progress'):
            raise Error('This is a draft. Mark it ready before merging.')
        fields = [Field('method', 'Method: merge / squash / rebase', 'merge')] if self.github else [Field('squash', 'Squash: yes / no', 'no')]
        def submit(values):
            payload = {'sha': sha}
            if self.github:
                payload['merge_method'] = choice(values['method'], ('merge', 'squash', 'rebase'))
            else:
                payload['squash'] = choice(values['squash'], ('yes', 'no')) == 'yes'
            return self.prepare(f'Merge #{number} at {sha}', self.endpoint('pr', number) + '/merge', 'PUT', payload)
        return form(f'Merge #{number} — reviewed head {sha[:12]}', fields, submit)

    def checks(self, number, record):
        if self.github:
            sha = record['head']['sha']
            return View(f'CI #{number} — {sha[:12]}', [
                self.link('Check runs', self.check_runs, sha),
                self.link('Commit statuses', self.statuses, sha)])
        return self.pipelines(number)

    def check_runs(self, sha, page=1):
        data = self.get_page(self.base + '/commits/' + quote(sha, safe='') + '/check-runs', page)
        items = []
        for run in data['check_runs']:
            output = run.get('output') or {}
            preamble = f"{run['name']}\nStatus: {run['status']}\nConclusion: {run.get('conclusion')}\n{run.get('details_url') or ''}\n{output.get('title') or ''}"
            text = (output.get('summary') or '') + '\n\n' + (output.get('text') or '')
            items.append(Item(f"{run['name']} [{run.get('conclusion') or run['status']}]", 'text',
                              {'text': text, 'preamble': preamble, 'markdown': True, 'base_url': run.get('details_url') or ''}))
        return View('Check runs', items, page=page, more=page * PAGE_SIZE < data.get('total_count', 0), loader=lambda p: self.check_runs(sha, p), refresh=lambda: self.check_runs(sha, page))

    def statuses(self, sha, page=1):
        records = self.get_page(self.base + '/commits/' + quote(sha, safe='') + '/statuses', page)
        items = [Item(f"{record['context']} [{record['state']}]", 'text', {'text': f"{record.get('description') or ''}\n{record.get('target_url') or ''}"}) for record in records]
        return View('Commit statuses', items, page=page, more=len(records) == PAGE_SIZE, loader=lambda p: self.statuses(sha, p), refresh=lambda: self.statuses(sha, page))

    def pipelines(self, number, page=1):
        records = self.get_page(self.endpoint('pr', number) + '/pipelines', page)
        items = [self.link(f"Pipeline {record['id']} [{record['status']}] {record.get('sha', '')[:12]}", self.jobs,
                           record.get('project_id') or self.repo['id'], record['id']) for record in records]
        return View(f'Pipelines #{number}', items, page=page, more=len(records) == PAGE_SIZE, loader=lambda p: self.pipelines(number, p), refresh=lambda: self.pipelines(number, page))

    def jobs(self, project_id, pipeline_id, page=1):
        base = f'https://gitlab.com/api/v4/projects/{int(project_id)}'
        records = self.get_page(base + f'/pipelines/{int(pipeline_id)}/jobs', page)
        items = [Item(f"{record['name']} [{record['status']}]", 'text', {'text': f"Stage: {record.get('stage')}\nDuration: {record.get('duration')}\n{record.get('web_url') or ''}"}) for record in records]
        return View(f'Pipeline {pipeline_id} jobs', items, page=page, more=len(records) == PAGE_SIZE, loader=lambda p: self.jobs(project_id, pipeline_id, p), refresh=lambda: self.jobs(project_id, pipeline_id, page))
