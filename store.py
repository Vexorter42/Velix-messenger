"""Что клиент помнит между запусками: аккаунты и настройки.

Файл лежит в профиле пользователя, рядом с настройками других программ.
Пароли тут не хранятся — только токены сессий, выданные сервером. Токен
можно отозвать выходом из аккаунта, пароль так не отзовёшь.
"""

import json
import os
import sys
from pathlib import Path


def config_dir():
    """Каталог с настройками: %APPDATA%\\Velix, а на других системах ~/.velix."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or Path.home()
        return Path(base) / "Velix"
    return Path.home() / ".velix"


CONFIG_PATH = config_dir() / "velix.json"

DEFAULTS = {
    "accounts": [],   # [{login, name, server, token}]
    "last": None,     # "логин@сервер" последнего входа
    "settings": {},   # пригодится для трея и автозапуска
}


def load(path=None):
    """Читает настройки. Битый или отсутствующий файл — это не беда."""
    path = Path(path or CONFIG_PATH)
    data = dict(DEFAULTS)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data.update({key: loaded[key] for key in DEFAULTS if key in loaded})
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass

    if not isinstance(data.get("accounts"), list):
        data["accounts"] = []
    if not isinstance(data.get("settings"), dict):
        data["settings"] = {}
    return data


def save(data, path=None):
    """Пишет настройки, создавая каталог при необходимости."""
    path = Path(path or CONFIG_PATH)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError:
        pass  # не смогли сохранить — работать это не мешает
    return data


def key_of(account):
    return f"{account.get('login')}@{account.get('server')}"


def remember_account(data, login, name, server, token):
    """Добавляет или обновляет запись об аккаунте и делает её последней."""
    account = {"login": login, "name": name, "server": server, "token": token}
    others = [item for item in data["accounts"] if key_of(item) != key_of(account)]
    data["accounts"] = [account] + others
    data["last"] = key_of(account)
    return data


def forget_account(data, account):
    """Убирает аккаунт из списка — например, после выхода."""
    data["accounts"] = [item for item in data["accounts"]
                        if key_of(item) != key_of(account)]
    if data.get("last") == key_of(account):
        data["last"] = None
    return data


def update_name(data, login, server, name):
    """Подтягивает новое имя в сохранённую запись."""
    for account in data["accounts"]:
        if account.get("login") == login and account.get("server") == server:
            account["name"] = name
    return data
