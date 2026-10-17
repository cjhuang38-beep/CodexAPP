from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from .models import Message

COLUMNS = {
    "日期": "date", "寄件者姓名": "sender_name", "寄件者Email": "sender_email", "主旨": "subject",
    "分類": "category", "摘要": "summary", "待辦事項": "todos", "期限": "deadline",
    "處理狀態": "status", "內容限制": "limitation", "錯誤原因": "error",
    "信件ID": "id", "討論串ID": "thread_id", "帳號識別": "account",
}
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_ROWS = 20000
# Escape every cell starting with these characters, even after whitespace.
FORMULA_STARTS = "=+-@\t\r\n"


def safe_cell(value: str) -> str:
    stripped = value.lstrip()
    formula = bool(value) and (value[0] in FORMULA_STARTS or bool(stripped) and stripped[0] in "=+-@")
    if value.startswith("'") or formula:
        return "'" + value
    return value


def restore_cell(value: str) -> str:
    # Inverse of our own exporter, without stripping legitimate leading apostrophes.
    if value.startswith("'") and safe_cell(value[1:]) == value:
        return value[1:]
    return value


def export_csv(path: Path, messages: list[Message]) -> None:
    # Atomic replace prevents truncated output from being treated as a successful save.
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="",
                                         dir=path.parent, prefix=".gmail-summary-", suffix=".tmp", delete=False) as stream:
            temp = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=list(COLUMNS))
            writer.writeheader()
            for msg in messages:
                writer.writerow({col: safe_cell(str(getattr(msg, attr))) for col, attr in COLUMNS.items()})
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def import_csv(path: Path) -> list[Message]:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("CSV 超過 50 MB，請分批匯入。")
    result = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(COLUMNS):
            raise ValueError("CSV 欄位不符，請使用本程式匯出的 14 欄 CSV。")
        for row in reader:
            if len(result) >= MAX_ROWS:
                raise ValueError("CSV 超過 20000 筆，請分批匯入。")
            if None in row or any(row[c] is None for c in COLUMNS):
                raise ValueError(f"CSV 第 {reader.line_num} 行欄位數不正確。")
            data = {attr: restore_cell(row[col]) for col, attr in COLUMNS.items()}
            if not data["id"] or not data["account"]:
                raise ValueError(f"CSV 第 {reader.line_num} 行缺少信件 ID 或帳號。")
            if "CSV 匯入；無原始正文，無法重新分析" not in data["limitation"]:
                data["limitation"] = "；".join(filter(None, [data["limitation"], "CSV 匯入；無原始正文，無法重新分析"]))
            result.append(Message(**data))
    return result
