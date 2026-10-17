"""Run with DISPLAY on Linux; real Windows checks remain a separate release gate."""
import gc
import os
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt' and not os.environ.get('DISPLAY'), reason='需要桌面顯示器')


@pytest.fixture
def app(monkeypatch, tmp_path):
    # Tk objects must be finalized on the main thread, including previous test roots.
    gc.collect()
    import gmail_summary.settings as settings
    monkeypatch.setattr(settings, 'config_dir', lambda: tmp_path / 'config')
    from gmail_summary.ui import App
    instance = App(auto_restore=False)
    errors = []
    instance.report_callback_exception = lambda *args: errors.append(args)
    monkeypatch.setattr('gmail_summary.ui.messagebox.showinfo', lambda *a, **k: None)
    monkeypatch.setattr('gmail_summary.ui.messagebox.showerror', lambda *a, **k: errors.append(a))
    monkeypatch.setattr('gmail_summary.ui.messagebox.askokcancel', lambda *a, **k: True)
    monkeypatch.setattr('gmail_summary.ui.messagebox.askyesnocancel', lambda *a, **k: False)
    monkeypatch.setattr('gmail_summary.ui.messagebox.askyesno', lambda *a, **k: True)
    instance.update()
    gc.collect()
    yield instance
    instance.destroy()
    gc.collect()
    assert not errors, errors


def settle(app, timeout=10):
    end = time.monotonic() + timeout
    while app.pending and time.monotonic() < end:
        app.update()
        time.sleep(.01)
    app.update()
    assert not app.pending, '背景工作未完成'


def test_demo_ui_edit_filter_export_import_retry_theme(app, monkeypatch, tmp_path):
    main_thread = threading.get_ident()
    calls = []
    original = app.handle_event

    def handle(kind, data):
        calls.append(threading.get_ident())
        original(kind, data)
    monkeypatch.setattr(app, 'handle_event', handle)
    app.enter_demo()
    settle(app)
    assert len(app.store.messages) == 18
    assert len(app.search_tree.get_children()) == 18
    app.search()
    settle(app)
    assert len(app.store.messages) == 18, '重複搜尋不能新增重複 ID'
    app.show_page('分類摘要')
    app.check_all('summary', True)
    app.simulate_var.set(True)
    app.analyze()
    settle(app)
    assert sum(m.status == '分析失敗' for m in app.store.messages.values()) == 1
    app.retry_failed()
    settle(app)
    assert all(m.status == '已完成' for m in app.store.messages.values())
    first = app.summary_tree.get_children()[0]
    app.summary_tree.selection_set(first)
    app.update()
    key = app.current_key
    assert key
    app.summary_box.delete('1.0', 'end')
    app.summary_box.insert('1.0', '人工摘要,保留\n換行')
    app.todos_entry.delete('1.0', 'end')
    app.todos_entry.insert('1.0', '=SUM(1,2)')
    app.commit_editor()
    assert app.store.messages[key].summary == '人工摘要,保留\n換行'
    assert app.store.messages[key].status == '人工修改'
    file = tmp_path / 'export.csv'
    monkeypatch.setattr('gmail_summary.ui.filedialog.asksaveasfilename', lambda **kwargs: str(file))
    assert app.export()
    assert not app.store.dirty
    app.filter_category.set('帳務')
    app.refresh_tables()
    assert len(app.summary_tree.get_children()) == 3
    app.store.revision += 1
    assert app.export(filtered=True)
    assert app.store.dirty, '部分匯出不可清除全部未保存狀態'
    app.filter_category.set('全部分類')
    app.refresh_tables()
    assert app.export()
    app.reset_session(None)
    monkeypatch.setattr('gmail_summary.ui.filedialog.askopenfilename', lambda **kwargs: str(file))
    app.import_file()
    assert len(app.store.messages) == 18
    assert app.store.messages[key].summary == '人工摘要,保留\n換行'
    assert all(not m.body for m in app.store.messages.values())
    app.set_theme('淺色')
    app.set_theme('深色')
    app.geometry('1100x740')
    app.update()
    assert set(calls) == {main_thread}, '所有 UI 事件必須在主執行緒更新'


def test_ui_cancel_isolation_and_close_guard(app, monkeypatch):
    app.enter_demo()
    settle(app)
    app.show_page('分類摘要')
    app.check_all('summary', True)
    app.analyze()
    # Allow some work to complete, then cancel while preserving partial results.
    end = time.monotonic() + .24
    while time.monotonic() < end:
        app.update()
        time.sleep(.01)
    app.cancel()
    settle(app)
    complete = sum(m.status == '已完成' for m in app.store.messages.values())
    assert 0 < complete < 18
    assert '已取消' in app.progress_text.get()
    assert app.store.dirty
    monkeypatch.setattr('gmail_summary.ui.messagebox.askyesnocancel', lambda *a, **k: None)
    app.close()
    assert app.winfo_exists(), '取消關閉必須保留資料'
    assert not app.confirm_discard()
    app.reset_session('another@example.invalid')
    assert not app.store.messages and not app.checked and app.current_key is None


def test_search_displays_current_query_without_losing_session(app):
    app.enter_demo()
    settle(app)
    assert len(app.search_tree.get_children()) == 18
    app.subject_entry.insert(0, '謝謝')
    app.search()
    settle(app)
    assert len(app.search_tree.get_children()) == 3
    assert len(app.store.messages) == 18
    assert len(app.summary_tree.get_children()) == 18


def test_body_restore_preserves_manual_edit_and_reauth_state(app):
    import copy
    from gmail_summary.demo import demo_messages, DEMO_ACCOUNT
    message = demo_messages()[0]
    imported = copy.deepcopy(message)
    imported.body = ''
    imported.summary = '人工保留'
    app.reset_session(DEMO_ACCOUNT)
    app.store.add(imported)
    app.handle_event('message', message)
    assert app.store.messages[message.key].body == message.body
    assert app.store.messages[message.key].summary == '人工保留'
    app.handle_event('reauth', '授權失效')
    assert app.mailbox is None and '重新登入' in app.connection.get()
    assert len(app.store.messages) == 1, '授權失效不應丟失尚未匯出的摘要'


def test_imap_login_restore_and_logout_without_oauth(app, monkeypatch):
    from unittest.mock import Mock
    from gmail_summary.imap_mail import GmailImapService
    mailbox = GmailImapService('reader@example.invalid', 'abcdefghijklmnop', Mock())
    app.imap_auth.login = Mock(return_value=mailbox)
    app.auth.logout = Mock()
    app.auth.login = Mock(side_effect=AssertionError('IMAP must not start Google OAuth'))
    app.auth.restore = Mock(side_effect=AssertionError('IMAP restore must not need OAuth'))
    app.connect_imap('reader@example.invalid', 'abcdefghijklmnop')
    settle(app)
    assert app.store.account == 'reader@example.invalid'
    assert app.mailbox is mailbox and app.settings.login_method == 'imap'
    assert 'IMAP 程式唯讀' in app.connection.get()
    app.auth.login.assert_not_called()
    from gmail_summary.auth import ReauthRequired
    app.imap_auth.login = Mock(side_effect=ReauthRequired('新應用程式密碼無效'))
    app.connect_imap('reader@example.invalid', 'ponmlkjihgfedcba')
    settle(app)
    assert app.mailbox is mailbox and app.store.account == mailbox.account
    app.imap_auth.restore = Mock(return_value=mailbox)
    app.connect(restore=True)
    settle(app)
    app.auth.restore.assert_not_called()
    app.imap_auth.logout = Mock()
    app.logout()
    settle(app)
    assert app.mailbox is None and app.store.account is None
    app.imap_auth.logout.assert_called_once()


def test_quick_login_dialog_is_masked_fits_and_cancel_does_not_save(app):
    import customtkinter as ctk
    from unittest.mock import Mock
    app.imap_auth.login = Mock(side_effect=AssertionError('Cancelled dialog must not connect'))
    app.quick_login()
    app.update()
    dialog = next(w for w in app.winfo_children() if isinstance(w, ctk.CTkToplevel))
    children = []

    def visit(widget):
        children.append(widget)
        for child in widget.winfo_children():
            visit(child)
    visit(dialog)
    password = next(w for w in children if isinstance(w, ctk.CTkEntry)
                    and w.cget('placeholder_text') == 'Google 產生的 16 碼，可含分隔空格')
    password.insert(0, 'abcdefghijklmnop')
    assert password.cget('show') == '•'
    button = next(w for w in children if isinstance(w, ctk.CTkButton) and w.cget('text') == '安全連線 Gmail')
    assert button.winfo_rooty() + button.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height()
    cancel = next(w for w in children if isinstance(w, ctk.CTkButton) and w.cget('text') == '取消')
    cancel.invoke()
    app.update()
    assert not dialog.winfo_exists()
    app.imap_auth.login.assert_not_called()


def test_imap_uid_placeholder_merge_preserves_existing_human_edit(app):
    from gmail_summary.models import Message
    account = 'reader@example.invalid'
    app.reset_session(account)
    placeholder = Message('imap:123:1', '', account, '', '', '', '讀取失敗', status='讀取失敗')
    canonical = Message('65', '3e8', account, '', '', '', '同一封信', summary='人工修改保留', status='人工修改')
    app.store.add(placeholder)
    app.store.add(canonical)
    app.checked.add(placeholder.key)
    restored = Message('65', '3e8', account, '', '', '', '同一封信', body='重新讀取的正文')
    app.handle_event('read_replaced', (placeholder.key, restored))
    assert len(app.store.messages) == 1
    assert app.store.messages[canonical.key].summary == '人工修改保留'
    assert app.store.messages[canonical.key].body == restored.body
    assert app.checked == {canonical.key}
