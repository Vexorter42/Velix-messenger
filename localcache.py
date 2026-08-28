"""Переписка, сохранённая на диске, — чтобы читать её без связи.

Связь рвётся в метро, в лифте, в поезде и просто когда захочет. До сих пор
в такие минуты Velix показывал пустоту: история живёт на сервере, а спросить
его не у кого. Теперь последнее, что он присылал, лежит рядом с настройками
и показывается сразу — ещё до того, как соединение установится.

Хранится нарочно просто: по файлу JSON на переписку, рядом с настройками.
Никакой второй базы: сообщений тут сотни, а не миллионы, и человеку важно
не «быстро искать», а «увидеть сразу».
"""

import json
import os
import re
import time
from pathlib import Path

import store

# Сколько сообщений держим на переписку. Двести — это несколько экранов
# прокрутки: больше человек без сети всё равно не отлистает
LIMIT = 200

# Сколько переписок помним. Забытые чистятся: место не бесконечное
ROOMS = 40


def cache_dir():
    """Где лежит сохранённое — рядом с настройками.

    Место считаем от store.CONFIG_PATH, а не от каталога настроек вообще:
    проверки подменяют именно его, и без этого одна из них однажды записала
    свою переписку в настоящий профиль хозяина.
    """
    свой = os.environ.get("VELIX_CACHE")
    основа = Path(свой) if свой else Path(store.CONFIG_PATH).parent
    папка = основа / "offline"
    папка.mkdir(parents=True, exist_ok=True)
    return папка


def _имя(server, кусок):
    """Безопасное имя файла: адрес сервера в него не пускаем как есть."""
    чистый = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(server or "server"))[:60]
    return f"{чистый}--{кусок}.json"


def _прочитать(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _записать(path, что):
    try:
        path.write_text(json.dumps(что, ensure_ascii=False), encoding="utf-8")
        return True
    except (OSError, TypeError, ValueError):
        return False        # не сохранилось — на экране всё равно всё есть


# ------------------------------------------------------- список переписок

def save_rooms(server, me, items):
    """Запоминает, кто мы и какие у нас переписки."""
    return _записать(cache_dir() / _имя(server, "rooms"),
                     {"me": me or {}, "items": list(items or []),
                      "at": time.time()})


def load_rooms(server):
    """Возвращает (кто мы, список переписок). Пусто — значит, ещё не входили."""
    сохранённое = _прочитать(cache_dir() / _имя(server, "rooms")) or {}
    return сохранённое.get("me") or {}, сохранённое.get("items") or []


# ------------------------------------------------------------- переписка

def save_history(server, conversation, items):
    """Кладёт последние сообщения переписки."""
    хвост = list(items or [])[-LIMIT:]
    ладно = _записать(cache_dir() / _имя(server, f"room-{int(conversation)}"),
                      {"items": хвост, "at": time.time()})
    if ладно:
        prune()
    return ладно


def load_history(server, conversation):
    """Что мы помним об этой переписке."""
    сохранённое = _прочитать(
        cache_dir() / _имя(server, f"room-{int(conversation)}")) or {}
    return сохранённое.get("items") or []


def prune(rooms=ROOMS):
    """Оставляет только последние переписки, к которым обращались."""
    файлы = sorted(cache_dir().glob("*--room-*.json"),
                   key=lambda path: path.stat().st_mtime, reverse=True)
    убрано = 0
    for лишний in файлы[rooms:]:
        try:
            лишний.unlink()
            убрано += 1
        except OSError:
            pass
    return убрано


def forget(server=None):
    """Забывает сохранённое — целиком или по одному серверу."""
    образец = "*.json" if server is None else _имя(server, "*")
    убрано = 0
    for файл in cache_dir().glob(образец):
        try:
            файл.unlink()
            убрано += 1
        except OSError:
            pass
    return убрано
