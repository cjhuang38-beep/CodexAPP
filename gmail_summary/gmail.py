from __future__ import annotations

import base64
from datetime import datetime
from email.header import decode_header, make_header
from email.utils import parseaddr
from threading import Event
from typing import Callable

import httplib2
from bs4 import BeautifulSoup
from google.auth.exceptions import RefreshError
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .auth import AuthService, ReauthRequired
from .models import Message, Search, TAIPEI


def decode_text(data: str, charset: str) -> str:
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def extract_body(payload: dict) -> tuple[str, str]:
    """Pick text/plain before HTML in alternatives; keep other nested body sections."""
    def walk(part: dict) -> tuple[str, list[str]]:
        headers = {h["name"].lower(): h["value"] for h in part.get("headers", [])}
        disposition = headers.get("content-disposition", "").lower()
        if part.get("filename") or disposition.startswith("attachment"):
            return "", []
        mime = part.get("mimeType", "")
        if mime == "message/rfc822":
            return "", ["未展開附加信件"]
        parts = part.get("parts", [])
        if parts:
            if mime == "multipart/alternative":
                preferred = sorted(parts, key=lambda p: p.get("mimeType") != "text/plain")
                notes = []
                for child in preferred:
                    text, child_notes = walk(child)
                    notes.extend(child_notes)
                    if text.strip():
                        return text, notes
                return "", notes
            results = [walk(p) for p in parts]
            return "\n\n".join(t for t, _ in results if t.strip()), [n for _, ns in results for n in ns]
        body = part.get("body", {})
        if mime not in {"text/plain", "text/html"}:
            return "", []
        if body.get("attachmentId") and not body.get("data"):
            return "", ["部分正文由 Gmail 以附件形式提供；未自動下載"]
        import re
        match = re.search(r'charset\s*=\s*["\']?([^;"\'\s]+)', headers.get("content-type", ""), re.I)
        try:
            text = decode_text(body.get("data", ""), match.group(1) if match else "utf-8")
        except (ValueError, TypeError):
            return "", ["正文編碼無法解析"]
        notes = ["正文有無法解碼的字元"] if "\ufffd" in text else []
        if mime == "text/html":
            soup = BeautifulSoup(text, "html.parser")
            for tag in soup(["script", "style", "img", "noscript", "iframe", "object"]):
                tag.decompose()
            text = soup.get_text("\n", strip=True)
        return text.strip(), notes
    text, notes = walk(payload)
    return text, "；".join(dict.fromkeys(notes))


def decoded_header(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, LookupError, UnicodeError):
        return value


def parse_message(raw: dict, account: str) -> Message:
    headers = {h["name"].lower(): h["value"] for h in raw.get("payload", {}).get("headers", [])}
    name, address = parseaddr(decoded_header(headers.get("from", "")))
    body, limitation = extract_body(raw.get("payload", {}))
    if not body:
        body = raw.get("snippet", "")
        limitation = "；".join(filter(None, [limitation, "僅有片段，未取得完整正文" if body else "無可用正文"]))
    stamp = datetime.fromtimestamp(int(raw.get("internalDate", 0)) / 1000, TAIPEI).isoformat()
    return Message(id=raw["id"], thread_id=raw.get("threadId", ""), account=account, date=stamp,
                   sender_name=name, sender_email=address, subject=decoded_header(headers.get("subject", "（無主旨）")),
                   body=body, limitation=limitation, unread="UNREAD" in raw.get("labelIds", []))


class GmailService:
    def __init__(self, auth: AuthService, credentials):
        self.auth = auth
        self.credentials = credentials
        transport = AuthorizedHttp(credentials, http=httplib2.Http(timeout=30))
        self.api = build("gmail", "v1", http=transport, cache_discovery=False)
        self.account = ""

    def execute(self, request):
        self.auth.ensure(self.credentials)
        try:
            result = request.execute(num_retries=1)
            # AuthorizedHttp can refresh internally on a 401. Persist only successful requests;
            # saving in finally would accidentally restore a token removed for revocation.
            if self.credentials.valid:
                self.auth.save(self.credentials)
            return result
        except RefreshError as exc:
            if getattr(exc, "retryable", False):
                raise RuntimeError("Google 暫時無法更新憑證，請稍後重試。") from exc
            self.auth.logout()
            raise ReauthRequired("Google 授權已撤銷或過期，請重新登入。") from exc
        except HttpError as exc:
            if exc.resp.status == 401:
                self.auth.logout()
                raise ReauthRequired("Gmail 授權失效，請重新登入。") from exc
            if exc.resp.status == 403:
                raise RuntimeError("Gmail 拒絕存取。請檢查開發者已啟用 Gmail API、測試帳號及唯讀授權。") from exc
            raise RuntimeError("Gmail 請求失敗，請檢查網路後重試。") from exc

    def profile(self) -> str:
        self.account = self.execute(self.api.users().getProfile(userId="me"))["emailAddress"]
        return self.account

    def search(self, criteria: Search, cancel: Event, emit: Callable) -> None:
        query = criteria.query()
        seen = set()
        page = None
        count = 0
        while not cancel.is_set() and count < criteria.limit:
            response = self.execute(self.api.users().messages().list(
                userId="me", q=query, maxResults=min(100, criteria.limit - count), pageToken=page))
            for item in response.get("messages", []):
                if cancel.is_set() or count >= criteria.limit:
                    return
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                count += 1
                try:
                    raw = self.execute(self.api.users().messages().get(userId="me", id=item["id"], format="full"))
                    message = parse_message(raw, self.account)
                except ReauthRequired:
                    raise
                except Exception:
                    message = Message(item["id"], item.get("threadId", ""), self.account, "", "", "", "（讀取失敗）",
                                      status="讀取失敗", error="讀取正文失敗，可重試；未記錄服務端原始錯誤。")
                if not cancel.is_set():
                    emit("message", message)
                    emit("progress", (count, criteria.limit))
            next_page = response.get("nextPageToken")
            if not next_page or next_page == page:
                break
            page = next_page

    def retry_read(self, message: Message, cancel: Event, emit: Callable) -> None:
        if cancel.is_set():
            return
        raw = self.execute(self.api.users().messages().get(userId="me", id=message.id, format="full"))
        replacement = parse_message(raw, self.account)
        if not cancel.is_set():
            emit("read_retry", replacement)
