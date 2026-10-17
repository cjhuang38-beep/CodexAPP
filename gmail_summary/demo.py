from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta
from threading import Event

from .ai import Analysis, AnalysisCancelled
from .models import Message, Search, TAIPEI

DEMO_ACCOUNT = "demo@example.invalid"
# All identities, dates and messages below are synthetic.
SAMPLES = [
    ("教學組", "course@example.invalid", "邀請擔任資料分析工作坊講師", "課程邀約",
     "您好，合成大學教學組邀請您擔任資料分析工作坊講師。課程規劃為兩場，請先回覆是否有意願及可授課時段，後續再討論課綱與鐘點費。本信未訂回覆期限，亦未確認任何授課承諾。", "回覆授課意願及可授課時段", ""),
    ("研究團隊", "research@example.invalid", "研究計畫文獻整理", "學術研究",
     "您好，研究團隊已整理跨領域計畫的文獻清單，請協助檢查研究問題是否清楚，並提供可補充的相關論文。下一次會議時間尚未確定，請先回覆意見，不需開啟任何附件。", "檢查研究問題並提供相關論文", ""),
    ("行政中心", "office@example.invalid", "【合成通知】辦公室資料確認", "行政通知",
     "行政中心正在核對聯絡資料。請於2026年10月30日前，確認姓名、分機與辦公室位置。如資料正確，請回覆確認；有異動則提供更新資訊。此為合成信件，並非真實行政要求。", "確認或更新聯絡資料", "2026年10月30日"),
    ("帳務小組", "billing@example.invalid", "【合成帳單】付款資訊核對", "帳務",
     "本月合成服務費用為新臺幣 800 元，請核對帳單項目。此信沒有指定付款期限，也沒有提供付款連結。若發現項目錯誤，請回覆帳務小組協助確認。這不是實際請款。", "核對帳單項目", ""),
    ("活動小組", "news@example.invalid", "秋季活動電子報", "行銷",
     "本期合成電子報介紹秋季公開活動，包含數位工具講座與成果展示。活動時程尚未公布，若有興趣可留意後續通知。本信沒有要求回覆、付款或承諾參加。", "", ""),
    ("同事", "friend@example.invalid", "謝謝協助", "其他", "謝謝今天幫忙確認資料。", "", ""),
]


def demo_messages() -> list[Message]:
    today = datetime.now(TAIPEI).date()
    messages = []
    for i in range(18):
        name, sender, subject, _, body, _, _ = SAMPLES[i % len(SAMPLES)]
        stamp = datetime.combine(today - timedelta(days=i % 7), dt_time(10, 30), TAIPEI).isoformat()
        messages.append(Message(f"demo-{i:03d}", f"thread-{i:03d}", DEMO_ACCOUNT, stamp, name, sender,
                                "【Demo 合成】" + subject, body=body, synthetic=True, unread=i % 2 == 0,
                                limitation="合成信件；摘要為模擬，未呼叫 AI"))
    # Same subject is intentional: identity is account + Gmail ID, never subject.
    return messages


class DemoMailbox:
    account = DEMO_ACCOUNT

    def search(self, criteria: Search, cancel: Event, emit) -> None:
        criteria.query()
        if criteria.advanced:
            raise ValueError("Demo 支援日期、寄件者、主旨及未讀篩選；進階 Gmail 語法請在真實模式使用。")
        rows = [m for m in demo_messages() if criteria.start <= datetime.fromisoformat(m.date).date() <= criteria.end
                and criteria.sender.casefold() in (m.sender_name + m.sender_email).casefold()
                and criteria.subject.casefold() in m.subject.casefold() and (not criteria.unread or m.unread)]
        for n, msg in enumerate(rows[:criteria.limit], 1):
            if cancel.wait(.025):
                return
            emit("message", msg)
            emit("progress", (n, min(len(rows), criteria.limit)))


class DemoAnalyzer:
    def __init__(self, simulate_failure: bool = False):
        self.simulate_failure = simulate_failure
        self.failed_once: set[str] = set()

    def analyze(self, message: Message, categories: list[str], cancel: Event) -> Analysis:
        if cancel.wait(.09):
            raise AnalysisCancelled()
        if not message.body:
            raise ValueError("沒有原始正文，無法重新分析。")
        if self.simulate_failure and message.id.endswith("003") and message.id not in self.failed_once:
            self.failed_once.add(message.id)
            raise RuntimeError("Demo 模擬暫時失敗：可使用「重試失敗」。")
        match = next((s for s in SAMPLES if s[4] == message.body), None)
        if not match:
            raise ValueError("Demo 僅分析本程式的合成信件。")
        category = match[3] if match[3] in categories else categories[-1]
        return Analysis(category, "【模擬摘要】" + match[4], match[5], match[6], "合成信件；摘要為模擬，未呼叫 AI")
