"""Provider contracts and UI write workflows; no requests leave these tests."""
import curses
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from grabber.editor import Editor
from grabber.forms import Field, confirmation, form
from grabber.forge import Forge
from grabber.network import Client, Error, Redirects
from grabber.providers import Browser
from grabber.tui import App


class ClientFixture:
    def __init__(self, response=None):
        self.response = response
        self.reads = []
        self.writes = []

    def json(self, url, **kwargs):
        self.reads.append(url)
        return self.response

    def write_json(self, url, method, payload):
        self.writes.append((url, method, payload))
        return {'html_url': 'https://example.org/result'}


class ForgeTests(unittest.TestCase):
    def setUp(self):
        self.client = ClientFixture()
        self.browser = Browser(self.client)
        self.gh = Forge(self.browser, 'GitHub', {'full_name': 'owner/repo', 'default_branch': 'main'})
        self.gl = Forge(self.browser, 'GitLab', {'id': 42, 'path_with_namespace': 'group/sub/repo', 'default_branch': 'main'})
        self.record = {'title': 'Fix bug', 'state': 'open', 'number': 7, 'iid': 7,
                       'head': {'sha': 'abc123', 'label': 'owner:fix'}, 'base': {'label': 'owner:main'},
                       'sha': 'def456', 'diff_refs': {'base_sha': 'a', 'start_sha': 'b', 'head_sha': 'def456'}}

    def submit(self, draft, values):
        complete = {field.key: field.value for field in draft.context['fields']}
        complete.update(values)
        return draft.context['submit'](complete)

    def send(self, preview):
        return self.browser.activate(preview.items[-1])

    def test_github_search_is_scoped_and_separates_prs(self):
        self.client.response = {'items': [self.record], 'total_count': 31}
        view = self.gh.listing('issue', {'state': 'open', 'labels': 'good first issue', 'assignee': 'alice', 'query': 'crash'})
        query = parse_qs(urlsplit(self.client.reads[-1]).query)['q'][0]
        self.assertIn('repo:owner/repo is:issue is:open', query)
        self.assertIn('label:"good first issue"', query)
        self.assertIn('assignee:alice', query)
        self.assertTrue(view.more)
        view.loader(2)
        self.assertIn('page=2', self.client.reads[-1])
        self.gh.listing('pr', {'state': 'merged'})
        self.assertIn('is:pr is:merged', parse_qs(urlsplit(self.client.reads[-1]).query)['q'][0])

    def test_gitlab_mapping_and_iid(self):
        self.client.response = [dict(self.record, id=99999, state='opened')]
        view = self.gl.listing('issue', {'state': 'open', 'author': 'alice', 'assignee': '123'})
        url = self.client.reads[-1]
        self.assertIn('/projects/42/issues?', url)
        self.assertIn('state=opened', url)
        self.assertIn('assignee_id=123', url)
        self.client.response = self.record
        self.browser.activate(view.items[-1])
        self.assertTrue(self.client.reads[-1].endswith('/issues/7'))

    def test_detail_contains_pr_features(self):
        self.client.response = self.record
        view = self.gh.detail('pr', 7)
        titles = [item.title for item in view.items]
        for title in ['Comments', 'Changed files and diffs', 'Commits', 'Reviews / approvals',
                      'Review discussions', 'CI checks / pipelines', 'Request reviewers…', 'Merge…']:
            self.assertIn(title, titles)
        self.client.response = dict(self.record, merged=True)
        titles = [item.title for item in self.gh.detail('pr', 7).items]
        self.assertNotIn('Merge…', titles)
        self.assertNotIn('Close…', titles)

    def test_create_drafts_do_not_write_before_confirmation(self):
        for forge in (self.gh, self.gl):
            with self.subTest(provider=forge.provider):
                draft = forge.create('pr')
                before = len(self.client.writes)
                preview = self.submit(draft, {'title': 'Feature', 'body': 'First\nSecond', 'head': 'feature'})
                self.assertEqual(len(self.client.writes), before)
                self.send(preview)
                url, method, payload = self.client.writes[-1]
                self.assertEqual(method, 'POST')
                if forge.github:
                    self.assertTrue(url.endswith('/pulls'))
                    self.assertEqual(payload['head'], 'feature')
                    self.assertTrue(payload['draft'])
                else:
                    self.assertTrue(url.endswith('/merge_requests'))
                    self.assertEqual(payload['source_branch'], 'feature')
                    self.assertEqual(payload['title'], 'Draft: Feature')

    def test_validation_leaves_remote_unchanged(self):
        with self.assertRaises(Error):
            self.submit(self.gh.create('issue'), {'title': ''})
        with self.assertRaises(Error):
            self.submit(self.gl.assign('issue', 7, self.record), {'assignees': 'alice'})
        with self.assertRaises(Error):
            self.submit(self.gh.review(7, self.record), {'event': 'REQUEST_CHANGES', 'body': ''})
        self.assertEqual(self.client.writes, [])

    def test_github_pr_metadata_uses_issue_endpoint(self):
        preview = self.submit(self.gh.assign('pr', 7, self.record), {'labels': 'bug, urgent', 'assignees': 'alice,bob', 'milestone': ''})
        self.send(preview)
        url, method, payload = self.client.writes[-1]
        self.assertTrue(url.endswith('/issues/7'))
        self.assertEqual(method, 'PATCH')
        self.assertEqual(payload, {'labels': ['bug', 'urgent'], 'assignees': ['alice', 'bob'], 'milestone': None})

    def test_gitlab_metadata_clears_and_state_changes(self):
        self.send(self.submit(self.gl.assign('pr', 7, self.record), {'labels': '', 'assignees': '', 'milestone': ''}))
        self.assertEqual(self.client.writes[-1][2], {'labels': '', 'assignee_ids': [0], 'milestone_id': 0})
        self.send(self.gl.change_state('pr', 7, True))
        self.assertEqual(self.client.writes[-1][2], {'state_event': 'close'})
        self.send(self.gh.change_state('issue', 7, False))
        self.assertEqual(self.client.writes[-1][2], {'state': 'open'})

    def test_merge_and_review_bind_to_displayed_sha(self):
        for forge, sha in [(self.gh, 'abc123'), (self.gl, 'def456')]:
            preview = self.submit(forge.merge(7, self.record), {})
            self.send(preview)
            self.assertEqual(self.client.writes[-1][2]['sha'], sha)
            self.assertEqual(self.client.writes[-1][1], 'PUT')
        preview = self.submit(self.gh.review(7, self.record), {'event': 'APPROVE'})
        self.send(preview)
        self.assertEqual(self.client.writes[-1][2]['commit_id'], 'abc123')
        self.send(self.gl.review(7, self.record))
        self.assertEqual(self.client.writes[-1][2], {'sha': 'def456'})
        with self.assertRaises(Error):
            self.gh.merge(7, dict(self.record, draft=True))

    def test_comments_and_review_reply_endpoints(self):
        self.send(self.submit(self.gh.comment_form('pr', 7), {'body': 'Comment'}))
        self.assertTrue(self.client.writes[-1][0].endswith('/issues/7/comments'))
        thread = self.gh.thread(7, {'id': 55, 'in_reply_to_id': 44, 'body': 'Reply'})
        draft = self.browser.activate(thread.items[1])
        self.send(self.submit(draft, {'body': 'Follow-up'}))
        self.assertTrue(self.client.writes[-1][0].endswith('/pulls/7/comments/44/replies'))
        thread = self.gl.thread(7, {'id': 'abc', 'notes': [{'body': 'Fix', 'resolvable': True, 'resolved': False}]})
        self.send(self.browser.activate(thread.items[-1]))
        self.assertTrue(self.client.writes[-1][0].endswith('/merge_requests/7/discussions/abc'))
        self.assertEqual(self.client.writes[-1][2], {'resolved': True})

    def test_inline_comments_and_diff_limits(self):
        file = {'filename': 'a.py', 'new_path': 'a.py', 'old_path': 'old.py'}
        view = self.gh.file_diff(7, self.record, file)
        self.assertIn('server-limited', view.items[0].data['text'])
        for forge in (self.gh, self.gl):
            self.send(self.submit(forge.line_comment(7, self.record, file), {'line': '12', 'body': 'Fix this', 'side': 'LEFT'}))
            payload = self.client.writes[-1][2]
            if forge.github:
                self.assertEqual(payload['side'], 'LEFT')
                self.assertEqual(payload['commit_id'], 'abc123')
            else:
                self.assertEqual(payload['position']['old_line'], 12)
                self.assertEqual(payload['position']['head_sha'], 'def456')

    def test_rejected_merge_and_graphql_errors_are_not_success(self):
        for result in [{'merged': False, 'message': 'Head changed'}, {'errors': [{'message': 'Forbidden'}]}]:
            with self.subTest(result=result):
                with patch.object(self.client, 'write_json', return_value=result):
                    with self.assertRaises(Error):
                        self.send(self.gh.prepare('Action', self.gh.endpoint('pr', 7), 'PUT', {}))

    def test_draft_transitions(self):
        self.send(self.gh.change_draft(7, dict(self.record, node_id='PR_123', draft=True)))
        url, method, payload = self.client.writes[-1]
        self.assertEqual(url, 'https://api.github.com/graphql')
        self.assertIn('markPullRequestReadyForReview', payload['query'])
        self.assertEqual(payload['variables'], {'id': 'PR_123'})
        self.send(self.gl.change_draft(7, dict(self.record, title='Draft: Fix bug', draft=True)))
        self.assertEqual(self.client.writes[-1][2], {'title': 'Fix bug'})
        self.send(self.gl.change_draft(7, self.record))
        self.assertEqual(self.client.writes[-1][2], {'title': 'Draft: Fix bug'})

    def test_check_runs_pagination(self):
        self.client.response = {'total_count': 31, 'check_runs': [{'name': 'CI', 'status': 'completed', 'conclusion': 'failure', 'output': {'summary': 'Tests failed'}}]}
        view = self.gh.check_runs('abc123')
        self.assertTrue(view.more)
        self.assertIn('failure', view.items[0].title)
        self.assertIn('Tests failed', view.items[0].data['text'])


class ConfirmationTests(unittest.TestCase):
    def test_payload_snapshot_and_single_use(self):
        writes = []
        data = {'body': 'Original'}
        preview = confirmation('Post', 'repo', data, lambda payload: writes.append(payload))
        data['body'] = 'Changed later'
        preview.items[-1].data['callback']()
        self.assertEqual(writes, [{'body': 'Original'}])
        with self.assertRaises(Error):
            preview.items[-1].data['callback']()
        self.assertEqual(len(writes), 1)

    def test_preview_preserves_readable_multiline_body(self):
        preview = confirmation('Comment', 'owner/repo #7', {'body': 'First line\nSecond line'}, lambda payload: None)
        self.assertIn('First line\nSecond line', preview.items[0].data['text'])

    def test_failed_request_cannot_be_blindly_repeated(self):
        def failed(payload):
            raise Error('Connection lost')
        preview = confirmation('Post', 'repo', {}, failed)
        with self.assertRaisesRegex(Error, 'Check the remote state'):
            preview.items[-1].data['callback']()
        with self.assertRaisesRegex(Error, 'already been used'):
            preview.items[-1].data['callback']()


class EditorTests(unittest.TestCase):
    def test_multiline_save_discard_and_navigation(self):
        saved = []
        editor = Editor(Field('body', 'Body', '', True), saved.append)
        for key in 'Hello\nworld':
            editor.handle(key)
        editor.handle(curses.KEY_HOME)
        editor.handle(curses.KEY_UP)
        self.assertEqual(editor.position, 0)
        editor.handle(curses.KEY_END)
        editor.handle('!')
        self.assertEqual(editor.handle('\x13'), 'save')
        self.assertEqual(saved, ['Hello!\nworld'])
        editor = Editor(Field('title', 'Title', 'old'), saved.append)
        editor.handle('x')
        self.assertEqual(editor.handle('\x1b'), 'cancel')
        self.assertEqual(saved, ['Hello!\nworld'])

    def test_form_ui_preserves_draft_on_validation_failure(self):
        app = App(None, Path('/tmp'))
        def submit(values):
            raise Error('Title is required')
        draft = form('New issue', [Field('title', 'Title')], submit)
        app.stack.append(draft)
        app.key('\n')
        self.assertIsNotNone(app.editor)
        app.key('a')
        app.key('\x13')
        self.assertEqual(app.view.context['fields'][0].value, 'a')
        app.view.selected = 1
        app.key('\n')
        app.worker.join()
        app.poll()
        self.assertIn('Title is required', app.status)
        self.assertEqual(app.view.context['fields'][0].value, 'a')


class TransportTests(unittest.TestCase):
    def test_write_auth_method_and_json(self):
        client = Client()
        requests = []
        class Response(io.BytesIO):
            headers = {}
        def opened(request, timeout):
            requests.append(request)
            return Response(b'{"id": 1}')
        client.opener.open = opened
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'gh', 'GITLAB_TOKEN': 'gl'}):
            client.write_json('https://api.github.com/repos/a/b/issues', 'POST', {'title': 'Test'})
            client.write_json('https://gitlab.com/api/v4/projects/1/issues/2', 'PUT', {'state_event': 'close'})
        self.assertEqual(requests[0].get_method(), 'POST')
        self.assertEqual(json.loads(requests[0].data), {'title': 'Test'})
        self.assertEqual(requests[0].get_header('Authorization'), 'Bearer gh')
        self.assertEqual(requests[1].get_header('Private-token'), 'gl')

    def test_write_guards_and_redirects(self):
        client = Client()
        with patch.dict(os.environ, {'GITHUB_TOKEN': ''}):
            with self.assertRaisesRegex(Error, 'GITHUB_TOKEN'):
                client.write_json('https://api.github.com/repos/a/b/issues', 'POST', {})
        with self.assertRaises(Error):
            client.write_json('https://example.org/repos/a/b', 'POST', {})
        request = Request('https://api.github.com/repos/a/b/issues', data=b'{}', method='POST')
        with self.assertRaisesRegex(Error, 'redirected'):
            Redirects().redirect_request(request, None, 302, '', {}, 'https://example.org/')


if __name__ == '__main__':
    unittest.main()
