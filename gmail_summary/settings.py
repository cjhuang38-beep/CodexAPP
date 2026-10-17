from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_config_path

from .models import DEFAULT_CATEGORIES


def config_dir() -> Path:
    return user_config_path("GmailSummary", appauthor=False)


@dataclass
class Settings:
    appearance: str = "深色"
    login_method: str = "imap"
    categories: list[str] = field(default_factory=lambda: list(DEFAULT_CATEGORIES))
    provider: str = "OpenAI"
    model: str = "gpt-4.1-mini"
    base_url: str = "https://api.openai.com/v1"
    body_limit: int = 24000

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or config_dir() / "preferences.json"
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            obj = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            if (obj.appearance not in {"深色", "淺色"} or obj.login_method not in {"imap", "oauth"}
                    or obj.provider not in {"OpenAI", "OpenAI 相容"}
                    or not isinstance(obj.categories, list) or not obj.categories
                    or len(obj.categories) > 100
                    or not all(isinstance(c, str) and c.strip() and len(c) <= 60 for c in obj.categories)
                    or len(set(obj.categories)) != len(obj.categories)
                    or not isinstance(obj.model, str) or not isinstance(obj.base_url, str)
                    or not isinstance(obj.body_limit, int) or not 1000 <= obj.body_limit <= 200000):
                return cls()
            return obj
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path | None = None) -> None:
        path = path or config_dir() / "preferences.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)


def oauth_path() -> Path:
    """Developer/installer provisioning only; no end-user import dialog."""
    override = os.environ.get("GMAIL_SUMMARY_OAUTH_CLIENT")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "oauth_client.json"
    return Path(__file__).resolve().parent.parent / "oauth_client.json"
