from __future__ import annotations

import hashlib

import keyring
from keyring.errors import PasswordDeleteError


class SecretStore:
    SERVICE = "GmailSummaryDesktop"

    def _check_backend(self) -> None:
        # Refuse plaintext / third-party fallback stores, including keyrings.alt.
        backend = keyring.get_keyring()
        module = type(backend).__module__
        if module not in {"keyring.backends.Windows", "keyring.backends.macOS", "keyring.backends.SecretService"}:
            raise RuntimeError("找不到安全的作業系統 keyring。請啟用 Windows 認證管理員；不會改存明文。")

    def get(self, name: str) -> str | None:
        self._check_backend()
        return keyring.get_password(self.SERVICE, name)

    def set(self, name: str, value: str) -> None:
        self._check_backend()
        keyring.set_password(self.SERVICE, name, value)

    def delete(self, name: str) -> None:
        self._check_backend()
        try:
            keyring.delete_password(self.SERVICE, name)
        except PasswordDeleteError:
            pass


def api_key_slot(provider: str, base_url: str) -> str:
    digest = hashlib.sha256((provider + "\n" + base_url.rstrip("/")).encode()).hexdigest()[:24]
    return "model-api-" + digest
