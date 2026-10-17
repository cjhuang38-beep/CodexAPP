from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
DEFAULT_CATEGORIES = ["課程邀約", "學術研究", "行政通知", "帳務", "行銷", "其他"]


@dataclass
class Search:
    start: date = field(default_factory=lambda: datetime.now(TAIPEI).date() - timedelta(days=6))
    end: date = field(default_factory=lambda: datetime.now(TAIPEI).date())
    sender: str = ""
    subject: str = ""
    unread: bool = False
    advanced: str = ""
    limit: int = 50

    def query(self) -> str:
        if self.start > self.end:
            raise ValueError("開始日期不可晚於結束日期。")
        if not 1 <= self.limit <= 5000:
            raise ValueError("信件上限必須介於 1～5000。")
        start = int(datetime.combine(self.start, time.min, TAIPEI).timestamp())
        end = int(datetime.combine(self.end + timedelta(days=1), time.min, TAIPEI).timestamp())
        terms = [f"after:{start}", f"before:{end}"]
        for name, value in (("from", self.sender), ("subject", self.subject)):
            if value.strip():
                value = value.replace('\\', '\\\\').replace('"', '\\"').replace('\r', ' ').replace('\n', ' ')
                terms.append(f'{name}:"{value}"')
        if self.unread:
            terms.append("is:unread")
        if self.advanced.strip():
            # Group advanced OR expressions so they cannot widen the selected date range.
            terms.append(f"({self.advanced.strip()})")
        return " ".join(terms)


@dataclass
class Message:
    id: str
    thread_id: str
    account: str
    date: str
    sender_name: str
    sender_email: str
    subject: str
    body: str = ""
    category: str = "其他"
    summary: str = ""
    todos: str = ""
    deadline: str = ""
    status: str = "待處理"
    limitation: str = ""
    error: str = ""
    synthetic: bool = False
    unread: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return self.account.casefold(), self.id


class MailStore:
    """Session-only storage; keys never depend on subjects."""
    def __init__(self) -> None:
        self.messages: dict[tuple[str, str], Message] = {}
        self.account: str | None = None
        self.revision = 0
        self.exported_revision = 0

    @property
    def dirty(self) -> bool:
        return self.revision != self.exported_revision

    def switch(self, account: str | None) -> None:
        self.messages.clear()
        self.account = account
        self.revision = self.exported_revision = 0

    def add(self, message: Message) -> bool:
        if not self.account or message.account.casefold() != self.account.casefold():
            raise ValueError("信件帳號與目前工作階段不一致。")
        if message.key in self.messages:
            return False
        self.messages[message.key] = message
        self.revision += 1
        return True

    def edit(self, key: tuple[str, str], **changes: str) -> None:
        allowed = {"category", "summary", "todos", "deadline", "status", "limitation", "error"}
        if set(changes) - allowed:
            raise ValueError("不可修改信件識別或原始正文。")
        message = self.messages[key]
        for name, value in changes.items():
            setattr(message, name, value)
        self.revision += 1

    def mark_exported(self) -> None:
        self.exported_revision = self.revision
