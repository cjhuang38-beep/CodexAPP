from __future__ import annotations

import copy
from datetime import date
from pathlib import Path
from queue import Empty
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from .ai import OpenAICompatibleService, validate_endpoint
from .auth import AuthService, ReauthRequired
from .csvio import export_csv, import_csv
from .demo import DEMO_ACCOUNT, DemoAnalyzer, DemoMailbox
from .gmail import GmailService
from .imap_mail import GmailImapService, ImapAuth, validate_login
from .models import MailStore, Search
from .security import SecretStore, api_key_slot
from .settings import Settings, oauth_path
from .workers import JobRunner, analyze_batch, user_error


class App(ctk.CTk):
    def __init__(self, demo: bool = False, auto_restore: bool = True):
        super().__init__()
        self.settings = Settings.load()
        ctk.set_appearance_mode("Dark" if self.settings.appearance == "深色" else "Light")
        self.title("拾信 · Gmail 分類與摘要")
        self.geometry("1360x860")
        self.minsize(1060, 720)
        self.store = MailStore()
        self.secrets = SecretStore()
        self.auth = AuthService(self.secrets, oauth_path())
        self.imap_auth = ImapAuth(self.secrets)
        self.jobs = JobRunner()
        self.mailbox = None
        self.is_demo = False
        self.demo_analyzer = DemoAnalyzer()
        self.pending = False
        self.current_key = None
        self.editor_snapshot = None
        self.row_keys = {}
        self.checked = set()
        self.search_result_keys = None
        self.page = "帳號設定"
        self.connection = tk.StringVar(value="尚未連線")
        self.account_text = tk.StringVar(value="尚未登入")
        self.progress_text = tk.StringVar(value="準備就緒")
        self.filter_category = tk.StringVar(value="全部分類")
        self.filter_text = tk.StringVar()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._layout()
        self.apply_theme()
        self.after(500, self.sync_dpi)
        self.after(80, self.poll)
        if demo:
            self.after(120, self.enter_demo)
        elif auto_restore:
            self.after(200, lambda: self.connect(restore=True))

    def _layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        sidebar = ctk.CTkFrame(self, width=184, corner_radius=0)
        sidebar.grid(row=0, column=0, rowspan=3, sticky="nsew")
        sidebar.grid_propagate(False)
        ctk.CTkLabel(sidebar, text="拾信", font=ctk.CTkFont(size=30, weight="bold")).pack(pady=(32, 2))
        ctk.CTkLabel(sidebar, text="GMAIL 分類與摘要", text_color="gray", font=("", 12)).pack(pady=(0, 30))
        self.nav_buttons = {}
        for label in ["帳號設定", "信件搜尋", "分類摘要", "匯入／匯出"]:
            b = ctk.CTkButton(sidebar, text=label, anchor="w", height=42,
                              command=lambda name=label: self.show_page(name))
            b.pack(fill="x", padx=14, pady=5)
            self.nav_buttons[label] = b
        ctk.CTkLabel(sidebar, text="唯讀 Gmail\n不寄信、不修改信件\n資料僅保留於本次執行", justify="left", text_color="gray").pack(side="bottom", pady=24)
        top = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        top.grid(row=0, column=1, sticky="ew", padx=24, pady=(18, 10))
        top.grid_columnconfigure(0, weight=1)
        self.page_title = ctk.CTkLabel(top, text="帳號設定", font=ctk.CTkFont(size=25, weight="bold"))
        self.page_title.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(top, textvariable=self.account_text).grid(row=0, column=1, padx=14)
        ctk.CTkLabel(top, textvariable=self.connection).grid(row=0, column=2)
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=1, column=1, sticky="nsew", padx=24)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        self.pages = {}
        self._account_page()
        self._search_page()
        self._summary_page()
        self._io_page()
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=2, column=1, sticky="ew", padx=24, pady=14)
        bottom.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bottom, textvariable=self.progress_text, anchor="w").grid(row=0, column=0, sticky="ew")
        self.cancel_button = ctk.CTkButton(bottom, text="取消工作", width=100, command=self.cancel, state="disabled")
        self.cancel_button.grid(row=0, column=1, padx=(12, 0))
        self.progress = ctk.CTkProgressBar(bottom)
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.progress.set(0)
        self.show_page("帳號設定")

    def new_page(self, name, scroll=False):
        frame = ctk.CTkScrollableFrame(self.content) if scroll else ctk.CTkFrame(self.content, fg_color="transparent")
        self.pages[name] = frame
        return frame

    def _account_page(self):
        page = self.new_page("帳號設定", scroll=True)
        page.grid_columnconfigure(0, weight=1)
        welcome = ctk.CTkFrame(page)
        welcome.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        ctk.CTkLabel(welcome, text="把信件整理成清楚的下一步", font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w", padx=24, pady=(24, 8))
        ctk.CTkLabel(welcome, text="快速連線：開啟 Google 兩步驟驗證，建立 16 碼應用程式密碼即可，不需要 Cloud Console。\n本程式只讀取信件、不下載附件；密碼存於 Windows 認證管理員。信件請主動匯出 CSV 保存。", justify="left").pack(anchor="w", padx=24, pady=(0, 18))
        buttons = ctk.CTkFrame(welcome, fg_color="transparent")
        buttons.pack(fill="x", padx=24, pady=(0, 24))
        self.login_button = ctk.CTkButton(buttons, text="連線 Gmail（免 Cloud 設定）", width=200, command=self.quick_login)
        self.login_button.pack(side="left", padx=(0, 10))
        ctk.CTkButton(buttons, text="進入 Demo（無需帳號）", command=self.enter_demo).pack(side="left", padx=6)
        ctk.CTkButton(buttons, text="重新連線", width=96, command=lambda: self.connect(restore=True)).pack(side="left", padx=6)
        ctk.CTkButton(buttons, text="登出", width=72, fg_color="gray40", command=self.logout).pack(side="left", padx=6)
        ctk.CTkButton(welcome, text="進階：Google 瀏覽器 OAuth 登入（需開發者設定）", fg_color="transparent",
                      command=lambda: self.connect()).pack(anchor="w", padx=24, pady=(0, 16))
        ai = ctk.CTkFrame(page)
        ai.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        ai.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(ai, text="AI 服務設定", font=ctk.CTkFont(size=19, weight="bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=20, pady=16)
        self.provider_var = tk.StringVar(value=self.settings.provider)
        self.provider_menu = ctk.CTkOptionMenu(ai, values=["OpenAI", "OpenAI 相容"], variable=self.provider_var, command=self.provider_changed)
        ctk.CTkLabel(ai, text="供應商").grid(row=1, column=0, padx=20, sticky="w")
        self.provider_menu.grid(row=1, column=1, sticky="w", padx=16, pady=6)
        self.model_entry = self.field(ai, 2, "模型名稱", self.settings.model)
        self.url_entry = self.field(ai, 3, "API 基礎網址", self.settings.base_url)
        self.key_entry = self.field(ai, 4, "API key（隱藏）", "", show="•")
        self.body_limit_entry = self.field(ai, 5, "最多傳送正文字元", str(self.settings.body_limit))
        ctk.CTkLabel(ai, text="key 留空會沿用此供應商／網址的 keyring 金鑰。變更網址後需重新儲存金鑰。\nGoogle 登入及 ChatGPT 訂閱不包含模型 API 額度；費用由供應商另計。", justify="left", text_color="gray").grid(row=6, column=0, columnspan=2, sticky="w", padx=20, pady=10)
        actions = ctk.CTkFrame(ai, fg_color="transparent")
        actions.grid(row=7, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 18))
        ctk.CTkButton(actions, text="儲存 AI 設定", command=self.save_settings).pack(side="left", padx=(0, 10))
        ctk.CTkButton(actions, text="刪除此服務 API key", fg_color="gray40", command=self.delete_api_key).pack(side="left")
        theme = ctk.CTkFrame(page)
        theme.grid(row=2, column=0, sticky="ew")
        ctk.CTkLabel(theme, text="外觀模式").pack(side="left", padx=20, pady=18)
        ctk.CTkOptionMenu(theme, values=["深色", "淺色"], variable=tk.StringVar(value=self.settings.appearance), command=self.set_theme).pack(side="left")

    @staticmethod
    def field(parent, row, label, value, **kwargs):
        ctk.CTkLabel(parent, text=label).grid(row=row, column=0, padx=20, sticky="w")
        entry = ctk.CTkEntry(parent, **kwargs)
        entry.insert(0, value)
        entry.grid(row=row, column=1, sticky="ew", padx=16, pady=6)
        return entry

    def _search_page(self):
        page = self.new_page("信件搜尋")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        form = ctk.CTkFrame(page)
        form.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        form.grid_columnconfigure((1, 3), weight=1)
        criteria = Search()
        self.start_entry = self.field(form, 0, "開始日期", criteria.start.isoformat())
        ctk.CTkLabel(form, text="結束日期（含當日）").grid(row=0, column=2, padx=16)
        self.end_entry = ctk.CTkEntry(form)
        self.end_entry.insert(0, criteria.end.isoformat())
        self.end_entry.grid(row=0, column=3, sticky="ew", padx=16, pady=6)
        self.sender_entry = self.field(form, 1, "寄件者", "")
        ctk.CTkLabel(form, text="主旨關鍵字").grid(row=1, column=2, padx=16)
        self.subject_entry = ctk.CTkEntry(form)
        self.subject_entry.grid(row=1, column=3, sticky="ew", padx=16, pady=6)
        self.advanced_entry = self.field(form, 2, "進階 Gmail 查詢", "")
        self.unread_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(form, text="僅未讀", variable=self.unread_var).grid(row=2, column=2, padx=16)
        self.limit_entry = ctk.CTkEntry(form, width=90)
        self.limit_entry.insert(0, "50")
        self.limit_entry.grid(row=2, column=3, sticky="w", padx=16)
        ctk.CTkLabel(form, text="台北時間 · YYYY-MM-DD · 最多 1～5000 封 · 搜尋結果累積於本次帳號，可在摘要頁篩選", text_color="gray").grid(row=3, column=0, columnspan=3, padx=16, pady=12, sticky="w")
        ctk.CTkButton(form, text="搜尋信件", command=self.search).grid(row=3, column=3, padx=16, pady=10)
        self.search_tree = self.make_tree(page, 1, "search")
        ctk.CTkLabel(page, text="雙擊第一欄勾選；Ctrl／Shift 多選。搜尋不會改變 Gmail 已讀狀態。", text_color="gray").grid(row=2, column=0, sticky="w", pady=10)
        controls = ctk.CTkFrame(page, fg_color="transparent")
        controls.grid(row=3, column=0, sticky="ew")
        self.selection_controls(controls, "search")
        ctk.CTkButton(controls, text="前往分類摘要", command=lambda: self.show_page("分類摘要")).pack(side="right")

    def make_tree(self, parent, row, name):
        frame = ctk.CTkFrame(parent)
        frame.grid(row=row, column=0, sticky="nsew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        columns = ("check", "date", "from", "subject", "category", "status")
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended", style="Mail.Treeview", height=8)
        for col, label, width in zip(columns, ["勾選", "日期", "寄件者", "主旨", "分類", "狀態"], [46, 115, 130, 270, 90, 95]):
            tree.heading(col, text=label)
            tree.column(col, width=width, minwidth=45, stretch=col in {"subject", "from"})
        tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        tree.bind("<Double-1>", lambda event, key=name: self.toggle_check(event, key))
        tree.bind("<space>", lambda event, key=name: self.check_selected(key))
        tree.bind("<<TreeviewSelect>>", lambda event, key=name: self.tree_selected(key))
        return tree

    def selection_controls(self, frame, name):
        for label, command in [
            ("全選可見信件", lambda: self.check_all(name, True)),
            ("取消勾選", lambda: self.check_all(name, False)),
            ("勾選所選列", lambda: self.check_selected(name)),
        ]:
            ctk.CTkButton(frame, text=label, width=110, command=command).pack(side="left", padx=(0, 8))

    def _summary_page(self):
        page = self.new_page("分類摘要")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)
        page.grid_rowconfigure(4, weight=1)
        filters = ctk.CTkFrame(page, fg_color="transparent")
        filters.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.category_filter = ctk.CTkOptionMenu(filters, values=["全部分類"] + self.settings.categories,
                                               variable=self.filter_category, command=lambda _: self.refresh_tables())
        self.category_filter.pack(side="left", padx=(0, 10))
        ctk.CTkEntry(filters, textvariable=self.filter_text, placeholder_text="篩選主旨、寄件者或摘要").pack(side="left", fill="x", expand=True)
        ctk.CTkButton(filters, text="套用篩選", width=90, command=self.refresh_tables).pack(side="left", padx=8)
        ctk.CTkButton(filters, text="管理分類", width=90, command=self.manage_categories).pack(side="right")
        actions = ctk.CTkFrame(page, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="ew", pady=6)
        ctk.CTkButton(actions, text="分析勾選／所選信件", command=self.analyze).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="重試失敗", command=self.retry_failed).pack(side="left", padx=8)
        self.simulate_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(actions, text="Demo 模擬部分失敗", variable=self.simulate_var).pack(side="left", padx=10)
        self.summary_tree = self.make_tree(page, 2, "summary")
        controls = ctk.CTkFrame(page, fg_color="transparent")
        controls.grid(row=3, column=0, sticky="ew", pady=8)
        self.selection_controls(controls, "summary")
        self.count_label = ctk.CTkLabel(controls, text="0 封")
        self.count_label.pack(side="right")
        editor = ctk.CTkFrame(page)
        editor.grid(row=4, column=0, sticky="nsew")
        editor.grid_columnconfigure((0, 1), weight=1)
        editor.grid_rowconfigure(2, weight=1)
        self.detail_label = ctk.CTkLabel(editor, text="選擇信件查看摘要與原文", anchor="w")
        self.detail_label.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(8, 4))
        editbar = ctk.CTkFrame(editor, fg_color="transparent")
        editbar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12)
        self.editor_category = tk.StringVar(value=self.settings.categories[0])
        self.editor_category_menu = ctk.CTkOptionMenu(editbar, values=self.settings.categories, variable=self.editor_category, width=130)
        self.editor_category_menu.pack(side="left")
        ctk.CTkLabel(editbar, text="明確期限").pack(side="left", padx=8)
        self.deadline_entry = ctk.CTkEntry(editbar, width=160)
        self.deadline_entry.pack(side="left", padx=(0, 8))
        ctk.CTkButton(editbar, text="儲存人工修改", command=self.commit_editor, width=110).pack(side="right")
        self.summary_box = ctk.CTkTextbox(editor, height=95, wrap="word")
        self.summary_box.grid(row=2, column=0, sticky="nsew", padx=(12, 6), pady=8)
        self.body_box = ctk.CTkTextbox(editor, height=95, wrap="word", state="disabled")
        self.body_box.grid(row=2, column=1, rowspan=2, sticky="nsew", padx=(6, 12), pady=8)
        self.todos_entry = ctk.CTkTextbox(editor, height=55, wrap="word")
        self.todos_entry.grid(row=3, column=0, sticky="ew", padx=(12, 6), pady=(0, 8))
        self.todos_entry.insert("1.0", "")
        self.detail_note = ctk.CTkLabel(editor, text="左：摘要／待辦（可編輯）　右：正文（唯讀）", anchor="w", wraplength=850, justify="left")
        self.detail_note.grid(row=4, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 8))

    def _io_page(self):
        page = self.new_page("匯入／匯出", scroll=True)
        ctk.CTkLabel(page, text="保存你整理過的成果", font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w", padx=20, pady=24)
        ctk.CTkLabel(page, text="CSV 使用 UTF-8 BOM，可用 Excel 開啟。\n匯出包含人工修改、帳號、信件 ID 與內容限制；不包含原始正文、Token 或 API key。\n已防護試算表公式注入。CSV 仍含私人信件摘要，請自行妥善保存。", justify="left").pack(anchor="w", padx=20, pady=(0, 20))
        for text, command in [
            ("匯出目前分類／文字篩選結果", lambda: self.export(filtered=True)),
            ("匯出本次帳號全部結果", self.export),
            ("匯入先前 CSV", self.import_file),
        ]:
            ctk.CTkButton(page, text=text, width=300, height=42, command=command).pack(anchor="w", padx=20, pady=8)
        ctk.CTkLabel(page, text="「目前篩選」使用分類摘要頁的分類與文字條件。\n匯入支援查看及編輯；沒有原始正文的資料不能重新分析。\n每次只開啟一個帳號的資料。切換帳號、登出或關閉時，會提示保存未匯出修改。", justify="left", text_color="gray").pack(anchor="w", padx=20, pady=24)

    def show_page(self, name):
        if hasattr(self, "summary_box"):
            self.commit_editor(silent=True)
        self.page = name
        for frame in self.pages.values():
            frame.grid_remove()
        self.pages[name].grid(row=0, column=0, sticky="nsew")
        self.page_title.configure(text=name)
        for key, button in self.nav_buttons.items():
            button.configure(fg_color="#2563a8" if key == name else "transparent")

    def apply_theme(self):
        dark = self.settings.appearance == "深色"
        bg, fg, selected = ("#202834", "#e6edf5", "#24598d") if dark else ("#ffffff", "#203047", "#c5dff7")
        style = ttk.Style(self)
        style.theme_use("clam")
        # CTk scales its widgets per monitor. ttk needs explicit pixel metrics too.
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        self.table_scale = scale
        font_pixels = -round(14 * scale)
        style.configure("Mail.Treeview", background=bg, fieldbackground=bg, foreground=fg,
                        rowheight=round(32 * scale), borderwidth=0, font=("Microsoft JhengHei", font_pixels))
        style.configure("Mail.Treeview.Heading", background="#303c4d" if dark else "#e4ebf2", foreground=fg,
                        padding=round(8 * scale), font=("Microsoft JhengHei", font_pixels, "bold"))
        style.map("Mail.Treeview", background=[("selected", selected)], foreground=[("selected", fg)])
        style.map("Mail.Treeview.Heading", background=[("active", selected)])

    def sync_dpi(self):
        if abs(ctk.ScalingTracker.get_widget_scaling(self) - self.table_scale) > .01:
            self.apply_theme()
        self.after(500, self.sync_dpi)

    def set_theme(self, theme):
        self.settings.appearance = theme
        ctk.set_appearance_mode("Dark" if theme == "深色" else "Light")
        self.apply_theme()
        self.save_preferences()

    def provider_changed(self, provider):
        if provider == "OpenAI":
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, "https://api.openai.com/v1")
        self.key_entry.delete(0, "end")

    def save_preferences(self):
        try:
            self.settings.save()
        except OSError:
            messagebox.showerror("設定儲存失敗", "無法寫入一般偏好設定；請檢查使用者資料夾權限。", parent=self)

    def save_settings(self, silent=False):
        if self.pending:
            if not silent:
                self.notice("請先等待或取消目前工作。")
            return False
        try:
            provider = self.provider_var.get()
            url = validate_endpoint(self.url_entry.get().strip())
            if provider == "OpenAI" and url != "https://api.openai.com/v1":
                raise ValueError("OpenAI 供應商僅使用官方網址；其他服務請選「OpenAI 相容」。")
            limit = int(self.body_limit_entry.get())
            if not 1000 <= limit <= 200000:
                raise ValueError("正文長度需介於 1000～200000 字元。")
            model = self.model_entry.get().strip()
            if not model:
                raise ValueError("請輸入模型名稱。")
            key = self.key_entry.get().strip()
            if key:
                self.secrets.set(api_key_slot(provider, url), key)
                self.key_entry.delete(0, "end")
            self.settings.provider, self.settings.base_url = provider, url
            self.settings.model, self.settings.body_limit = model, limit
            self.settings.save()
        except Exception as exc:
            messagebox.showerror("設定未儲存", user_error(exc), parent=self)
            return False
        if not silent:
            self.notice("已儲存設定；API key 僅存於作業系統 keyring。")
        return True

    def delete_api_key(self):
        if self.block_busy():
            return
        try:
            url = validate_endpoint(self.url_entry.get().strip())
            self.secrets.delete(api_key_slot(self.provider_var.get(), url))
            self.key_entry.delete(0, "end")
            self.notice("此服務的 API key 已從 keyring 刪除。")
        except Exception as exc:
            self.notice(user_error(exc))

    def notice(self, text):
        messagebox.showinfo("拾信", text, parent=self)

    def block_busy(self):
        if self.pending:
            self.notice("請等待目前工作完成，或按「取消工作」。網路呼叫會於逾時或回應後停止。")
            return True
        return False

    def confirm_discard(self):
        self.commit_editor(silent=True)
        if not self.store.dirty:
            return True
        answer = messagebox.askyesnocancel("保存變更", "有尚未匯出的信件或修改。是否先匯出全部結果？\n「否」會捨棄資料；「取消」會留在目前畫面。", parent=self)
        if answer is None:
            return False
        if answer:
            return self.export()
        return True

    def start_job(self, task, label):
        if self.pending:
            return False
        self.pending = True
        self.set_editor_state("disabled")
        self.progress.set(0)
        self.progress_text.set(label)
        self.cancel_button.configure(state="normal")
        self.jobs.start(task)
        return True

    def quick_login(self):
        if self.block_busy():
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("連線 Gmail · 不需要 Cloud Console")
        dialog.geometry("660x570")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        dialog.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(dialog, text="使用 Google 應用程式密碼", font=ctk.CTkFont(size=23, weight="bold")).grid(
            row=0, column=0, padx=24, pady=(24, 12), sticky="w")
        ctk.CTkLabel(dialog, text="① 開啟 Google 兩步驟驗證　② 建立 16 碼應用程式密碼\n③ 在下方輸入 Email 與該密碼，即可連線。", justify="left").grid(
            row=1, column=0, padx=24, sticky="w")
        links = ctk.CTkFrame(dialog, fg_color="transparent")
        links.grid(row=2, column=0, padx=24, pady=14, sticky="w")
        ctk.CTkButton(links, text="開啟兩步驟驗證", command=lambda: webbrowser.open("https://myaccount.google.com/signinoptions/two-step-verification")).pack(side="left", padx=(0, 10))
        ctk.CTkButton(links, text="建立應用程式密碼", command=lambda: webbrowser.open("https://myaccount.google.com/apppasswords")).pack(side="left")
        ctk.CTkLabel(dialog, text="Gmail／Google Workspace Email").grid(row=3, column=0, padx=24, sticky="w")
        account_entry = ctk.CTkEntry(dialog, placeholder_text="yourname@gmail.com")
        account_entry.grid(row=4, column=0, padx=24, pady=(4, 12), sticky="ew")
        if self.store.account and not self.is_demo:
            account_entry.insert(0, self.store.account)
        ctk.CTkLabel(dialog, text="16 碼應用程式密碼（不要輸入平常的 Google 密碼）").grid(row=5, column=0, padx=24, sticky="w")
        password_entry = ctk.CTkEntry(dialog, show="•", placeholder_text="Google 產生的 16 碼，可含分隔空格")
        password_entry.grid(row=6, column=0, padx=24, pady=(4, 12), sticky="ew")
        ctk.CTkLabel(dialog, text="密碼僅存於作業系統 keyring，可在 Google 帳號隨時撤銷。\n應用程式密碼沒有獨立唯讀 scope；本程式只以 IMAP 唯讀收信。\n部分學校／公司、進階保護帳號可能禁止此方式。不要把密碼貼到聊天。", justify="left", text_color="gray").grid(
            row=7, column=0, padx=24, pady=6, sticky="w")
        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.grid(row=8, column=0, padx=24, pady=16, sticky="e")

        def dismiss():
            password_entry.delete(0, "end")
            dialog.destroy()

        def submit():
            account, password = account_entry.get(), password_entry.get()
            try:
                account, password = validate_login(account, password)
            except ValueError as exc:
                messagebox.showinfo("請檢查連線資料", str(exc), parent=dialog)
                return
            dismiss()
            self.connect_imap(account, password)
        ctk.CTkButton(buttons, text="取消", width=90, fg_color="gray40", command=dismiss).pack(side="left", padx=10)
        ctk.CTkButton(buttons, text="安全連線 Gmail", command=submit).pack(side="left")
        dialog.protocol("WM_DELETE_WINDOW", dismiss)
        password_entry.bind("<Return>", lambda _: submit())
        account_entry.focus_set()

    def connect_imap(self, account, password):
        if self.block_busy() or (self.store.messages and not self.confirm_discard()):
            return
        self.connection.set("正在連線 Gmail IMAP…")

        def task(cancel, emit):
            try:
                mailbox = self.imap_auth.login(account, password, cancel)
            except ReauthRequired as exc:
                # A rejected replacement login must not disconnect the previous session.
                emit("error", str(exc))
                return
            if mailbox is not None and not cancel.is_set():
                # Remove obsolete OAuth credentials only after the new method succeeds.
                self.auth.logout()
                emit("connected", (mailbox, mailbox.account))
        self.start_job(task, "正在安全連線 imap.gmail.com；不需要 Cloud Console 設定。")

    def connect(self, restore=False):
        if self.block_busy():
            return
        if self.store.messages and not self.confirm_discard():
            return
        self.connection.set("正在恢復連線…" if restore else "等待瀏覽器授權…")
        preference = self.settings.login_method

        def task(cancel, emit):
            if restore:
                mailbox = self.imap_auth.restore() if preference == "imap" else None
                if cancel.is_set():
                    return
                if mailbox is not None:
                    emit("connected", (mailbox, mailbox.account))
                    return
            creds = self.auth.restore() if restore else self.auth.login(cancel)
            if cancel.is_set():
                return
            if not creds:
                emit("no_credentials")
                return
            mailbox = GmailService(self.auth, creds)
            account = mailbox.profile()
            if not cancel.is_set():
                if not restore:
                    self.imap_auth.logout()
                emit("connected", (mailbox, account))
        self.start_job(task, "正在恢復安全連線…" if restore else "請在系統瀏覽器完成 OAuth 授權（最長 180 秒）。")

    def enter_demo(self):
        if self.block_busy() or not self.confirm_discard():
            return
        self.reset_session(DEMO_ACCOUNT)
        self.mailbox = DemoMailbox()
        self.is_demo = True
        self.demo_analyzer = DemoAnalyzer()
        self.connection.set("Demo · 合成資料")
        self.show_page("信件搜尋")
        self.search()

    def reset_session(self, account):
        self.current_key = None
        self.editor_snapshot = None
        self.checked.clear()
        self.search_result_keys = None
        self.store.switch(account)
        self.mailbox = None
        self.is_demo = False
        self.account_text.set(account or "尚未登入")
        self.filter_category.set("全部分類")
        self.filter_text.set("")
        self.clear_editor()
        self.refresh_tables()

    def logout(self):
        if self.block_busy() or not self.confirm_discard():
            return

        def task(cancel, emit):
            self.auth.logout()
            self.imap_auth.logout()
            emit("logged_out")
        # Demo has no Google credential; leave any real keyring session untouched.
        if self.is_demo:
            self.reset_session(None)
            self.connection.set("已離開本次工作階段")
        else:
            self.start_job(task, "正在從本機 keyring 移除 Google 授權…")

    def search(self):
        if self.block_busy():
            return
        if not self.mailbox:
            self.notice("請先連線 Gmail 或進入 Demo。")
            return
        self.commit_editor(silent=True)
        try:
            criteria = Search(date.fromisoformat(self.start_entry.get().strip()), date.fromisoformat(self.end_entry.get().strip()),
                              self.sender_entry.get(), self.subject_entry.get(), self.unread_var.get(), self.advanced_entry.get(), int(self.limit_entry.get()))
            criteria.query()
        except ValueError:
            self.notice("請檢查日期（YYYY-MM-DD）、起訖順序及上限（1～5000）。")
            return
        mailbox = self.mailbox
        self.search_result_keys = set()
        self.refresh_tables()
        self.start_job(lambda cancel, emit: mailbox.search(criteria, cancel, emit), "正在搜尋與讀取信件…")

    def visible_messages(self, filtered=True):
        rows = list(self.store.messages.values())
        if not filtered:
            return rows
        category, text = self.filter_category.get(), self.filter_text.get().strip().casefold()
        return [m for m in rows if (category == "全部分類" or m.category == category)
                and (not text or text in (m.subject + m.sender_name + m.sender_email + m.summary).casefold())]

    def refresh_tables(self, commit=True):
        if commit:
            self.commit_editor(silent=True, refresh=False)
        if not hasattr(self, "search_tree"):
            return
        self.row_keys = {str(i): m.key for i, m in enumerate(self.store.messages.values())}
        iid_for_key = {key: iid for iid, key in self.row_keys.items()}
        for name, tree in [("search", self.search_tree), ("summary", self.summary_tree)]:
            selected = [self.row_keys.get(i) for i in tree.selection()]
            children = tree.get_children()
            if children:
                tree.delete(*children)
            rows = self.visible_messages(name == "summary")
            if name == "search" and self.search_result_keys is not None:
                rows = [m for m in rows if m.key in self.search_result_keys]
            for msg in rows:
                iid = iid_for_key[msg.key]
                tree.insert("", "end", iid=iid, values=("☑" if msg.key in self.checked else "☐", msg.date[:10],
                            msg.sender_name or msg.sender_email, msg.subject, msg.category, msg.status))
            for key in selected:
                iid = iid_for_key.get(key)
                if iid and tree.exists(iid):
                    tree.selection_add(iid)
        self.count_label.configure(text=f"可見 {len(self.visible_messages())} / 全部 {len(self.store.messages)} 封 · 勾選 {len(self.checked)}")

    def get_tree(self, name):
        return self.search_tree if name == "search" else self.summary_tree

    def toggle_check(self, event, name):
        if self.pending:
            return
        tree = self.get_tree(name)
        if tree.identify_column(event.x) == "#1":
            iid = tree.identify_row(event.y)
            if iid:
                key = self.row_keys[iid]
                if key in self.checked:
                    self.checked.remove(key)
                else:
                    self.checked.add(key)
                self.refresh_tables()

    def check_selected(self, name):
        if self.pending:
            return "break"
        for iid in self.get_tree(name).selection():
            self.checked.add(self.row_keys[iid])
        self.refresh_tables()
        return "break"

    def check_all(self, name, value):
        if self.pending:
            return
        for iid in self.get_tree(name).get_children():
            key = self.row_keys[iid]
            if value:
                self.checked.add(key)
            else:
                self.checked.discard(key)
        self.refresh_tables()

    def tree_selected(self, name):
        selection = self.get_tree(name).selection()
        if not selection:
            return
        key = self.row_keys.get(selection[-1])
        if key and key != self.current_key:
            self.commit_editor(silent=True, refresh=False)
            self.current_key = key
            self.load_editor()

    def set_editor_state(self, state):
        if not hasattr(self, "summary_box"):
            return
        for widget in [self.summary_box, self.todos_entry, self.deadline_entry, self.editor_category_menu]:
            widget.configure(state=state)

    def clear_editor(self):
        if not hasattr(self, "summary_box"):
            return
        self.set_editor_state("normal")
        for widget in [self.summary_box, self.todos_entry, self.body_box]:
            widget.configure(state="normal")
            widget.delete("1.0", "end")
        self.body_box.configure(state="disabled")
        self.deadline_entry.delete(0, "end")
        self.detail_label.configure(text="選擇信件查看摘要與原文")
        self.detail_note.configure(text="左：摘要／待辦（可編輯）　右：正文（唯讀）")

    def load_editor(self):
        msg = self.store.messages.get(self.current_key)
        if not msg:
            return
        self.clear_editor()
        self.detail_label.configure(text=f"{msg.subject[:90]}　｜　{msg.sender_email}")
        self.editor_category.set(msg.category)
        self.summary_box.insert("1.0", msg.summary)
        self.todos_entry.insert("1.0", msg.todos)
        self.deadline_entry.insert(0, msg.deadline)
        self.body_box.configure(state="normal")
        self.body_box.insert("1.0", msg.body or "此筆資料沒有原始正文，不能重新分析。")
        self.body_box.configure(state="disabled")
        self.detail_note.configure(text="；".join(filter(None, [msg.limitation, msg.error])) or "完整可用正文；摘要仍請人工核對。")
        self.editor_snapshot = self.editor_values()
        self.set_editor_state("disabled" if self.pending else "normal")

    def editor_values(self):
        return {"category": self.editor_category.get(), "summary": self.summary_box.get("1.0", "end-1c"),
                "todos": self.todos_entry.get("1.0", "end-1c"), "deadline": self.deadline_entry.get()}

    def commit_editor(self, silent=False, refresh=True):
        if not self.current_key or self.current_key not in self.store.messages or self.editor_snapshot is None:
            return
        values = self.editor_values()
        if values != self.editor_snapshot:
            self.store.edit(self.current_key, **values, status="人工修改", error="")
            self.editor_snapshot = values
            if refresh:
                self.refresh_tables(commit=False)
            self.progress_text.set("人工修改已保留於記憶體，請匯出 CSV 保存。")

    def analyze(self, messages=None):
        if self.block_busy():
            return
        self.commit_editor(silent=True)
        if messages is None:
            selected = self.checked or {self.row_keys[i] for i in self.summary_tree.selection()}
            messages = [m for m in self.store.messages.values() if m.key in selected]
        if not messages:
            self.notice("請先勾選或選取信件。")
            return
        eligible = [copy.deepcopy(m) for m in messages if m.body and m.status != "讀取失敗"]
        if not eligible:
            self.notice("這些信件沒有可用的原始正文。CSV 匯入資料只能查看與人工修改；可登入對應 Gmail 重新搜尋正文。")
            return
        if self.is_demo:
            self.demo_analyzer.simulate_failure = self.simulate_var.get()
            analyzer = self.demo_analyzer
            if not messagebox.askokcancel("Demo 分析", f"將模擬分析 {len(eligible)} 封合成信件。\n不會呼叫模型或傳送資料。", parent=self):
                return
        else:
            if not self.save_settings(silent=True):
                return
            if not messagebox.askokcancel("確認傳送信件資料", f"供應商：{self.settings.provider}\n網址：{self.settings.base_url}\n模型：{self.settings.model}\n\n將傳送 {len(eligible)} 封信的主旨、寄件者姓名及 Email、日期、正文（每封最多前 {self.settings.body_limit} 字元）、內容限制與分類清單。\n不傳送附件、Token、帳號憑證或整個信箱。\n\n可能產生 API 費用，並受供應商資料政策約束。AI 可能出錯，請核對重要資訊。\n是否繼續？", parent=self):
                return
            config = copy.deepcopy(self.settings)

            def real_task(cancel, emit):
                key = self.secrets.get(api_key_slot(config.provider, config.base_url))
                if not key:
                    raise RuntimeError("尚未設定此 AI 服務的 API key，請至帳號設定儲存。")
                adapter = OpenAICompatibleService(config.provider, config.base_url, config.model, key, config.body_limit)
                analyze_batch(eligible, adapter, config.categories, cancel, emit)
            self.start_job(real_task, f"正在使用 {self.settings.provider} 分析…")
            return
        categories = list(self.settings.categories)
        self.start_job(lambda cancel, emit: analyze_batch(eligible, analyzer, categories, cancel, emit), "正在產生 Demo 模擬摘要…")

    def retry_failed(self):
        if self.block_busy():
            return
        failures = [m for m in self.store.messages.values() if m.status in {"分析失敗", "讀取失敗"}]
        reads = [copy.deepcopy(m) for m in failures if m.status == "讀取失敗"]
        if reads:
            if not isinstance(self.mailbox, (GmailService, GmailImapService)):
                self.notice("請先連線對應 Gmail 帳號，再重試讀取失敗項目。")
                return
            mailbox = self.mailbox

            def task(cancel, emit):
                for index, msg in enumerate(reads, 1):
                    if cancel.is_set():
                        break
                    mailbox.retry_read(msg, cancel, emit)
                    emit("progress", (index, len(reads)))
            self.start_job(task, "正在重試讀取失敗信件；完成後可再次重試分析失敗。")
        else:
            self.analyze(failures)

    def cancel(self):
        self.jobs.cancel()
        self.progress_text.set("已要求取消，等待目前呼叫返回；已完成結果會保留。Google 授權最長等待 180 秒。")
        self.cancel_button.configure(state="disabled")

    def poll(self):
        try:
            while True:
                generation, kind, data = self.jobs.queue.get_nowait()
                if generation != self.jobs.generation:
                    continue
                self.handle_event(kind, data)
        except Empty:
            pass
        self.after(80, self.poll)

    def handle_event(self, kind, data):
        if kind == "message":
            if self.search_result_keys is not None:
                self.search_result_keys.add(data.key)
            existing = self.store.messages.get(data.key)
            if existing and not existing.body and data.body:
                # CSV records regain body for reanalysis without overwriting manual edits.
                existing.body = data.body
                existing.limitation = data.limitation
                existing.synthetic = data.synthetic
                if existing.status == "讀取失敗":
                    existing.status, existing.error = "待處理", ""
                self.store.revision += 1
            else:
                self.store.add(data)
            if data.key == self.current_key:
                self.load_editor()
            self.refresh_tables()
        elif kind == "read_replaced":
            old_key, replacement = data
            if old_key in self.store.messages:
                del self.store.messages[old_key]
                existing = self.store.messages.get(replacement.key)
                if existing is None:
                    self.store.messages[replacement.key] = replacement
                elif not existing.body:
                    # Resolve a UID placeholder without erasing canonical-row human edits.
                    existing.body = replacement.body
                    existing.limitation = replacement.limitation
                    if existing.status == "讀取失敗":
                        existing.status, existing.error = "待處理", ""
                self.store.revision += 1
                if old_key in self.checked:
                    self.checked.remove(old_key)
                    self.checked.add(replacement.key)
                if self.current_key == old_key:
                    self.current_key = replacement.key
                    self.load_editor()
                if self.search_result_keys is not None and old_key in self.search_result_keys:
                    self.search_result_keys.remove(old_key)
                    self.search_result_keys.add(replacement.key)
                self.refresh_tables()
        elif kind == "read_retry":
            if data.key in self.store.messages:
                self.store.messages[data.key] = data
                self.store.revision += 1
                self.refresh_tables()
        elif kind == "analysis":
            key, result = data
            if key in self.store.messages:
                self.store.edit(key, category=result.category, summary=result.summary, todos=result.todos,
                                deadline=result.deadline, limitation=result.limitation, status="已完成", error="")
                if key == self.current_key:
                    self.load_editor()
                self.refresh_tables()
        elif kind == "analysis_failed":
            key, error = data
            if key in self.store.messages:
                self.store.edit(key, status="分析失敗", error=error)
                if key == self.current_key:
                    self.load_editor()
                self.refresh_tables()
        elif kind == "progress":
            current, total = data
            self.progress.set(current / max(total, 1))
            self.progress_text.set(f"已處理 {current} / {total}；目前帳號共 {len(self.store.messages)} 封")
        elif kind == "connected":
            mailbox, account = data
            if self.store.account and self.store.account.casefold() == account.casefold():
                self.mailbox, self.is_demo = mailbox, False
            else:
                self.reset_session(account)
                self.mailbox = mailbox
            is_imap = isinstance(mailbox, GmailImapService)
            self.settings.login_method = "imap" if is_imap else "oauth"
            self.save_preferences()
            self.connection.set("已連線 · IMAP 程式唯讀" if is_imap else "已連線 · Gmail 唯讀")
        elif kind == "no_credentials":
            self.connection.set("請選擇連線 Gmail 或使用 Demo")
        elif kind == "logged_out":
            self.reset_session(None)
            self.connection.set("已登出 · 本機 Gmail 連線憑證已移除")
        elif kind == "reauth":
            self.mailbox = None
            self.connection.set("授權失效 · 請重新登入")
            self.progress_text.set(data)
            self.notice(data)
        elif kind == "error":
            if self.mailbox:
                self.connection.set("Demo · 合成資料" if self.is_demo else "先前連線保留 · 操作失敗")
            else:
                self.connection.set("未連線 · 可重試或使用 Demo")
            self.progress_text.set(data)
            self.notice(data)
        elif kind == "done":
            if data and ("等待" in self.connection.get() or "正在" in self.connection.get()):
                self.connection.set("已取消連線" if not self.mailbox else "先前連線保留")
            self.pending = False
            self.cancel_button.configure(state="disabled")
            self.set_editor_state("normal")
            failed = sum(m.status in {"分析失敗", "讀取失敗"} for m in self.store.messages.values())
            self.progress_text.set(f"{'已取消，保留已完成結果' if data else '工作結束'} · 共 {len(self.store.messages)} 封 · 失敗 {failed} 封")
            if not data:
                self.progress.set(1)
            self.refresh_tables()

    def export(self, filtered=False):
        if self.block_busy():
            return False
        self.commit_editor(silent=True)
        rows = self.visible_messages(filtered)
        if not rows:
            self.notice("沒有可匯出的資料。")
            return False
        path = filedialog.asksaveasfilename(parent=self, title="匯出 CSV", defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile="拾信摘要.csv")
        if not path:
            return False
        try:
            export_csv(Path(path), rows)
        except (OSError, ValueError, UnicodeError):
            self.notice("CSV 未儲存；請檢查檔案是否被 Excel 開啟、磁碟空間或權限。")
            return False
        if {m.key for m in rows} == set(self.store.messages):
            self.store.mark_exported()
        self.progress_text.set(f"已匯出 {len(rows)} 封信件；" + ("所有修改均已保存。" if not self.store.dirty else "仍有未匯出的資料。"))
        return True

    def import_file(self):
        if self.block_busy():
            return
        path = filedialog.askopenfilename(parent=self, title="匯入先前匯出的 CSV", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            rows = import_csv(Path(path))
            accounts = {m.account.casefold() for m in rows}
            if len(accounts) != 1:
                raise ValueError("CSV 必須包含且只包含一個帳號，避免混合不同帳號資料。")
            account = rows[0].account
            categories = list(self.settings.categories)
            for row in rows:
                if row.category and row.category not in categories:
                    categories.append(row.category)
            if len(categories) > 100 or any(len(c) > 60 for c in categories):
                raise ValueError("CSV 分類過多或分類名稱過長。")
            if self.store.account and self.store.account.casefold() != account.casefold():
                if not self.confirm_discard():
                    return
                self.reset_session(account)
                self.connection.set("CSV 離線資料 · 尚未連線 Gmail")
            elif not self.store.account:
                self.reset_session(account)
                self.connection.set("CSV 離線資料 · 尚未連線 Gmail")
            added = sum(self.store.add(row) for row in rows)
            self.settings.categories = categories
            self.update_category_menus()
            self.save_preferences()
            self.refresh_tables()
            self.show_page("分類摘要")
            self.progress_text.set(f"已匯入 {added} 筆；略過 {len(rows) - added} 筆重複 ID，保留原有修改。")
        except Exception as exc:
            self.notice(user_error(exc) if type(exc) is ValueError else "CSV 讀取失敗；請檢查格式、編碼或檔案權限。")

    def update_category_menus(self):
        self.category_filter.configure(values=["全部分類"] + self.settings.categories)
        self.editor_category_menu.configure(values=self.settings.categories)
        if self.filter_category.get() not in ["全部分類"] + self.settings.categories:
            self.filter_category.set("全部分類")

    def manage_categories(self):
        if self.block_busy():
            return
        self.commit_editor(silent=True)
        dialog = ctk.CTkToplevel(self)
        dialog.title("管理分類")
        dialog.geometry("420x330")
        dialog.transient(self)
        dialog.grab_set()
        chosen = tk.StringVar(value=self.settings.categories[0])
        menu = ctk.CTkOptionMenu(dialog, values=self.settings.categories, variable=chosen)
        menu.pack(padx=24, pady=18, fill="x")
        name = ctk.CTkEntry(dialog, placeholder_text="新分類名稱（最多 60 字）")
        name.pack(padx=24, pady=10, fill="x")
        replacement = tk.StringVar(value=self.settings.categories[-1])
        ctk.CTkLabel(dialog, text="刪除時，原信件分類移至：").pack()
        replace_menu = ctk.CTkOptionMenu(dialog, values=self.settings.categories, variable=replacement)
        replace_menu.pack(pady=8)

        def apply(action):
            old, new = chosen.get(), name.get().strip()
            categories = list(self.settings.categories)
            if action in {"add", "rename"}:
                if not new or len(new) > 60 or new in categories or len(categories) >= 100 and action == "add":
                    self.notice("請填入不重複的分類名稱（1～60 字）；最多 100 個分類。")
                    return
                if action == "add":
                    categories.append(new)
                else:
                    categories[categories.index(old)] = new
            else:
                new = replacement.get()
                if len(categories) == 1 or new == old:
                    self.notice("至少保留一個分類，且刪除時請選擇不同的替代分類。")
                    return
                categories.remove(old)
            if action != "add":
                for msg in self.store.messages.values():
                    if msg.category == old:
                        self.store.edit(msg.key, category=new)
            self.settings.categories = categories
            self.update_category_menus()
            self.save_preferences()
            self.load_editor()
            self.refresh_tables()
            dialog.destroy()
        actions = ctk.CTkFrame(dialog, fg_color="transparent")
        actions.pack(pady=18)
        for label, action in [("新增", "add"), ("改名", "rename"), ("刪除", "delete")]:
            ctk.CTkButton(actions, text=label, width=100, command=lambda a=action: apply(a)).pack(side="left", padx=5)

    def close(self):
        if self.pending:
            if messagebox.askyesno("工作仍在執行", "是否取消目前工作？\n請等待工作停止後再關閉，避免尚未保存的結果遺失。", parent=self):
                self.cancel()
            return
        if self.confirm_discard():
            self.destroy()
