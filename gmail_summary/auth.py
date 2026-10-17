from __future__ import annotations

import json
from pathlib import Path
from threading import Event

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .security import SecretStore

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_SLOT = "google-oauth-current"


class ReauthRequired(Exception):
    pass


class AuthCancelled(Exception):
    pass


class AuthService:
    def __init__(self, secrets: SecretStore, path: Path):
        self.secrets = secrets
        self.path = path

    def restore(self) -> Credentials | None:
        raw = self.secrets.get(TOKEN_SLOT)
        if not raw:
            return None
        try:
            saved = json.loads(raw)
            if set(saved.get("scopes", [])) != set(SCOPES):
                raise ReauthRequired("已儲存的授權範圍不同，請重新登入。")
            creds = Credentials.from_authorized_user_info(saved, SCOPES)
        except (ValueError, TypeError) as exc:
            self.secrets.delete(TOKEN_SLOT)
            raise ReauthRequired("授權資料失效，請重新登入。") from exc
        return self.ensure(creds)

    def ensure(self, creds: Credentials) -> Credentials:
        if not creds.valid:
            if not creds.refresh_token:
                self.logout()
                raise ReauthRequired("沒有可更新的授權，請重新登入。")
            try:
                creds.refresh(Request())
            except RefreshError as exc:
                # Transient refresh errors must not destroy a usable refresh token.
                if getattr(exc, "retryable", False):
                    raise RuntimeError("Google 暫時無法更新憑證，請稍後重試連線。") from exc
                self.logout()
                raise ReauthRequired("Google 授權已撤銷或過期，請重新登入。") from exc
            except TransportError as exc:
                raise RuntimeError("無法連線 Google；保留憑證，請稍後重試連線。") from exc
            self.save(creds)
        return creds

    def save(self, creds: Credentials) -> None:
        self.secrets.set(TOKEN_SLOT, creds.to_json())

    def login(self, cancel: Event) -> Credentials:
        if not self.path.exists():
            raise RuntimeError("開發者尚未配置 Google 桌面 OAuth 用戶端。可先使用 Demo；請參閱 docs/OAUTH.md。")
        try:
            config = json.loads(self.path.read_text(encoding="utf-8"))
            installed = config["installed"]
            if not installed.get("client_id", "").endswith(".apps.googleusercontent.com"):
                raise ValueError
            # Prevent alternate authorization/token endpoints from collecting credentials.
            if installed.get("auth_uri") != "https://accounts.google.com/o/oauth2/auth":
                raise ValueError
            if installed.get("token_uri") != "https://oauth2.googleapis.com/token":
                raise ValueError
        except (OSError, KeyError, ValueError, TypeError) as exc:
            raise RuntimeError("開發者的 OAuth 桌面用戶端設定格式不正確。") from exc
        flow = InstalledAppFlow.from_client_config(config, SCOPES, autogenerate_code_verifier=True)
        if cancel.is_set():
            raise AuthCancelled()
        # Google's official installed-app flow handles state validation and PKCE.
        # Only listen on loopback; the browser receives no password form from this app.
        flow.run_local_server(
            host="127.0.0.1", port=0, open_browser=True, timeout_seconds=180,
            authorization_prompt_message="請在系統瀏覽器完成 Google 帳號選擇與 Gmail 唯讀授權。",
            success_message="授權回應已收到，請關閉此頁並返回 Gmail 分類摘要 App。",
            access_type="offline", prompt="select_account consent",
        )
        if cancel.is_set():
            raise AuthCancelled()
        granted = set(flow.credentials.granted_scopes or flow.credentials.scopes or [])
        if not set(SCOPES).issubset(granted):
            raise ReauthRequired("未授予 Gmail 唯讀權限，無法讀取信件。請重新登入並同意 Gmail 讀取。")
        self.save(flow.credentials)
        return flow.credentials

    def logout(self) -> None:
        self.secrets.delete(TOKEN_SLOT)
