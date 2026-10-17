from __future__ import annotations

from queue import Queue
from threading import Event, Thread

from .ai import AnalysisCancelled
from .auth import AuthCancelled, ReauthRequired


class JobRunner:
    """Worker code only emits data. The UI consumes it on the Tk main thread."""
    def __init__(self):
        self.queue: Queue = Queue()
        self.cancel_event = Event()
        self.thread: Thread | None = None
        self.generation = 0

    @property
    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, task) -> int:
        if self.busy:
            raise RuntimeError("已有工作執行中。")
        self.generation += 1
        generation = self.generation
        cancel = Event()
        self.cancel_event = cancel

        def emit(kind, data=None):
            self.queue.put((generation, kind, data))

        def run():
            try:
                task(cancel, emit)
            except (AnalysisCancelled, AuthCancelled):
                cancel.set()
            except ReauthRequired as exc:
                emit("reauth", str(exc))
            except Exception as exc:
                # Do not emit traceback, response bodies, credential JSON or arbitrary error messages.
                emit("error", user_error(exc))
            finally:
                emit("done", cancel.is_set())
        self.thread = Thread(target=run, name=f"gmail-job-{generation}", daemon=True)
        self.thread.start()
        return generation

    def cancel(self):
        self.cancel_event.set()


def user_error(exc: Exception) -> str:
    # Only our own ValueError/RuntimeError messages are displayed by most paths.
    # External exceptions (including keyring/OAuth errors) get a generic message.
    if type(exc) in {ValueError, RuntimeError}:
        return str(exc)
    return "操作未完成。請檢查網路、開發者 OAuth 設定與作業系統 keyring，再重試；未輸出敏感錯誤內容。"


def analyze_batch(messages, analyzer, categories, cancel, emit):
    for index, message in enumerate(messages, 1):
        if cancel.is_set():
            break
        try:
            result = analyzer.analyze(message, categories, cancel)
            if cancel.is_set():
                break
            emit("analysis", (message.key, result))
        except AnalysisCancelled:
            break
        except Exception as exc:
            if cancel.is_set():
                break
            emit("analysis_failed", (message.key, user_error(exc)))
        emit("progress", (index, len(messages)))
