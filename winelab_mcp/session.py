"""Постоянное хранилище сессии: куки, регион, телефон.

Заменяет копипаст `WINELAB_COOKIES`: авторизуемся один раз (`winelab-mcp login`
или инструменты `auth_*`), сессия переживает перезапуск сервера и работает
одинаково на macOS, Linux и Windows.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
APP_NAME = "winelab-mcp"

# Куки, которые имеет смысл переносить между запусками. Всё остальное
# (аналитика, ретаргетинг) не нужно и только раздувает файл.
KEEP_COOKIES = frozenset(
    {
        "JSESSIONID",
        "currentRegion",
        "currentPOS",
        "currentDeliveryMode",
        "qrator_ssid",
        "qrator_jsid",
        "acceleratorSecureGUID",
        "cookie-notification",
    }
)


def config_dir() -> Path:
    """Каталог конфигурации по правилам конкретной ОС.

    Переопределяется `WINELAB_CONFIG_DIR` — удобно для CI и для нескольких
    независимых аккаунтов на одной машине.
    """
    override = os.environ.get("WINELAB_CONFIG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
        root = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return root / APP_NAME


def session_path() -> Path:
    """Полный путь к файлу сессии (`WINELAB_SESSION` переопределяет)."""
    override = os.environ.get("WINELAB_SESSION", "").strip()
    if override:
        return Path(override).expanduser()
    return config_dir() / "session.json"


def parse_cookie_header(raw: str) -> dict[str, str]:
    """'JSESSIONID=abc; currentPOS=M735' -> {'JSESSIONID': 'abc', ...}."""
    out: dict[str, str] = {}
    for part in raw.split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            key, value = key.strip(), value.strip()
            if key:
                out[key] = value
    return out


class SessionStore:
    """JSON-файл с куками и регионом. Права 0600 там, где ОС это умеет."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else session_path()

    # --- чтение ---------------------------------------------------------

    def load(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
            return {}
        return data

    def cookies(self) -> dict[str, str]:
        raw = self.load().get("cookies")
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v) for k, v in raw.items() if k and v is not None}

    # --- запись ---------------------------------------------------------

    def save(
        self,
        cookies: Iterable[tuple[str, str]] | dict[str, str],
        *,
        region: str | None = None,
        phone: str | None = None,
    ) -> None:
        pairs = cookies.items() if isinstance(cookies, dict) else cookies
        kept = {k: v for k, v in pairs if k in KEEP_COOKIES}
        payload = {
            "version": SCHEMA_VERSION,
            "saved_at": int(time.time()),
            "region": region,
            "phone": phone,
            "cookies": kept,
        }
        self._write(payload)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _write(self, payload: dict[str, Any]) -> None:
        """Атомарная запись: временный файл рядом + rename."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".session-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                _chmod_private(Path(tmp))
                os.replace(tmp, self.path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
            _chmod_private(self.path)
        except OSError:
            # Сессия — кэш, а не источник истины: не смогли записать — работаем дальше.
            pass


def _chmod_private(path: Path) -> None:
    if sys.platform == "win32":
        return  # ACL по умолчанию и так ограничивают профилем пользователя
    try:
        path.chmod(0o600)
    except OSError:
        pass
