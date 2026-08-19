"""Хранилище истории сообщений на SQLite.

Модуль sqlite3 блокирующий, поэтому каждый запрос выполняется в отдельном
потоке через asyncio.to_thread() — иначе обращение к диску тормозило бы весь
сервер, пока идёт запись.
"""

import asyncio
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

# Файл базы лежит рядом с server.py, независимо от того, откуда его запустили
DB_PATH = Path(__file__).with_name("velix.db")

# Сколько последних сообщений получает клиент при подключении
HISTORY_LIMIT = 50

_connection = None

# Соединение используется из разных потоков пула, поэтому обращения к нему
# сериализуем блокировкой.
_lock = threading.Lock()


def _init_sync(path):
    global _connection

    with _lock:
        _connection = sqlite3.connect(path, check_same_thread=False)
        # WAL — чтобы чтение истории не ждало записи очередного сообщения
        _connection.execute("PRAGMA journal_mode=WAL")
        _connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                nickname   TEXT NOT NULL,
                text       TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        _connection.commit()


def _save_sync(nickname, text):
    created_at = datetime.now(timezone.utc).isoformat()
    with _lock:
        _connection.execute(
            "INSERT INTO messages (nickname, text, created_at) VALUES (?, ?, ?)",
            (nickname, text, created_at),
        )
        _connection.commit()


def _last_messages_sync(limit):
    with _lock:
        # Берём последние limit записей, но возвращаем их в прямом порядке,
        # чтобы клиент прочитал переписку сверху вниз.
        rows = _connection.execute(
            """
            SELECT nickname, text, created_at FROM (
                SELECT id, nickname, text, created_at
                FROM messages
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id ASC
            """,
            (limit,),
        ).fetchall()
    return rows


def _close_sync():
    global _connection

    with _lock:
        if _connection is not None:
            _connection.close()
            _connection = None


async def init(path=DB_PATH):
    """Открывает базу и создаёт таблицу, если её ещё нет."""
    await asyncio.to_thread(_init_sync, path)


async def save_message(nickname, text):
    """Сохраняет одно сообщение."""
    await asyncio.to_thread(_save_sync, nickname, text)


async def last_messages(limit=HISTORY_LIMIT):
    """Возвращает последние сообщения списком (никнейм, текст, время)."""
    return await asyncio.to_thread(_last_messages_sync, limit)


async def close():
    """Закрывает соединение с базой."""
    await asyncio.to_thread(_close_sync)


def format_line(nickname, text, created_at):
    """Собирает строку истории в том же виде, в каком её увидит клиент."""
    # В базе время хранится в UTC, показываем в местном часовом поясе
    stamp = datetime.fromisoformat(created_at).astimezone().strftime("%d.%m %H:%M")
    return f"[{stamp}] [{nickname}]: {text}"
