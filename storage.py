"""Хранилище истории сообщений на SQLite.

Модуль sqlite3 блокирующий, поэтому каждый запрос выполняется в отдельном
потоке через asyncio.to_thread() — иначе обращение к диску тормозило бы весь
сервер, пока идёт запись.

Текст сообщений живёт в базе, а вложения — отдельными файлами в подкаталоге
media: гонять мегабайты через SQLite незачем, в базе остаётся только ссылка.
"""

import asyncio
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Файл базы лежит рядом с server.py, независимо от того, откуда его запустили
DB_PATH = Path(__file__).with_name("velix.db")
MEDIA_DIR = Path(__file__).with_name("media")

# Сколько последних сообщений получает клиент при подключении
HISTORY_LIMIT = 50

_connection = None
_media_dir = MEDIA_DIR

# Соединение используется из разных потоков пула, поэтому обращения к нему
# сериализуем блокировкой.
_lock = threading.Lock()

COLUMNS = {
    "kind": "TEXT NOT NULL DEFAULT 'text'",
    "media_id": "TEXT",
    "media_name": "TEXT",
    "media_size": "INTEGER",
}


def _init_sync(path, media_dir):
    global _connection, _media_dir

    _media_dir = Path(media_dir)
    _media_dir.mkdir(exist_ok=True)

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
        # База могла остаться от прежней версии — дописываем недостающие столбцы
        existing = {row[1] for row in _connection.execute("PRAGMA table_info(messages)")}
        for column, definition in COLUMNS.items():
            if column not in existing:
                _connection.execute(f"ALTER TABLE messages ADD COLUMN {column} {definition}")
        _connection.commit()


def _save_message_sync(nickname, text):
    created_at = datetime.now(timezone.utc).isoformat()
    with _lock:
        _connection.execute(
            "INSERT INTO messages (nickname, text, created_at, kind)"
            " VALUES (?, ?, ?, 'text')",
            (nickname, text, created_at),
        )
        _connection.commit()
    return created_at


def _save_media_sync(nickname, kind, name, data):
    media_id = uuid.uuid4().hex
    suffix = Path(name).suffix.lower()[:16]
    (_media_dir / f"{media_id}{suffix}").write_bytes(data)

    created_at = datetime.now(timezone.utc).isoformat()
    with _lock:
        _connection.execute(
            "INSERT INTO messages (nickname, text, created_at, kind,"
            " media_id, media_name, media_size) VALUES (?, '', ?, ?, ?, ?, ?)",
            (nickname, created_at, kind, media_id, name, len(data)),
        )
        _connection.commit()
    return media_id, created_at


def _media_bytes_sync(media_id):
    with _lock:
        row = _connection.execute(
            "SELECT kind, media_name FROM messages WHERE media_id = ?", (media_id,)
        ).fetchone()
    if row is None:
        return None

    kind, name = row
    matches = list(_media_dir.glob(f"{media_id}*"))
    if not matches:
        return None
    return kind, name, matches[0].read_bytes()


def _last_messages_sync(limit):
    with _lock:
        # Берём последние limit записей, но возвращаем их в прямом порядке,
        # чтобы клиент прочитал переписку сверху вниз.
        rows = _connection.execute(
            """
            SELECT nickname, text, created_at, kind, media_id, media_name, media_size
            FROM (
                SELECT id, nickname, text, created_at, kind,
                       media_id, media_name, media_size
                FROM messages
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id ASC
            """,
            (limit,),
        ).fetchall()

    items = []
    for nickname, text, created_at, kind, media_id, media_name, media_size in rows:
        item = {"nick": nickname, "at": created_at, "kind": kind or "text"}
        if item["kind"] == "text":
            item["text"] = text
        else:
            item["id"] = media_id
            item["name"] = media_name
            item["size"] = media_size
        items.append(item)
    return items


def _close_sync():
    global _connection

    with _lock:
        if _connection is not None:
            _connection.close()
            _connection = None


async def init(path=DB_PATH, media_dir=MEDIA_DIR):
    """Открывает базу, создаёт таблицу и каталог для вложений."""
    await asyncio.to_thread(_init_sync, path, media_dir)


async def save_message(nickname, text):
    """Сохраняет текстовое сообщение, возвращает время в UTC."""
    return await asyncio.to_thread(_save_message_sync, nickname, text)


async def save_media(nickname, kind, name, data):
    """Сохраняет вложение файлом, возвращает (идентификатор, время)."""
    return await asyncio.to_thread(_save_media_sync, nickname, kind, name, data)


async def media_bytes(media_id):
    """Возвращает (вид, имя, содержимое) вложения или None."""
    return await asyncio.to_thread(_media_bytes_sync, media_id)


async def last_messages(limit=HISTORY_LIMIT):
    """Возвращает последние сообщения списком словарей."""
    return await asyncio.to_thread(_last_messages_sync, limit)


async def close():
    """Закрывает соединение с базой."""
    await asyncio.to_thread(_close_sync)


def format_time(created_at):
    """Время сообщения в местном поясе, как его показывает клиент."""
    return datetime.fromisoformat(created_at).astimezone().strftime("%d.%m %H:%M")
