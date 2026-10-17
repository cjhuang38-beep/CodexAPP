import base64
from email.header import Header
import imaplib
import json
import ssl
from threading import Event
from unittest.mock import Mock

import pytest

from gmail_summary.auth import ReauthRequired
from gmail_summary.imap_mail import (
    GmailImapService, IMAP_SLOT, ImapAuth, MAX_PART_BYTES,
    preferred_parts, structure, tokens, validate_login,
)
from gmail_summary.models import Search

ACCOUNT = 'reader@example.invalid'
# Deliberately synthetic, not a Google credential.
PASSWORD = 'abcdefghijklmnop'
TEXT = '請於2026年10月30日前回覆。'
BODY = base64.b64encode(TEXT.encode())
STRUCTURE = (b'((("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "BASE64" 100 2 NIL NIL NIL) '
             b'("TEXT" "HTML" ("CHARSET" "UTF-8") NIL NIL "7BIT" 100 2 NIL NIL NIL) "ALTERNATIVE" NIL NIL NIL) '
             b'("APPLICATION" "PDF" ("NAME" "secret.pdf") NIL NIL "BASE64" 100000 NIL '
             b'("ATTACHMENT" ("FILENAME" "secret.pdf")) NIL) "MIXED" NIL NIL NIL)')


class FakeImap:
    def __init__(self):
        self.commands = []
        self.literal = None
        self.query = None
        self.search_ids = b'1 2 2 3'
        self.fail_uid = None
        self.login_error = None
        self.all_mail = True
        self.structure = STRUCTURE
        self.debug = 0

    def login(self, account, password):
        self.commands.append(('LOGIN', account))
        assert account == ACCOUNT and password == PASSWORD
        if self.login_error:
            raise self.login_error
        return 'OK', []

    def list(self):
        self.commands.append(('LIST',))
        return 'OK', [b'(\\HasNoChildren \\All) "/" "[Gmail]/All Mail"'] if self.all_mail else []

    def select(self, mailbox, readonly=False):
        self.commands.append(('SELECT', mailbox, readonly))
        assert readonly is True
        return 'OK', [b'3']

    def response(self, name):
        assert name == 'UIDVALIDITY'
        return name, [b'123']

    def logout(self):
        self.commands.append(('LOGOUT',))
        return 'BYE', []

    def uid(self, operation, *args):
        self.commands.append((operation, *args))
        if operation == 'SEARCH':
            if args[1] == 'X-GM-RAW':
                self.query = self.literal
                self.literal = None
                return 'OK', [self.search_ids]
            assert args[1] == 'X-GM-MSGID'
            return 'OK', [str(int(args[2]) - 100).encode()]
        assert operation == 'FETCH'
        uid, fields = args
        if 'BODYSTRUCTURE' in fields:
            flags = b'\\Seen' if uid == b'2' else b''
            return 'OK', [b'1 (UID ' + uid + b' X-GM-MSGID ' + str(100 + int(uid)).encode()
                          + b' X-GM-THRID 1000 FLAGS (' + flags + b') INTERNALDATE "18-Oct-2026 00:00:00 +0800" BODYSTRUCTURE '
                          + self.structure + b')']
        if self.fail_uid == uid:
            raise imaplib.IMAP4.error('synthetic failure; must not be shown to users')
        if fields == '(BODY.PEEK[HEADER])':
            subject = Header('合成測試主旨', 'utf-8').encode().encode()
            raw = b'From: Test <teacher@example.invalid>\r\nSubject: ' + subject + b'\r\nDate: Sat, 17 Oct 2026 12:00:00 +0800\r\n\r\n'
        elif fields == f'(BODY.PEEK[1.1]<0.{MAX_PART_BYTES}>)':
            raw = BODY
        else:
            raise AssertionError('Must not fetch attachment, HTML alternative, entire message or alter flags: ' + fields)
        return 'OK', [(b'1 (BODY[] {' + str(len(raw)).encode() + b'}', raw), b')']


@pytest.fixture
def connection(monkeypatch):
    fake = FakeImap()
    calls = []

    def factory(host, port, *, ssl_context, timeout):
        calls.append((host, port, ssl_context, timeout))
        return fake
    monkeypatch.setattr('gmail_summary.imap_mail.imaplib.IMAP4_SSL', factory)
    return fake, calls


def service(secrets=None):
    vault = secrets or Mock()
    vault.get.return_value = None
    return GmailImapService(ACCOUNT, PASSWORD, vault)


def test_app_password_normalization_and_validation():
    assert validate_login(' READER@example.invalid ', 'abcd efgh ijkl mnop') == (ACCOUNT, PASSWORD)
    for account, password in [('missing-email', PASSWORD), (ACCOUNT, 'normal-password'), ('a\r\nb@example.invalid', PASSWORD)]:
        with pytest.raises(ValueError):
            validate_login(account, password)


def test_bodystructure_chooses_plain_and_never_attachments():
    part = structure(tokens(STRUCTURE)[0])
    parts, notes = preferred_parts(part)
    assert [(p.mime, p.section) for p in parts] == [('text/plain', '1.1')]
    assert not notes
    malformed = b'("TEXT" "PLAIN" ("CHARSET" "UTF-8" "NAME*" "file.txt") NIL NIL "7BIT" 99 2 NIL NIL)'
    assert not preferred_parts(structure(tokens(malformed)[0]))[0]


def test_imap_tls_readonly_search_query_ids_body_and_flags(connection):
    fake, tls = connection
    mailbox = service()
    events = []
    mailbox.search(Search(limit=2, subject='中文主旨', advanced='has:attachment'), Event(), lambda k, v: events.append((k, v)))
    messages = [m for k, m in events if k == 'message']
    assert [m.id for m in messages] == [format(103, 'x'), format(102, 'x')]
    assert all(m.body == TEXT and m.subject == '合成測試主旨' and m.thread_id == '3e8' for m in messages)
    assert messages[0].unread is True and messages[1].unread is False
    assert messages[0].date == '2026-10-18T00:00:00+08:00'
    assert 'subject:"中文主旨"' in fake.query.decode() and '(has:attachment)' in fake.query.decode()
    host, port, context, timeout = tls[0]
    assert host == 'imap.gmail.com' and port == 993 and timeout == 30
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert fake.commands[-1] == ('LOGOUT',)
    assert not any(c[0] in {'STORE', 'CLOSE', 'EXPUNGE', 'APPEND'} for c in fake.commands)


def test_imap_cancel_closes_connection_and_preserves_completed(connection):
    fake, _ = connection
    cancel = Event()
    events = []

    def emit(kind, value):
        events.append((kind, value))
        if kind == 'message':
            cancel.set()
    service().search(Search(), cancel, emit)
    assert len([v for k, v in events if k == 'message']) == 1
    assert fake.commands[-1] == ('LOGOUT',)


def test_partial_read_failure_uses_gmail_id_and_can_retry(connection):
    fake, _ = connection
    fake.fail_uid = b'3'
    mailbox = service()
    events = []
    mailbox.search(Search(limit=2), Event(), lambda k, v: events.append((k, v)))
    rows = [v for k, v in events if k == 'message']
    assert rows[0].status == '讀取失敗' and rows[0].id == '67'
    assert rows[1].body == TEXT
    assert 'synthetic failure' not in rows[0].error
    fake.fail_uid = None
    retry = []
    mailbox.retry_read(rows[0], Event(), lambda k, v: retry.append((k, v)))
    assert retry[0][0] == 'read_replaced'
    old_key, new = retry[0][1]
    assert old_key == rows[0].key and new.id == '67' and new.body == TEXT


def test_inbox_fallback_is_labeled(connection):
    fake, _ = connection
    fake.all_mail = False
    events = []
    service().search(Search(limit=1), Event(), lambda k, v: events.append((k, v)))
    message = next(v for k, v in events if k == 'message')
    assert '僅搜尋收件匣' in message.limitation


def test_imap_auth_persists_only_keyring_after_success(connection):
    vault = Mock()
    vault.get.return_value = None
    auth = ImapAuth(vault)
    mailbox = auth.login(ACCOUNT, PASSWORD, Event())
    assert mailbox.account == ACCOUNT
    slot, value = vault.set.call_args.args
    assert slot == IMAP_SLOT and json.loads(value) == {'account': ACCOUNT, 'password': PASSWORD}
    vault.get.return_value = value
    assert auth.restore().account == ACCOUNT
    auth.logout()
    vault.delete.assert_called_with(IMAP_SLOT)


def test_imap_refuses_insecure_keyring_before_network(connection):
    fake, _ = connection
    vault = Mock()
    vault.get.side_effect = RuntimeError('keyring unavailable')
    with pytest.raises(RuntimeError):
        ImapAuth(vault).login(ACCOUNT, PASSWORD, Event())
    assert not fake.commands


def test_revoked_password_removed_without_showing_server_secret(connection):
    fake, _ = connection
    fake.login_error = imaplib.IMAP4.error('DO NOT DISPLAY ' + PASSWORD)
    vault = Mock()
    vault.get.return_value = json.dumps({'account': ACCOUNT, 'password': PASSWORD})
    with pytest.raises(ReauthRequired) as caught:
        GmailImapService(ACCOUNT, PASSWORD, vault).profile()
    assert PASSWORD not in str(caught.value)
    vault.delete.assert_called_once_with(IMAP_SLOT)
    vault.delete.reset_mock()
    vault.get.return_value = json.dumps({'account': 'other@example.invalid', 'password': PASSWORD})
    with pytest.raises(ReauthRequired):
        GmailImapService(ACCOUNT, PASSWORD, vault).profile()
    vault.delete.assert_not_called()


def test_network_error_retains_saved_password(monkeypatch):
    vault = Mock()
    monkeypatch.setattr('gmail_summary.imap_mail.imaplib.IMAP4_SSL', Mock(side_effect=ssl.SSLError('offline')))
    with pytest.raises(RuntimeError, match='保留憑證'):
        service(vault).profile()
    vault.delete.assert_not_called()


def test_corrupt_vault_entry_requests_reconnection():
    vault = Mock()
    vault.get.return_value = '{bad-json'
    with pytest.raises(ReauthRequired):
        ImapAuth(vault).restore()
    vault.delete.assert_called_once_with(IMAP_SLOT)


def test_cancelled_imap_login_never_saves_secret(connection):
    vault = Mock()
    vault.get.return_value = None
    cancel = Event()
    cancel.set()
    assert ImapAuth(vault).login(ACCOUNT, PASSWORD, cancel) is None
    vault.set.assert_not_called()


def test_imap_more_than_one_batch_and_message_limit(connection):
    fake, _ = connection
    fake.search_ids = b' '.join(str(i).encode() for i in range(1, 61)) + b' 60'
    events = []
    service().search(Search(limit=55), Event(), lambda k, v: events.append((k, v)))
    messages = [v for k, v in events if k == 'message']
    assert len(messages) == 55 and len({m.id for m in messages}) == 55
    assert messages[0].id == format(160, 'x') and messages[-1].id == format(106, 'x')
    assert [v for k, v in events if k == 'progress'][-1] == (55, 55)


def test_bad_replacement_password_does_not_erase_working_same_account_credential(connection):
    fake, _ = connection
    fake.login_error = imaplib.IMAP4.error('synthetic bad replacement password')
    vault = Mock()
    vault.get.return_value = json.dumps({'account': ACCOUNT, 'password': 'ponmlkjihgfedcba'})
    with pytest.raises(ReauthRequired):
        GmailImapService(ACCOUNT, PASSWORD, vault).profile()
    vault.delete.assert_not_called()


@pytest.mark.parametrize('identifier', [
    'imap:123:1\r\nA001 STORE 1 +FLAGS (\\Deleted)',
    'imap:123:4294967296',
    'invalid-id-not-hexadecimal',
])
def test_untrusted_csv_identifier_cannot_inject_imap_commands(connection, identifier):
    from gmail_summary.models import Message
    fake, _ = connection
    message = Message(identifier, '', ACCOUNT, '', '', '', '')
    with pytest.raises(ValueError):
        service().retry_read(message, Event(), lambda *args: None)
    assert not fake.commands, 'Reject untrusted identifiers before opening any connection'
