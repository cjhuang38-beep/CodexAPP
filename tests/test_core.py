import base64
import csv
import json
from datetime import date, datetime, timezone
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from google.auth.exceptions import RefreshError, TransportError

from gmail_summary.ai import OpenAICompatibleService, validate_endpoint
from gmail_summary.auth import AuthService, ReauthRequired, SCOPES, TOKEN_SLOT
from gmail_summary.csvio import COLUMNS, export_csv, import_csv, safe_cell
from gmail_summary.demo import DEMO_ACCOUNT, DemoAnalyzer, DemoMailbox
from gmail_summary.gmail import GmailService, extract_body, parse_message
from gmail_summary.models import MailStore, Message, Search
from gmail_summary.security import SecretStore, api_key_slot
from gmail_summary.settings import Settings
from gmail_summary.workers import JobRunner, analyze_batch


@pytest.fixture
def msg():
    return Message('m1', 't1', 'user@example.invalid', '2026-10-17T10:00:00+08:00', '王老師',
                   'teacher@example.invalid', '同一個主旨', body='請於2026年10月30日前回覆。')


def part(text, mime='text/plain', **kwargs):
    return {'mimeType': mime, 'body': {'data': base64.urlsafe_b64encode(text.encode()).decode()}, **kwargs}


def test_taipei_inclusive_end():
    q = Search(date(2026, 10, 1), date(2026, 10, 7), advanced='a OR b').query()
    start = int(datetime(2026, 9, 30, 16, tzinfo=timezone.utc).timestamp())
    end = int(datetime(2026, 10, 7, 16, tzinfo=timezone.utc).timestamp())
    assert q == f'after:{start} before:{end} (a OR b)'


@pytest.mark.parametrize('criteria', [Search(date(2026, 2, 2), date(2026, 2, 1)), Search(limit=0), Search(limit=5001)])
def test_invalid_search(criteria):
    with pytest.raises(ValueError):
        criteria.query()


def test_query_fields():
    query = Search(sender='a"b@example.invalid', subject='標題', unread=True).query()
    assert 'from:"a\\"b@example.invalid"' in query
    assert 'subject:"標題"' in query
    assert 'is:unread' in query


def test_store_dedupe_manual_and_isolation(msg):
    store = MailStore()
    store.switch(msg.account)
    assert store.add(msg)
    assert not store.add(msg)
    second = Message('m2', 't2', msg.account, '', '', '', msg.subject)
    assert store.add(second)
    assert len(store.messages) == 2
    store.edit(msg.key, summary='人工摘要', todos='人工待辦', category='帳務')
    assert store.dirty
    store.mark_exported()
    assert not store.dirty
    assert store.messages[msg.key].summary == '人工摘要'
    with pytest.raises(ValueError):
        store.edit(msg.key, body='修改原文')
    store.switch('other@example.invalid')
    assert not store.messages and not store.dirty
    with pytest.raises(ValueError):
        store.add(msg)


def test_mime_nested_alternatives_html_attachments():
    payload = {'mimeType': 'multipart/mixed', 'parts': [
        {'mimeType': 'multipart/alternative', 'parts': [part('<p>HTML 版本</p>', 'text/html'), part('純文字版本')]},
        part('秘密附件', filename='secret.txt'),
        part('<p>補充內容</p><img src="https://track.invalid/a"><script>追蹤()</script>', 'text/html')
    ]}
    body, note = extract_body(payload)
    assert body == '純文字版本\n\n補充內容'
    assert not note and '秘密' not in body and '追蹤' not in body and 'HTML' not in body


def test_mime_body_attachment_not_downloaded():
    body, note = extract_body({'mimeType': 'text/plain', 'body': {'attachmentId': 'x'}})
    assert not body and '未自動下載' in note


def test_snippet_labeled():
    msg = parse_message({'id': '1', 'snippet': '僅片段', 'internalDate': '0', 'payload': {}}, 'a')
    assert msg.body == '僅片段' and '僅有片段' in msg.limitation


def test_pagination_dedup_and_limit(monkeypatch):
    service = GmailService.__new__(GmailService)
    service.account = 'a'
    api = Mock()
    service.api = api
    pages = [
        {'messages': [{'id': '1'}, {'id': '2'}], 'nextPageToken': 'p2'},
        {'messages': [{'id': '2'}, {'id': '3'}], 'nextPageToken': 'p3'},
        {'messages': [{'id': '4'}, {'id': '5'}]},
    ]
    calls = []
    api.users.return_value.messages.return_value.list.side_effect = lambda **kwargs: ('list', kwargs)
    api.users.return_value.messages.return_value.get.side_effect = lambda **kwargs: ('get', kwargs)

    def execute(request):
        calls.append(request)
        if request[0] == 'list':
            return pages.pop(0)
        return {'id': request[1]['id'], 'payload': part('完整正文'), 'internalDate': '1'}
    service.execute = execute
    events = []
    service.search(Search(limit=4), Event(), lambda k, v: events.append((k, v)))
    assert [v.id for k, v in events if k == 'message'] == ['1', '2', '3', '4']
    assert len([c for c in calls if c[0] == 'list']) == 3
    assert all(c[1]['format'] == 'full' for c in calls if c[0] == 'get')
    assert not api.users.return_value.messages.return_value.modify.called


def test_search_cancellation_and_partial_failure():
    service = GmailService.__new__(GmailService)
    service.account = 'a'
    service.api = Mock()
    service.api.users.return_value.messages.return_value.list.return_value = 'list'
    service.api.users.return_value.messages.return_value.get.side_effect = lambda **kw: kw['id']
    cancel = Event()

    def execute(request):
        if request == 'list':
            return {'messages': [{'id': 'bad'}, {'id': 'good'}, {'id': 'never'}]}
        if request == 'bad':
            raise RuntimeError('network')
        return {'id': request, 'payload': part('正文')}
    service.execute = execute
    results = []

    def emit(kind, value):
        if kind == 'message':
            results.append(value)
            if value.id == 'good':
                cancel.set()
    service.search(Search(), cancel, emit)
    assert [m.id for m in results] == ['bad', 'good']
    assert results[0].status == '讀取失敗' and results[1].body == '正文'


def test_csv_bom_roundtrip_edits_and_formulas(tmp_path, msg):
    msg.subject = '=HYPERLINK("bad","逗號,引號")\n第二行'
    msg.summary = '人工修改摘要, "保留引號"\n下一行'
    msg.todos = '  +SUM(1,2)'
    msg.deadline = "'原本的引號"
    file = tmp_path / 'result.csv'
    export_csv(file, [msg])
    assert file.read_bytes().startswith(b'\xef\xbb\xbf')
    with file.open(encoding='utf-8-sig', newline='') as f:
        row = next(csv.DictReader(f))
    assert row['主旨'].startswith("'=") and row['待辦事項'].startswith("'  +")
    restored = import_csv(file)[0]
    assert restored.subject == msg.subject
    assert restored.summary == msg.summary
    assert restored.todos == msg.todos and restored.deadline == msg.deadline
    assert restored.id == msg.id and restored.account == msg.account
    assert not restored.body and '無原始正文' in restored.limitation
    assert list(row) == list(COLUMNS)


@pytest.mark.parametrize('value', ['=1+2', '+test', '-1', '@evil', '\t=1', '\n=1', '\r=1', '  =1', "'value"])
def test_csv_formula_prefix(value):
    assert safe_cell(value) == "'" + value


def test_invalid_csv_atomic(tmp_path, msg):
    file = tmp_path / 'a.csv'
    file.write_text('主旨,摘要\na,b\n', encoding='utf-8')
    with pytest.raises(ValueError):
        import_csv(file)


def test_preferences_only_public(tmp_path):
    path = tmp_path / 'prefs.json'
    settings = Settings()
    settings.save(path)
    data = json.loads(path.read_text())
    assert set(data) == {'appearance', 'categories', 'provider', 'model', 'base_url', 'body_limit'}
    assert Settings.load(path) == settings
    path.write_text('{invalid')
    assert Settings.load(path) == Settings()


def test_keyring_refuses_plaintext(monkeypatch):
    monkeypatch.setattr('gmail_summary.security.keyring.get_keyring', lambda: SimpleNamespace())
    with pytest.raises(RuntimeError, match='keyring'):
        SecretStore().set('token', 'not-a-real-token')
    assert api_key_slot('A', 'https://a.test') != api_key_slot('A', 'https://b.test')


def test_refresh_revocation_and_network_preservation(tmp_path):
    secrets = Mock()
    auth = AuthService(secrets, tmp_path / 'none')
    creds = Mock(valid=False, refresh_token='fake')
    creds.refresh.side_effect = RefreshError('invalid_grant')
    with pytest.raises(ReauthRequired):
        auth.ensure(creds)
    secrets.delete.assert_called_with(TOKEN_SLOT)
    secrets.reset_mock()
    creds.refresh.side_effect = TransportError('offline')
    with pytest.raises(RuntimeError, match='保留憑證'):
        auth.ensure(creds)
    secrets.delete.assert_not_called()


def test_refresh_success_is_saved(tmp_path):
    secrets = Mock()
    auth = AuthService(secrets, tmp_path / 'none')
    creds = Mock(valid=False, refresh_token='fake')
    creds.to_json.return_value = '{"fake":"test-only"}'
    assert auth.ensure(creds) is creds
    secrets.set.assert_called_once_with(TOKEN_SLOT, creds.to_json())


def test_restore_scope_mismatch(tmp_path):
    secrets = Mock()
    secrets.get.return_value = json.dumps({'scopes': ['too-broad']})
    with pytest.raises(ReauthRequired):
        AuthService(secrets, tmp_path / 'none').restore()
    assert SCOPES == ['https://www.googleapis.com/auth/gmail.readonly']


def test_missing_oauth_developer_config(tmp_path):
    with pytest.raises(RuntimeError, match='開發者尚未配置'):
        AuthService(Mock(), tmp_path / 'missing').login(Event())


def test_demo_common_pipeline_failure_retry_cancel():
    mailbox = DemoMailbox()
    events = []
    mailbox.search(Search(), Event(), lambda k, v: events.append((k, v)))
    messages = [v for k, v in events if k == 'message']
    assert len(messages) == 18 and all(m.synthetic and m.account == DEMO_ACCOUNT for m in messages)
    analyzer = DemoAnalyzer(simulate_failure=True)
    results = []
    from gmail_summary.models import DEFAULT_CATEGORIES
    analyze_batch(messages[:5], analyzer, DEFAULT_CATEGORIES, Event(), lambda k, v: results.append((k, v)))
    assert sum(k == 'analysis' for k, v in results) == 4
    assert sum(k == 'analysis_failed' for k, v in results) == 1
    results.clear()
    analyze_batch([messages[3]], analyzer, DEFAULT_CATEGORIES, Event(), lambda k, v: results.append((k, v)))
    assert any(k == 'analysis' for k, v in results)
    cancel = Event()
    results.clear()

    def emit(k, v):
        results.append((k, v))
        if k == 'analysis':
            cancel.set()
    analyze_batch(messages[:3], analyzer, DEFAULT_CATEGORIES, cancel, emit)
    assert sum(k == 'analysis' for k, v in results) == 1


def test_job_queue_and_reauth():
    runner = JobRunner()
    runner.start(lambda cancel, emit: emit('sample', 'payload'))
    runner.thread.join(timeout=3)
    assert not runner.busy
    assert runner.queue.get_nowait()[1:] == ('sample', 'payload')
    assert runner.queue.get_nowait()[1:] == ('done', False)

    def reauth(cancel, emit):
        raise ReauthRequired('請重新登入')
    runner.start(reauth)
    runner.thread.join(timeout=3)
    assert runner.queue.get_nowait()[1] == 'reauth'


@pytest.mark.parametrize('url', ['http://example.invalid', 'https://u:p@example.invalid', 'https://example.invalid?k=v'])
def test_ai_endpoint_rejects_insecure_urls(url):
    with pytest.raises(ValueError):
        validate_endpoint(url)


def test_ai_payload_truncation_validation_and_no_tools(monkeypatch, msg):
    recorded = {}
    output = {'category': '帳務', 'summary': '請於期限前回覆。', 'todos': '回覆',
              'deadline': '2026年10月30日', 'deadline_quote': '請於2026年10月30日前回覆。', 'limitation': ''}

    def post(client, url, **kwargs):
        recorded.update(kwargs['json'])
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(output)}}]},
                              request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx.Client, 'post', post)
    adapter = OpenAICompatibleService('test', 'https://model.example.invalid/v1', 'test', 'fake')
    result = adapter.analyze(msg, ['帳務'], Event())
    assert result.deadline == '2026年10月30日'
    assert 'tools' not in recorded and recorded['messages'][0]['role'] == 'system'
    payload = json.loads(recorded['messages'][1]['content'])
    assert payload['body'] == msg.body and 'account' not in payload
    output['deadline'] = '2030年1月1日'
    with pytest.raises(ValueError, match='原文引句'):
        adapter.analyze(msg, ['帳務'], Event())
    output['deadline'] = ''
    msg.body = 'a' * 1200
    adapter.body_limit = 1000
    result = adapter.analyze(msg, ['帳務'], Event())
    assert '僅分析前段' in result.limitation


def test_imported_no_body_cannot_analyze(msg):
    msg.body = ''
    adapter = OpenAICompatibleService('test', 'https://example.invalid', 'test', 'fake')
    with pytest.raises(ValueError, match='沒有原始正文'):
        adapter.analyze(msg, ['其他'], Event())


def test_gmail_revoked_token_not_resaved():
    import httplib2
    from googleapiclient.errors import HttpError
    service = GmailService.__new__(GmailService)
    service.auth = Mock()
    service.credentials = Mock(valid=True)
    request = Mock()
    request.execute.side_effect = HttpError(httplib2.Response({'status': '401'}), b'{"error":"invalid"}')
    with pytest.raises(ReauthRequired):
        service.execute(request)
    service.auth.logout.assert_called_once()
    service.auth.save.assert_not_called()
    service.auth.reset_mock()
    request.execute.side_effect = RefreshError('invalid_grant')
    with pytest.raises(ReauthRequired):
        service.execute(request)
    service.auth.logout.assert_called_once()
    service.auth.save.assert_not_called()


def test_gmail_temporary_refresh_error_keeps_token():
    service = GmailService.__new__(GmailService)
    service.auth = Mock()
    service.credentials = Mock(valid=True)
    request = Mock()
    request.execute.side_effect = RefreshError('temporary', retryable=True)
    with pytest.raises(RuntimeError, match='稍後重試'):
        service.execute(request)
    service.auth.logout.assert_not_called()


def test_oauth_official_flow_options_and_scope_denial(monkeypatch, tmp_path):
    path = tmp_path / 'desktop.json'
    path.write_text(json.dumps({'installed': {
        'client_id': 'synthetic.apps.googleusercontent.com',
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token',
    }}))
    secrets = Mock()
    factory = Mock()
    flow = factory.return_value
    flow.credentials.granted_scopes = SCOPES
    flow.credentials.to_json.return_value = '{}'
    monkeypatch.setattr('gmail_summary.auth.InstalledAppFlow.from_client_config', factory)
    auth = AuthService(secrets, path)
    assert auth.login(Event()) is flow.credentials
    assert factory.call_args.kwargs['autogenerate_code_verifier'] is True
    assert factory.call_args.args[1] == SCOPES
    args = flow.run_local_server.call_args.kwargs
    assert args['host'] == '127.0.0.1' and args['port'] == 0 and args['open_browser'] is True
    assert args['access_type'] == 'offline' and args['prompt'] == 'select_account consent'
    secrets.set.assert_called_once()
    secrets.reset_mock()
    flow.credentials.granted_scopes = ['openid']
    with pytest.raises(ReauthRequired, match='Gmail'):
        auth.login(Event())
    secrets.set.assert_not_called()
