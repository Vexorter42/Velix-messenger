"""Хранилище Velix на SQLite: пользователи, сессии, сообщения, вложения.

Модуль sqlite3 блокирующий, поэтому каждый запрос выполняется в отдельном
потоке через asyncio.to_thread() — иначе обращение к диску тормозило бы весь
сервер, пока идёт запись.

Текст сообщений живёт в базе, а вложения и аватарки — отдельными файлами в
подкаталоге media: гонять мегабайты через SQLite незачем, в базе остаётся
только ссылка.
"""

import asyncio
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Файл базы лежит рядом с server.py, независимо от того, откуда его запустили.
# Переменные окружения VELIX_DB и VELIX_MEDIA уводят их в другое место —
# так поднимается тестовый сервер, которому нельзя трогать боевую переписку.
DB_PATH = Path(os.environ.get("VELIX_DB") or Path(__file__).with_name("velix.db"))
MEDIA_DIR = Path(os.environ.get("VELIX_MEDIA") or Path(__file__).with_name("media"))

# Сколько последних сообщений получает клиент при подключении
HISTORY_LIMIT = 50

_connection = None
_media_dir = MEDIA_DIR

# Соединение используется из разных потоков пула, поэтому обращения к нему
# сериализуем блокировкой.
_lock = threading.Lock()

MESSAGE_COLUMNS = {
    "kind": "TEXT NOT NULL DEFAULT 'text'",
    "media_id": "TEXT",
    "media_name": "TEXT",
    "media_size": "INTEGER",
    "user_id": "INTEGER",
}


def now():
    return datetime.now(timezone.utc).isoformat()


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
        _connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                login         TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                name          TEXT NOT NULL,
                bio           TEXT NOT NULL DEFAULT '',
                avatar_id     TEXT,
                created_at    TEXT NOT NULL,
                last_seen     TEXT
            )
            """
        )
        _connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
            """
        )
        # База могла остаться от прежней версии — дописываем недостающие столбцы
        existing = {row[1] for row in _connection.execute("PRAGMA table_info(messages)")}
        for column, definition in MESSAGE_COLUMNS.items():
            if column not in existing:
                _connection.execute(f"ALTER TABLE messages ADD COLUMN {column} {definition}")
        _connection.commit()


# ------------------------------------------------------------ пользователи

def _row_to_user(row):
    if row is None:
        return None
    return {"id": row[0], "login": row[1], "name": row[2],
            "bio": row[3], "avatar": row[4]}


USER_FIELDS = "id, login, name, bio, avatar_id"


def _create_user_sync(login, password_hash, name):
    with _lock:
        try:
            cursor = _connection.execute(
                "INSERT INTO users (login, password_hash, name, created_at)"
                " VALUES (?, ?, ?, ?)",
                (login, password_hash, name, now()),
            )
            _connection.commit()
        except sqlite3.IntegrityError:
            return None  # логин занят
        row = _connection.execute(
            f"SELECT {USER_FIELDS} FROM users WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return _row_to_user(row)


def _user_with_hash_sync(login):
    with _lock:
        row = _connection.execute(
            f"SELECT {USER_FIELDS}, password_hash FROM users WHERE login = ?", (login,)
        ).fetchone()
    if row is None:
        return None, None
    return _row_to_user(row[:5]), row[5]


def _user_by_id_sync(user_id):
    with _lock:
        row = _connection.execute(
            f"SELECT {USER_FIELDS} FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return _row_to_user(row)


def _remember_token_sync(token, user_id):
    with _lock:
        _connection.execute(
            "INSERT OR REPLACE INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, user_id, now()),
        )
        _connection.execute("UPDATE users SET last_seen = ? WHERE id = ?", (now(), user_id))
        _connection.commit()


def _user_by_token_sync(token):
    with _lock:
        row = _connection.execute(
            f"SELECT u.id, u.login, u.name, u.bio, u.avatar_id FROM sessions s"
            f" JOIN users u ON u.id = s.user_id WHERE s.token = ?", (token,)
        ).fetchone()
        if row is not None:
            _connection.execute("UPDATE users SET last_seen = ? WHERE id = ?",
                                (now(), row[0]))
            _connection.commit()
    return _row_to_user(row)


def _forget_token_sync(token):
    with _lock:
        _connection.execute("DELETE FROM sessions WHERE token = ?", (token,))
        _connection.commit()


def _update_profile_sync(user_id, name, bio):
    with _lock:
        _connection.execute("UPDATE users SET name = ?, bio = ? WHERE id = ?",
                            (name, bio, user_id))
        _connection.commit()
        row = _connection.execute(
            f"SELECT {USER_FIELDS} FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return _row_to_user(row)


def _set_avatar_sync(user_id, name, data):
    avatar_id = uuid.uuid4().hex
    suffix = Path(name).suffix.lower()[:16] or ".png"
    (_media_dir / f"{avatar_id}{suffix}").write_bytes(data)

    with _lock:
        old = _connection.execute("SELECT avatar_id FROM users WHERE id = ?",
                                  (user_id,)).fetchone()
        _connection.execute("UPDATE users SET avatar_id = ? WHERE id = ?",
                            (avatar_id, user_id))
        _connection.execute(
            "INSERT INTO messages (nickname, text, created_at, kind, media_id,"
            " media_name, media_size, user_id) VALUES ('', '', ?, 'avatar', ?, ?, ?, ?)",
            (now(), avatar_id, name, len(data), user_id),
        )
        _connection.commit()

    # Прежнюю аватарку убираем: она больше нигде не показывается
    if old and old[0]:
        for path in _media_dir.glob(f"{old[0]}*"):
            path.unlink(missing_ok=True)
        with _lock:
            _connection.execute("DELETE FROM messages WHERE kind = 'avatar' AND media_id = ?",
                                (old[0],))
            _connection.commit()

    return avatar_id


# --------------------------------------------------------------- сообщения

def _save_message_sync(user_id, nickname, text):
    created_at = now()
    with _lock:
        _connection.execute(
            "INSERT INTO messages (nickname, text, created_at, kind, user_id)"
            " VALUES (?, ?, ?, 'text', ?)",
            (nickname, text, created_at, user_id),
        )
        _connection.commit()
    return created_at


def _save_media_sync(user_id, nickname, kind, name, data):
    media_id = uuid.uuid4().hex
    suffix = Path(name).suffix.lower()[:16]
    (_media_dir / f"{media_id}{suffix}").write_bytes(data)

    created_at = now()
    with _lock:
        _connection.execute(
            "INSERT INTO messages (nickname, text, created_at, kind,"
            " media_id, media_name, media_size, user_id)"
            " VALUES (?, '', ?, ?, ?, ?, ?, ?)",
            (nickname, created_at, kind, media_id, name, len(data), user_id),
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
    return kind, name or "файл", matches[0].read_bytes()


def _last_messages_sync(limit):
    with _lock:
        # Берём последние limit записей, но возвращаем их в прямом порядке,
        # чтобы клиент прочитал переписку сверху вниз. Имя и аватарку тянем
        # из профиля: если человек переименовался, старые сообщения тоже
        # должны показывать новое имя.
        rows = _connection.execute(
            """
            SELECT nickname, text, created_at, kind, media_id, media_name,
                   media_size, name, avatar_id
            FROM (
                SELECT m.id, m.nickname, m.text, m.created_at, m.kind,
                       m.media_id, m.media_name, m.media_size,
                       u.name, u.avatar_id
                FROM messages m
                LEFT JOIN users u ON u.id = m.user_id
                WHERE m.kind != 'avatar'
                ORDER BY m.id DESC
                LIMIT ?
            )
            ORDER BY id ASC
            """,
            (limit,),
        ).fetchall()

    items = []
    for (nickname, text, created_at, kind, media_id, media_name, media_size,
         profile_name, avatar_id) in rows:
        item = {"nick": profile_name or nickname, "at": created_at,
                "kind": kind or "text"}
        if avatar_id:
            item["avatar"] = avatar_id
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


# ------------------------------------------------------- асинхронная обёртка

async def init(path=DB_PATH, media_dir=MEDIA_DIR):
    """Открывает базу, создаёт таблицы и каталог для вложений."""
    await asyncio.to_thread(_init_sync, path, media_dir)


async def create_user(login, password_hash, name):
    """Заводит пользователя. Возвращает профиль или None, если логин занят."""
    return await asyncio.to_thread(_create_user_sync, login, password_hash, name)


async def user_with_hash(login):
    """Возвращает (профиль, хеш пароля) или (None, None)."""
    return await asyncio.to_thread(_user_with_hash_sync, login)


async def user_by_id(user_id):
    return await asyncio.to_thread(_user_by_id_sync, user_id)


async def remember_token(token, user_id):
    await asyncio.to_thread(_remember_token_sync, token, user_id)


async def user_by_token(token):
    """Профиль по токену сессии или None."""
    return await asyncio.to_thread(_user_by_token_sync, token)


async def forget_token(token):
    await asyncio.to_thread(_forget_token_sync, token)


async def update_profile(user_id, name, bio):
    return await asyncio.to_thread(_update_profile_sync, user_id, name, bio)


async def set_avatar(user_id, name, data):
    """Сохраняет аватарку, возвращает её идентификатор."""
    return await asyncio.to_thread(_set_avatar_sync, user_id, name, data)


async def save_message(user_id, nickname, text):
    """Сохраняет текстовое сообщение, возвращает время в UTC."""
    return await asyncio.to_thread(_save_message_sync, user_id, nickname, text)


async def save_media(user_id, nickname, kind, name, data):
    """Сохраняет вложение файлом, возвращает (идентификатор, время)."""
    return await asyncio.to_thread(_save_media_sync, user_id, nickname, kind, name, data)


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
