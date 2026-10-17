"""Gmail IMAP app-password adapter. TLS + EXAMINE + BODY.PEEK only."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
import imaplib
import hmac
import json
import quopri
import re
import ssl
from threading import Event

from .auth import ReauthRequired
from .gmail import parse_message
from .models import Message, Search, TAIPEI
from .security import SecretStore

IMAP_SLOT = "gmail-imap-current"
MAX_PART_BYTES = 2 * 1024 * 1024


def validate_login(account: str, password: str) -> tuple[str, str]:
    account = account.strip().casefold()
    password = "".join(password.split())
    if not re.fullmatch(r'[^\s<>"\\@]+@[^\s<>"\\@]+\.[^\s<>"\\@]+', account):
        raise ValueError("請輸入完整的 Gmail／Google Workspace Email。")
    if not re.fullmatch(r"[A-Za-z0-9]{16}", password):
        raise ValueError("請輸入 Google 產生的 16 碼應用程式密碼（可含分隔空格），不要輸入平常的 Google 密碼。")
    return account, password


def tokens(raw: bytes) -> list:
    """Parse IMAP quoted strings/lists without eval or mailbox-controlled code."""
    index = 0

    def one():
        nonlocal index
        while index < len(raw) and raw[index:index + 1].isspace():
            index += 1
        if index >= len(raw):
            raise ValueError("IMAP 資料不完整。")
        if raw[index:index + 1] == b'(':
            index += 1
            result = []
            while True:
                while index < len(raw) and raw[index:index + 1].isspace():
                    index += 1
                if index >= len(raw):
                    raise ValueError("IMAP 清單不完整。")
                if raw[index:index + 1] == b')':
                    index += 1
                    return result
                result.append(one())
        if raw[index:index + 1] == b'"':
            index += 1
            result = bytearray()
            while index < len(raw):
                ch = raw[index:index + 1]
                index += 1
                if ch == b'"':
                    return bytes(result)
                if ch == b'\\':
                    if index >= len(raw):
                        break
                    ch = raw[index:index + 1]
                    index += 1
                result.extend(ch)
            raise ValueError("IMAP 字串不完整。")
        start = index
        while index < len(raw) and raw[index:index + 1] not in b' ()\r\n':
            index += 1
        if index == start:
            raise ValueError("IMAP 格式不正確。")
        atom = raw[start:index]
        return None if atom.upper() == b'NIL' else atom

    result = []
    while index < len(raw):
        if not raw[index:].strip():
            break
        result.append(one())
    return result


def text(value) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else ""


def params(value) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    return {text(value[i]).casefold(): text(value[i + 1]) for i in range(0, len(value) - 1, 2)}


@dataclass
class BodyPart:
    mime: str
    section: str
    encoding: str = "7BIT"
    charset: str = "utf-8"
    size: int = 0
    attached: bool = False
    children: list[BodyPart] = field(default_factory=list)


def structure(value: list, section: str = "") -> BodyPart:
    if not value or not isinstance(value, list):
        raise ValueError("IMAP MIME 結構不正確。")
    if isinstance(value[0], list):
        end = next((i for i, item in enumerate(value) if not isinstance(item, list)), len(value))
        mime = "multipart/" + text(value[end]).casefold() if end < len(value) else "multipart/mixed"
        disposition = value[end + 2] if end + 2 < len(value) else None
        attached = bool(isinstance(disposition, list) and disposition and (
            text(disposition[0]).casefold() == "attachment"
            or any(k.startswith("filename") for k in params(disposition[1] if len(disposition) > 1 else None))))
        return BodyPart(mime, section, attached=attached, children=[
            structure(v, f"{section}.{i + 1}" if section else str(i + 1)) for i, v in enumerate(value[:end])])
    if len(value) < 7:
        raise ValueError("IMAP MIME 結構不完整。")
    mime = text(value[0]).casefold() + "/" + text(value[1]).casefold()
    properties = params(value[2])
    position = 9 if mime.startswith("text/") else 11 if mime == "message/rfc822" else 8
    disposition = value[position] if len(value) > position else None
    attached = any(k.startswith(("name", "filename")) for k in properties)
    if isinstance(disposition, list) and disposition:
        attached |= text(disposition[0]).casefold() == "attachment" or any(k.startswith("filename") for k in params(disposition[1] if len(disposition) > 1 else None))
    return BodyPart(mime, section or "1", text(value[5]).upper(), properties.get("charset", "utf-8"),
                    int(value[6] or 0), attached)


def preferred_parts(part: BodyPart) -> tuple[list[BodyPart], list[str]]:
    if part.attached:
        return [], []
    if part.mime == "message/rfc822":
        return [], ["未展開附加信件"]
    if part.mime == "multipart/alternative":
        candidates = sorted(part.children, key=lambda p: p.mime != "text/plain")
        for child in candidates:
            parts, notes = preferred_parts(child)
            if parts:
                return parts, notes
        return [], []
    if part.children:
        results = [preferred_parts(child) for child in part.children]
        return [p for ps, _ in results for p in ps], [n for _, ns in results for n in ns]
    return ([part], []) if part.mime in {"text/plain", "text/html"} and part.size > 0 else ([], [])


def metadata_bytes(data) -> bytes:
    result = []
    for item in data or []:
        if isinstance(item, bytes):
            result.append(item)
        elif isinstance(item, tuple) and len(item) == 2:
            # BODYSTRUCTURE parameters can themselves use IMAP literals.
            prefix, literal = item
            quoted = b'"' + literal.replace(b'\\', b'\\\\').replace(b'"', b'\\"') + b'"'
            result.append(re.sub(rb'\{\d+\}$', lambda _: quoted, prefix))
    return b' '.join(result)


class ImapAuth:
    def __init__(self, secrets: SecretStore):
        self.secrets = secrets

    def login(self, account: str, password: str, cancel: Event):
        account, password = validate_login(account, password)
        self.secrets.get(IMAP_SLOT)  # Fail before connecting if no secure OS keyring exists.
        mailbox = GmailImapService(account, password, self.secrets)
        if cancel.is_set():
            return None
        mailbox.profile()
        if cancel.is_set():
            return None
        self.secrets.set(IMAP_SLOT, json.dumps({"account": account, "password": password}))
        return mailbox

    def restore(self):
        raw = self.secrets.get(IMAP_SLOT)
        if not raw:
            return None
        try:
            data = json.loads(raw)
            account, password = validate_login(data["account"], data["password"])
        except (ValueError, KeyError, TypeError, AttributeError):
            self.logout()
            raise ReauthRequired("已儲存的應用程式密碼資料失效，請重新連線 Gmail。") from None
        mailbox = GmailImapService(account, password, self.secrets)
        mailbox.profile()
        return mailbox

    def logout(self):
        self.secrets.delete(IMAP_SLOT)


class GmailImapService:
    def __init__(self, account: str, password: str, secrets: SecretStore):
        self.account, self._password = validate_login(account, password)
        self.secrets = secrets
        self.identities: dict[tuple[str, bytes], str] = {}

    @contextmanager
    def connection(self):
        client = None
        try:
            client = imaplib.IMAP4_SSL("imap.gmail.com", 993, ssl_context=ssl.create_default_context(), timeout=30)
            client.debug = 0
            try:
                client.login(self.account, self._password)
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:
                # Never erase another account's working credential after a failed new login.
                saved = self.secrets.get(IMAP_SLOT)
                try:
                    saved_data = json.loads(saved or '{}')
                    same_credential = (saved_data.get("account", "").casefold() == self.account.casefold()
                                       and hmac.compare_digest(saved_data.get("password", ""), self._password))
                except (ValueError, TypeError, AttributeError):
                    same_credential = False
                if same_credential:
                    self.secrets.delete(IMAP_SLOT)
                raise ReauthRequired("Gmail 拒絕應用程式密碼。請確認已啟用兩步驟驗證並重新產生 16 碼密碼；學校／公司可能禁止此方式。") from None
            yield client
        except ReauthRequired:
            raise
        except (OSError, imaplib.IMAP4.abort):
            raise RuntimeError("無法連線 Gmail IMAP（TLS 993）；已保留憑證，請檢查網路後重試。") from None
        except imaplib.IMAP4.error:
            raise RuntimeError("Gmail IMAP 請求未完成；請重試，或確認管理員允許 IMAP。") from None
        finally:
            if client is not None:
                try:
                    client.logout()  # LOGOUT, never CLOSE/EXPUNGE/STORE.
                except (OSError, imaplib.IMAP4.error):
                    pass

    def profile(self):
        with self.connection() as client:
            self.select_mailbox(client)
        return self.account

    @staticmethod
    def select_mailbox(client) -> tuple[str, str]:
        mailbox = "INBOX"
        limitation = "目前僅搜尋收件匣；Gmail 未提供所有郵件資料夾"
        status, rows = client.list()
        if status == "OK":
            for row in rows or []:
                if not isinstance(row, bytes):
                    continue
                parsed = tokens(row)
                if len(parsed) >= 3 and isinstance(parsed[0], list) and b'\\All' in parsed[0]:
                    # Keep modified UTF-7 mailbox bytes; quote safely for the IMAP command.
                    mailbox = b'"' + parsed[2].replace(b'\\', b'\\\\').replace(b'"', b'\\"') + b'"'
                    limitation = ""
                    break
        status, _ = client.select(mailbox, readonly=True)
        if status != "OK":
            raise RuntimeError("無法以唯讀模式開啟 Gmail 信箱。請確認帳號允許 IMAP。")
        _, validity = client.response("UIDVALIDITY")
        uidvalidity = next((x.decode('ascii') for x in validity or [] if x), "")
        if not uidvalidity.isdigit():
            raise RuntimeError("Gmail 未提供有效的信箱識別，請重新連線。")
        return uidvalidity, limitation

    @staticmethod
    def raw_search(client, query: str) -> list[bytes]:
        if '\x00' in query:
            raise ValueError("搜尋條件不可包含空字元。")
        client.literal = query.encode("utf-8")
        status, rows = client.uid("SEARCH", None, "X-GM-RAW")
        if status != "OK":
            raise RuntimeError("Gmail 搜尋失敗，請檢查進階查詢語法。")
        ids = list(dict.fromkeys(b' '.join(r for r in rows or [] if isinstance(r, bytes)).split()))
        if any(not uid.isdigit() or not 1 <= int(uid) <= 4294967295 for uid in ids):
            raise ValueError("Gmail 傳回不正確的信件識別，請重新搜尋。")
        return ids

    @staticmethod
    def literal_fetch(client, uid: bytes, item: str) -> bytes:
        status, rows = client.uid("FETCH", uid, f"({item})")
        if status != "OK":
            raise RuntimeError("信件正文讀取失敗，可稍後重試。")
        literal = next((r[1] for r in rows or [] if isinstance(r, tuple) and isinstance(r[1], bytes)), None)
        if literal is None:
            raise ValueError("Gmail 未傳回可用的正文資料。")
        return literal

    def read(self, client, uid: bytes, validity: str, limitation: str, cancel: Event) -> Message | None:
        status, rows = client.uid("FETCH", uid, "(UID X-GM-MSGID X-GM-THRID FLAGS INTERNALDATE BODYSTRUCTURE)")
        if status != "OK":
            raise RuntimeError("無法取得信件資料。")
        raw = metadata_bytes(rows)
        parsed = tokens(raw)
        values = next((v for v in parsed if isinstance(v, list)), [])
        attrs = {values[i]: values[i + 1] for i in range(0, len(values) - 1, 2) if isinstance(values[i], bytes)}
        if b'BODYSTRUCTURE' not in attrs:
            raise ValueError("信件缺少 MIME 結構；不改用整封下載，以免下載附件。")
        message_id = attrs.get(b'X-GM-MSGID')
        thread_id = attrs.get(b'X-GM-THRID')
        identity = format(int(message_id), 'x') if message_id else f"imap:{validity}:{uid.decode('ascii')}"
        self.identities[(validity, uid)] = identity
        chosen, notes = preferred_parts(structure(attrs[b'BODYSTRUCTURE']))
        if limitation:
            notes.append(limitation)
        if cancel.is_set():
            return None
        header = self.literal_fetch(client, uid, "BODY.PEEK[HEADER]")
        from email import policy
        from email.parser import BytesHeaderParser
        headers = BytesHeaderParser(policy=policy.default).parsebytes(header)
        payload_parts = []
        for part in chosen:
            if cancel.is_set():
                return None
            body = self.literal_fetch(client, uid, f"BODY.PEEK[{part.section}]<0.{MAX_PART_BYTES}>")
            if part.size > MAX_PART_BYTES:
                notes.append("正文過長，僅讀取前 2 MiB（IMAP）")
            if part.encoding == "BASE64":
                try:
                    body = base64.b64decode(body + b'=' * (-len(body) % 4), validate=False)
                except ValueError:
                    notes.append("部分正文 Base64 編碼損壞")
                    continue
            elif part.encoding == "QUOTED-PRINTABLE":
                body = quopri.decodestring(body)
            elif part.encoding not in {"7BIT", "8BIT", "BINARY"}:
                notes.append("部分正文使用不支援的傳輸編碼")
                continue
            payload_parts.append({"mimeType": part.mime, "headers": [
                {"name": "Content-Type", "value": f'{part.mime}; charset="{part.charset}"'}],
                "body": {"data": base64.urlsafe_b64encode(body).decode('ascii')}})
        internal = attrs.get(b'INTERNALDATE')
        if internal:
            stamp = parsedate_to_datetime(internal.decode('ascii').replace('-', ' ', 2))
        else:
            notes.append("缺少 Gmail 收信日期，使用寄件日期")
            try:
                stamp = parsedate_to_datetime(str(headers.get('Date', '')))
            except (ValueError, TypeError):
                stamp = datetime.fromtimestamp(0, TAIPEI)
                notes.append("無法取得信件日期")
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=TAIPEI)
            notes.append("日期缺少時區，暫以台北時間解讀")
        # Google IMAP decimal IDs map to Gmail API hexadecimal IDs, preserving deduplication.
        thread = format(int(thread_id), 'x') if thread_id else ""
        msg = parse_message({"id": identity, "threadId": thread,
                             "internalDate": str(int(stamp.timestamp() * 1000)),
                             "labelIds": [] if b'\\Seen' in attrs.get(b'FLAGS', []) else ['UNREAD'],
                             "payload": {"mimeType": "multipart/mixed", "parts": payload_parts,
                                         "headers": [{"name": k, "value": str(v)} for k, v in headers.items()]}}, self.account)
        msg.limitation = "；".join(dict.fromkeys(n for n in [msg.limitation, *notes] if n))
        return msg

    def search(self, criteria: Search, cancel: Event, emit):
        query = criteria.query()
        if cancel.is_set():
            return
        with self.connection() as client:
            validity, limitation = self.select_mailbox(client)
            ids = list(reversed(self.raw_search(client, query)))[:criteria.limit]
            for start in range(0, len(ids), 50):
                for index, uid in enumerate(ids[start:start + 50], start + 1):
                    if cancel.is_set():
                        return
                    try:
                        msg = self.read(client, uid, validity, limitation, cancel)
                    except (ReauthRequired, imaplib.IMAP4.abort):
                        raise
                    except Exception:
                        identity = self.identities.get((validity, uid), f"imap:{validity}:{uid.decode('ascii')}")
                        msg = Message(identity, "", self.account, "", "", "", "（讀取失敗）",
                                      status="讀取失敗", error="IMAP 讀取失敗，可重試；未下載附件或輸出原始錯誤。")
                    if msg is not None and not cancel.is_set():
                        fallback = f"imap:{validity}:{uid.decode('ascii')}"
                        if msg.id != fallback and msg.status != "讀取失敗":
                            emit("read_replaced", ((self.account.casefold(), fallback), msg))
                        emit("message", msg)
                        emit("progress", (index, len(ids)))

    def retry_read(self, message: Message, cancel: Event, emit):
        if cancel.is_set():
            return
        if message.account.casefold() != self.account.casefold():
            raise ValueError("信件帳號與目前 Gmail 連線不符。")
        fallback = re.fullmatch(r"imap:([0-9]{1,10}):([1-9][0-9]{0,9})", message.id)
        if fallback:
            if int(fallback[2]) > 4294967295:
                raise ValueError("CSV 中的 IMAP 信件識別不正確，請重新搜尋。")
        elif not re.fullmatch(r"[0-9a-fA-F]{1,16}", message.id):
            raise ValueError("CSV 中的 Gmail 信件識別不正確，請重新搜尋。")
        with self.connection() as client:
            validity, limitation = self.select_mailbox(client)
            if fallback:
                original_validity, uid = fallback.groups()
                if original_validity != validity:
                    raise ValueError("IMAP 信箱識別已變更，請重新搜尋這封信。")
                uid = uid.encode('ascii')
            else:
                status, rows = client.uid("SEARCH", None, "X-GM-MSGID", str(int(message.id, 16)))
                if status != "OK" or not rows or not rows[0]:
                    raise ValueError("信件已不在目前信箱，請重新搜尋。")
                uid = rows[0].split()[0]
            msg = self.read(client, uid, validity, limitation, cancel)
            if msg is not None and not cancel.is_set():
                emit("read_replaced", (message.key, msg))
