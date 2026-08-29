"""Хранилище Velix на SQLite: пользователи, сессии, сообщения, вложения.

Модуль sqlite3 блокирующий, поэтому каждый запрос выполняется в отдельном
потоке через asyncio.to_thread() — иначе обращение к диску тормозило бы весь
сервер, пока идёт запись.

Текст сообщений живёт в базе, а вложения и аватарки — отдельными файлами в
подкаталоге media: гонять мегабайты через SQLite незачем, в базе остаётся
только ссылка.
"""

import asyncio
import json
import os
import shutil
import sqlite3
import threading
import time
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
    "conversation_id": "INTEGER",
    "reply_to": "INTEGER",
    "deleted": "INTEGER NOT NULL DEFAULT 0",
    "forwarded": "TEXT",
    "edited_at": "TEXT",
}

# Код восстановления пароля хранится хешем, как и сам пароль
USER_COLUMNS = {"recovery_hash": "TEXT"}

# Закреплённое сообщение переписки — тоже дописываемый столбец
CONVERSATION_COLUMNS = {"pinned_id": "INTEGER", "avatar_id": "TEXT",
                        "created_by": "INTEGER"}

# Общий чат существует всегда и лежит под первым номером
# Общий чат превратился в обычную группу: она заведена первой и в ней
# состоят все, кто был в чате до этого.
GENERAL_ID = 1
GENERAL_TITLE = "Velix"


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
        _connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                kind       TEXT NOT NULL,
                title      TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        _connection.execute(
            """
            CREATE TABLE IF NOT EXISTS members (
                conversation_id INTEGER NOT NULL,
                user_id         INTEGER NOT NULL,
                PRIMARY KEY (conversation_id, user_id)
            )
            """
        )
        _connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pushes (
                endpoint   TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                data       TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        _connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reactions (
                message_id INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                emoji      TEXT NOT NULL,
                PRIMARY KEY (message_id, user_id, emoji)
            )
            """
        )
        _connection.execute(
            """
            CREATE TABLE IF NOT EXISTS receipts (
                message_id   INTEGER NOT NULL,
                user_id      INTEGER NOT NULL,
                delivered_at TEXT,
                read_at      TEXT,
                PRIMARY KEY (message_id, user_id)
            )
            """
        )
        _connection.execute(
            """
            CREATE TABLE IF NOT EXISTS invites (
                code       TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                note       TEXT NOT NULL DEFAULT '',
                used_by    INTEGER,
                used_at    TEXT
            )
            """
        )
        _connection.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                name  TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        # База могла остаться от прежней версии — дописываем недостающие столбцы
        existing = {row[1] for row in _connection.execute("PRAGMA table_info(messages)")}
        for column, definition in MESSAGE_COLUMNS.items():
            if column not in existing:
                _connection.execute(f"ALTER TABLE messages ADD COLUMN {column} {definition}")

        existing = {row[1] for row in _connection.execute("PRAGMA table_info(users)")}
        for column, definition in USER_COLUMNS.items():
            if column not in existing:
                _connection.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")

        existing = {row[1] for row in
                    _connection.execute("PRAGMA table_info(conversations)")}
        for column, definition in CONVERSATION_COLUMNS.items():
            if column not in existing:
                _connection.execute(
                    f"ALTER TABLE conversations ADD COLUMN {column} {definition}")
        # Первая группа заводится сразу, чтобы новой базе было куда писать
        _connection.execute(
            "INSERT OR IGNORE INTO conversations (id, kind, title, created_at)"
            " VALUES (?, 'group', ?, ?)", (GENERAL_ID, GENERAL_TITLE, now()))

        # Сообщения из прежних версий жили без переписок — считаем их общими
        _connection.execute(
            "UPDATE messages SET conversation_id = ? WHERE conversation_id IS NULL",
            (GENERAL_ID,))

        # Раньше общий чат был особой переписки без списка участников: теперь
        # это обычная группа, и все, кто в ней был, становятся её участниками
        room = _connection.execute(
            "SELECT id FROM conversations WHERE kind = 'room'").fetchall()
        for (conversation_id,) in room:
            _connection.execute(
                "UPDATE conversations SET kind = 'group', title = ? WHERE id = ?",
                (GENERAL_TITLE, conversation_id))
            _connection.execute(
                "INSERT OR IGNORE INTO members (conversation_id, user_id)"
                " SELECT ?, id FROM users", (conversation_id,))
        _connection.commit()


# ------------------------------------------------------------ пользователи

def _row_to_user(row):
    if row is None:
        return None
    return {"id": row[0], "login": row[1], "name": row[2],
            "bio": row[3], "avatar": row[4], "seen": row[5]}


USER_FIELDS = "id, login, name, bio, avatar_id, last_seen"


def _create_user_sync(login, password_hash, name, recovery_hash=None):
    with _lock:
        try:
            cursor = _connection.execute(
                "INSERT INTO users (login, password_hash, name, created_at,"
                " recovery_hash) VALUES (?, ?, ?, ?, ?)",
                (login, password_hash, name, now(), recovery_hash),
            )
            _connection.commit()
        except sqlite3.IntegrityError:
            return None  # логин занят
        row = _connection.execute(
            f"SELECT {USER_FIELDS} FROM users WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return _row_to_user(row)


def _recovery_row_sync(login):
    """Логин, номер и хеш кода восстановления."""
    with _lock:
        row = _connection.execute(
            "SELECT id, recovery_hash FROM users WHERE login = ?",
            (str(login).strip(),)).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _set_recovery_sync(user_id, recovery_hash):
    with _lock:
        _connection.execute("UPDATE users SET recovery_hash = ? WHERE id = ?",
                            (recovery_hash, user_id))
        _connection.commit()


def _set_password_sync(user_id, password_hash):
    """Меняет пароль и разом гасит все сессии.

    Если пароль уводили, у того, кто им пользовался, останется токен —
    поэтому вместе с паролем сбрасываем и все входы.
    """
    with _lock:
        _connection.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                            (password_hash, user_id))
        _connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        _connection.commit()


def _user_with_hash_sync(login):
    with _lock:
        row = _connection.execute(
            f"SELECT {USER_FIELDS}, password_hash FROM users WHERE login = ?", (login,)
        ).fetchone()
    if row is None:
        return None, None
    return _row_to_user(row[:6]), row[6]


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
            f"SELECT u.id, u.login, u.name, u.bio, u.avatar_id, u.last_seen"
            f" FROM sessions s"
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


# --------------------------------------------------------------- переписки

def _direct_id_sync(first, second):
    """Личная переписка двоих: находит существующую или заводит новую."""
    with _lock:
        row = _connection.execute(
            """
            SELECT c.id FROM conversations c
            JOIN members a ON a.conversation_id = c.id AND a.user_id = ?
            JOIN members b ON b.conversation_id = c.id AND b.user_id = ?
            WHERE c.kind = 'direct'
            LIMIT 1
            """,
            (first, second),
        ).fetchone()
        if row is not None:
            return row[0]

        cursor = _connection.execute(
            "INSERT INTO conversations (kind, title, created_at) VALUES ('direct', '', ?)",
            (now(),))
        conversation = cursor.lastrowid
        _connection.executemany(
            "INSERT OR IGNORE INTO members (conversation_id, user_id) VALUES (?, ?)",
            [(conversation, first), (conversation, second)])
        _connection.commit()
    return conversation


def _conversations_sync(user_id, only=None):
    """Список переписок пользователя: общий чат плюс его личные.

    only оставляет одну переписку — её же и той же выделки, чтобы
    обновлённая строчка не потеряла ни имени собеседника, ни последнего
    сообщения.
    """
    with _lock:
        rows = _connection.execute(
            """
            SELECT c.id, c.kind, c.title, c.pinned_id, c.avatar_id, c.created_by
            FROM conversations c
            WHERE c.id IN (SELECT conversation_id FROM members WHERE user_id = ?)
              AND (? IS NULL OR c.id = ?)
            ORDER BY c.kind DESC, c.id ASC
            """,
            (user_id, only, only),
        ).fetchall()

        result = []
        for conversation_id, kind, title, pinned, avatar, owner in rows:
            item = {"id": conversation_id, "kind": kind, "title": title}
            if pinned:
                item["pinned"] = pinned
            if avatar:
                item["avatar"] = avatar
            if owner:
                item["owner"] = owner

            if kind == "direct":
                # У личной переписки заголовок — это имя собеседника
                other = _connection.execute(
                    "SELECT u.id, u.name, u.avatar_id FROM members m"
                    " JOIN users u ON u.id = m.user_id"
                    " WHERE m.conversation_id = ? AND m.user_id != ?",
                    (conversation_id, user_id)).fetchone()
                if other is None:
                    continue
                item["title"] = other[1]
                item["user"] = other[0]
                if other[2]:
                    item["avatar"] = other[2]

            if kind == "group":
                # Кто уже в группе — чтобы клиент не звал их повторно
                item["members"] = [row[0] for row in _connection.execute(
                    "SELECT user_id FROM members WHERE conversation_id = ?",
                    (conversation_id,))]

            last = _connection.execute(
                "SELECT m.text, m.kind, m.created_at, u.name FROM messages m"
                " LEFT JOIN users u ON u.id = m.user_id"
                " WHERE m.conversation_id = ? AND m.kind != 'avatar' AND m.deleted = 0"
                " ORDER BY m.id DESC LIMIT 1",
                (conversation_id,)).fetchone()
            if last is not None:
                item["last"] = {"text": last[0], "kind": last[1],
                                "at": last[2], "nick": last[3]}
            result.append(item)
    return result


def _create_group_sync(title, member_ids, creator=None):
    """Заводит группу и сразу вписывает в неё участников."""
    with _lock:
        cursor = _connection.execute(
            "INSERT INTO conversations (kind, title, created_at, created_by)"
            " VALUES ('group', ?, ?, ?)", (title, now(), creator))
        conversation = cursor.lastrowid
        _connection.executemany(
            "INSERT OR IGNORE INTO members (conversation_id, user_id) VALUES (?, ?)",
            [(conversation, user_id) for user_id in member_ids])
        _connection.commit()
    return conversation


def _add_members_sync(conversation_id, member_ids):
    """Дописывает людей в существующую группу."""
    with _lock:
        _connection.executemany(
            "INSERT OR IGNORE INTO members (conversation_id, user_id) VALUES (?, ?)",
            [(conversation_id, user_id) for user_id in member_ids])
        _connection.commit()


def _conversation_sync(conversation_id):
    """Одна переписка — какой её увидит участник группы."""
    with _lock:
        row = _connection.execute(
            "SELECT id, kind, title, pinned_id, avatar_id, created_by"
            " FROM conversations WHERE id = ?",
            (conversation_id,)).fetchone()
    if row is None:
        return None
    item = {"id": row[0], "kind": row[1], "title": row[2]}
    if row[3]:
        item["pinned"] = row[3]
    if row[4]:
        item["avatar"] = row[4]
    if row[5]:
        item["owner"] = row[5]
    return item


def _is_member_sync(conversation_id, user_id):
    """Пускать ли пользователя в эту переписку."""
    with _lock:
        row = _connection.execute(
            "SELECT kind FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if row is None:
            return False
        member = _connection.execute(
            "SELECT 1 FROM members WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id)).fetchone()
    return member is not None


def _members_sync(conversation_id):
    with _lock:
        rows = _connection.execute(
            "SELECT user_id FROM members WHERE conversation_id = ?",
            (conversation_id,)).fetchall()
    return [row[0] for row in rows]


def _people_sync():
    """Все, кто зарегистрирован — для списка участников."""
    with _lock:
        rows = _connection.execute(
            f"SELECT {USER_FIELDS} FROM users ORDER BY name COLLATE NOCASE").fetchall()
    return [_row_to_user(row) for row in rows]


# -------------------------------------------------------------- приглашения

def _add_invite_sync(code, note):
    with _lock:
        _connection.execute(
            "INSERT OR IGNORE INTO invites (code, created_at, note) VALUES (?, ?, ?)",
            (code, now(), note),
        )
        _connection.commit()
    return code


def _take_invite_sync(code, user_id):
    """Помечает код использованным. False, если кода нет или он уже потрачен."""
    with _lock:
        cursor = _connection.execute(
            "UPDATE invites SET used_by = ?, used_at = ? WHERE code = ? AND used_by IS NULL",
            (user_id, now(), code),
        )
        _connection.commit()
    return cursor.rowcount == 1


def _invite_exists_sync(code):
    with _lock:
        row = _connection.execute(
            "SELECT used_by FROM invites WHERE code = ?", (code,)).fetchone()
    return row is not None and row[0] is None


def _list_invites_sync():
    with _lock:
        return _connection.execute(
            "SELECT code, created_at, note, used_by, used_at FROM invites"
            " ORDER BY created_at DESC").fetchall()


# --------------------------------------------------------------- сообщения

def _save_message_sync(user_id, nickname, text, conversation_id, reply_to,
                       forwarded=None):
    created_at = now()
    with _lock:
        cursor = _connection.execute(
            "INSERT INTO messages (nickname, text, created_at, kind, user_id,"
            " conversation_id, reply_to, forwarded)"
            " VALUES (?, ?, ?, 'text', ?, ?, ?, ?)",
            (nickname, text, created_at, user_id, conversation_id, reply_to,
             forwarded),
        )
        _connection.commit()
    return cursor.lastrowid, created_at


def _save_existing_media_sync(user_id, nickname, kind, name, size, media_id,
                              conversation_id, forwarded):
    """Пересылка вложения: файл уже лежит на диске, копируем только запись."""
    created_at = now()
    with _lock:
        cursor = _connection.execute(
            "INSERT INTO messages (nickname, text, created_at, kind,"
            " media_id, media_name, media_size, user_id, conversation_id,"
            " forwarded) VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, ?)",
            (nickname, created_at, kind, media_id, name, size, user_id,
             conversation_id, forwarded))
        _connection.commit()
    return cursor.lastrowid, created_at


def _message_sync(message_id):
    """Одно сообщение целиком — для пересылки и закрепления."""
    with _lock:
        row = _connection.execute(
            "SELECT " + MESSAGE_FIELDS + " FROM messages m"
            " LEFT JOIN users u ON u.id = m.user_id WHERE m.id = ?",
            (message_id,)).fetchone()
    return _row_to_item(row) if row else None


def _conversation_of_sync(message_id):
    with _lock:
        row = _connection.execute(
            "SELECT conversation_id FROM messages WHERE id = ?",
            (message_id,)).fetchone()
    return row[0] if row else None


def _set_conversation_avatar_sync(conversation_id, media_id, name, data):
    """Ставит фото группы, стирая прежнее."""
    suffix = Path(name).suffix.lower()[:16] or ".png"
    (_media_dir / f"{media_id}{suffix}").write_bytes(data)

    with _lock:
        row = _connection.execute(
            "SELECT avatar_id FROM conversations WHERE id = ?",
            (conversation_id,)).fetchone()
        previous = row[0] if row else None
        _connection.execute("UPDATE conversations SET avatar_id = ? WHERE id = ?",
                            (media_id, conversation_id))
        # Скрытая строчка в сообщениях: по ней вложение потом и находят.
        # Переписку не указываем, иначе фото всплыло бы в ленте.
        _connection.execute(
            "INSERT INTO messages (nickname, text, created_at, kind, media_id,"
            " media_name, media_size) VALUES ('', '', ?, 'avatar', ?, ?, ?)",
            (now(), media_id, name, len(data)),
        )
        _connection.commit()

    if previous:
        for stale in _media_dir.glob(f"{previous}*"):
            stale.unlink(missing_ok=True)
        with _lock:
            _connection.execute(
                "DELETE FROM messages WHERE kind = 'avatar' AND media_id = ?",
                (previous,))
            _connection.commit()
    return media_id


def _forget_media(media_ids):
    """Стирает файлы вложений с диска."""
    for media_id in media_ids:
        if not media_id:
            continue
        for path in _media_dir.glob(f"{media_id}*"):
            path.unlink(missing_ok=True)


def _delete_conversation_sync(conversation_id):
    """Убирает переписку целиком: сообщения, вложения, участников."""
    with _lock:
        rows = _connection.execute(
            "SELECT media_id FROM messages WHERE conversation_id = ?",
            (conversation_id,)).fetchall()
        # Фото группы лежит отдельной скрытой строчкой — убираем и его
        face = _connection.execute(
            "SELECT avatar_id FROM conversations WHERE id = ?",
            (conversation_id,)).fetchone()
        if face and face[0]:
            rows.append((face[0],))
            _connection.execute(
                "DELETE FROM messages WHERE kind = 'avatar' AND media_id = ?",
                (face[0],))
        _connection.execute(
            "DELETE FROM receipts WHERE message_id IN"
            " (SELECT id FROM messages WHERE conversation_id = ?)",
            (conversation_id,))
        _connection.execute(
            "DELETE FROM reactions WHERE message_id IN"
            " (SELECT id FROM messages WHERE conversation_id = ?)",
            (conversation_id,))
        _connection.execute("DELETE FROM messages WHERE conversation_id = ?",
                            (conversation_id,))
        _connection.execute("DELETE FROM members WHERE conversation_id = ?",
                            (conversation_id,))
        _connection.execute("DELETE FROM conversations WHERE id = ?",
                            (conversation_id,))
        _connection.commit()

    _forget_media(row[0] for row in rows)


def _delete_user_sync(user_id):
    """Убирает учётную запись.

    Сообщения остаются: иначе в переписке появились бы дыры, а у соседей
    пропала бы половина разговора. Имя в них уже записано.
    """
    with _lock:
        row = _connection.execute("SELECT login, avatar_id FROM users WHERE id = ?",
                                  (user_id,)).fetchone()
        if row is None:
            return None
        _connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        _connection.execute("DELETE FROM pushes WHERE user_id = ?", (user_id,))
        _connection.execute("DELETE FROM members WHERE user_id = ?", (user_id,))
        _connection.execute("DELETE FROM reactions WHERE user_id = ?", (user_id,))
        _connection.execute("DELETE FROM receipts WHERE user_id = ?", (user_id,))
        _connection.execute("UPDATE messages SET user_id = NULL WHERE user_id = ?",
                            (user_id,))
        _connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
        _connection.commit()

    _forget_media([row[1]])
    return row[0]


def _stats_sync():
    """Сводка для панели: кто есть, сколько чего и сколько это весит."""
    with _lock:
        users = _connection.execute(
            "SELECT id, login, name, created_at, last_seen,"
            " (SELECT COUNT(*) FROM messages m WHERE m.user_id = users.id)"
            " FROM users ORDER BY id").fetchall()
        rooms = _connection.execute(
            "SELECT c.id, c.kind, c.title, c.created_by,"
            " (SELECT COUNT(*) FROM members WHERE conversation_id = c.id),"
            " (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id)"
            " FROM conversations c ORDER BY c.id").fetchall()
        messages = _connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    files = list(_media_dir.glob("*"))
    media_bytes = sum(path.stat().st_size for path in files if path.is_file())
    database_bytes = sum(
        path.stat().st_size for path in
        [Path(_connection.execute("PRAGMA database_list").fetchone()[2])]
        if path.exists())

    usage = shutil.disk_usage(_media_dir)
    return {
        "users": [{"id": row[0], "login": row[1], "name": row[2],
                   "created": row[3], "seen": row[4], "messages": row[5]}
                  for row in users],
        "rooms": [{"id": row[0], "kind": row[1], "title": row[2], "owner": row[3],
                   "members": row[4], "messages": row[5]} for row in rooms],
        "messages": messages,
        "media_files": len([path for path in files if path.is_file()]),
        "media_bytes": media_bytes,
        "database_bytes": database_bytes,
        "disk_total": usage.total,
        "disk_free": usage.free,
    }


def _pin_sync(conversation_id, message_id):
    with _lock:
        _connection.execute("UPDATE conversations SET pinned_id = ? WHERE id = ?",
                            (message_id, conversation_id))
        _connection.commit()


def _save_media_sync(user_id, nickname, kind, name, data, conversation_id, reply_to):
    media_id = uuid.uuid4().hex
    suffix = Path(name).suffix.lower()[:16]
    (_media_dir / f"{media_id}{suffix}").write_bytes(data)

    created_at = now()
    with _lock:
        cursor = _connection.execute(
            "INSERT INTO messages (nickname, text, created_at, kind,"
            " media_id, media_name, media_size, user_id, conversation_id, reply_to)"
            " VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, ?)",
            (nickname, created_at, kind, media_id, name, len(data), user_id,
             conversation_id, reply_to),
        )
        _connection.commit()
    return cursor.lastrowid, media_id, created_at


def _settings_sync():
    with _lock:
        rows = _connection.execute("SELECT name, value FROM settings").fetchall()
    return {name: value for name, value in rows}


def _set_setting_sync(name, value):
    with _lock:
        _connection.execute(
            "INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)",
            (str(name), str(value)))
        _connection.commit()


def _media_path_sync(media_id):
    """Где лежит вложение — нужно, чтобы отдавать его по кускам."""
    matches = list(_media_dir.glob(f"{media_id}*"))
    return matches[0] if matches else None


def upload_dir():
    """Где копится недоехавшее.

    Непременно рядом с вложениями: на малине /tmp — это tmpfs, то есть
    оперативная память. Гигабайтное видео сначала съедало её, а потом
    переезд на карту падал с «invalid cross-device link», и вложение
    пропадало на девяносто девятом проценте.
    """
    папка = _media_dir / ".uploads"
    папка.mkdir(parents=True, exist_ok=True)
    return папка


def forget_stale_uploads(старше_часов=6):
    """Убирает огрызки прошлых загрузок: связь рвётся, файлы остаются."""
    порог = time.time() - старше_часов * 3600
    убрано = 0
    for файл in upload_dir().glob("velix-upload-*"):
        try:
            if файл.stat().st_mtime < порог:
                файл.unlink()
                убрано += 1
        except OSError:
            pass
    return убрано


def _save_media_file_sync(user_id, nickname, kind, name, path, size,
                          conversation_id, reply_to):
    """Записывает вложение, которое уже лежит готовым файлом.

    Большое видео нельзя держать в памяти целиком — оно приезжает кусками
    во временный файл, и сюда попадает только имя этого файла.
    """
    media_id = uuid.uuid4().hex
    suffix = Path(name).suffix.lower()[:16]
    куда = _media_dir / f"{media_id}{suffix}"
    try:
        Path(path).replace(куда)
    except OSError:
        # Файл оказался на другой файловой системе: переименовать нельзя,
        # придётся перенести содержимое
        shutil.move(str(path), str(куда))

    created_at = now()
    with _lock:
        cursor = _connection.execute(
            "INSERT INTO messages (nickname, text, created_at, kind,"
            " media_id, media_name, media_size, user_id, conversation_id, reply_to)"
            " VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, ?)",
            (nickname, created_at, kind, media_id, name, size, user_id,
             conversation_id, reply_to),
        )
        _connection.commit()
    return cursor.lastrowid, media_id, created_at


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


def _add_push_sync(user_id, subscription):
    endpoint = subscription.get("endpoint")
    if not endpoint:
        return False
    with _lock:
        _connection.execute(
            "INSERT OR REPLACE INTO pushes (endpoint, user_id, data, created_at)"
            " VALUES (?, ?, ?, ?)",
            (endpoint, user_id, json.dumps(subscription, ensure_ascii=False), now()))
        _connection.commit()
    return True


def _pushes_for_sync(user_ids):
    if not user_ids:
        return []
    marks = ",".join("?" * len(user_ids))
    with _lock:
        rows = _connection.execute(
            "SELECT user_id, data FROM pushes WHERE user_id IN (" + marks + ")",
            list(user_ids)).fetchall()
    return [(row[0], json.loads(row[1])) for row in rows]


def _drop_push_sync(endpoint):
    with _lock:
        _connection.execute("DELETE FROM pushes WHERE endpoint = ?", (endpoint,))
        _connection.commit()


def _mark_receipts_sync(user_id, message_ids, read):
    """Отмечает сообщения доставленными, а при read — и прочитанными.

    Свои сообщения не отмечаем: галочки показывают, что с ними стало у
    других. Возвращает номера сообщений, у которых отметка изменилась, —
    только о них есть смысл сообщать автору.
    """
    if not message_ids:
        return []

    marks = ",".join("?" * len(message_ids))
    stamp = now()
    with _lock:
        rows = _connection.execute(
            "SELECT id FROM messages WHERE id IN (" + marks + ")"
            " AND user_id IS NOT NULL AND user_id != ?",
            list(message_ids) + [user_id]).fetchall()
        theirs = [row[0] for row in rows]
        if not theirs:
            return []

        changed = []
        for message_id in theirs:
            existing = _connection.execute(
                "SELECT delivered_at, read_at FROM receipts"
                " WHERE message_id = ? AND user_id = ?",
                (message_id, user_id)).fetchone()
            if existing is None:
                _connection.execute(
                    "INSERT INTO receipts (message_id, user_id, delivered_at, read_at)"
                    " VALUES (?, ?, ?, ?)",
                    (message_id, user_id, stamp, stamp if read else None))
                changed.append(message_id)
            elif read and existing[1] is None:
                _connection.execute(
                    "UPDATE receipts SET read_at = ? WHERE message_id = ? AND user_id = ?",
                    (stamp, message_id, user_id))
                changed.append(message_id)
        _connection.commit()
    return changed


def _receipt_state_sync(message_ids):
    """Состояние галочек: sent, delivered или read.

    Две галочки — сообщение дошло хотя бы до кого-то из собеседников,
    синие — хотя бы кто-то его прочитал. В переписке на двоих это ровно
    то же самое, что «дошло до всех», а в группе иначе нельзя: один
    заброшенный аккаунт держал бы галочки серыми навсегда.
    """
    if not message_ids:
        return {}

    marks = ",".join("?" * len(message_ids))
    with _lock:
        rows = _connection.execute(
            "SELECT m.id, m.conversation_id, m.user_id FROM messages m"
            " WHERE m.id IN (" + marks + ")", list(message_ids)).fetchall()

        state = {}
        for message_id, conversation_id, author in rows:
            others = _connection.execute(
                "SELECT COUNT(*) FROM members WHERE conversation_id = ?"
                " AND user_id != ?", (conversation_id, author)).fetchone()[0]
            if not others:
                state[message_id] = "sent"
                continue

            delivered, read = _connection.execute(
                "SELECT COUNT(delivered_at), COUNT(read_at) FROM receipts"
                " WHERE message_id = ?", (message_id,)).fetchone()
            if read > 0:
                state[message_id] = "read"
            elif delivered > 0:
                state[message_id] = "delivered"
            else:
                state[message_id] = "sent"
    return state


def _toggle_reaction_sync(message_id, user_id, emoji):
    """Ставит или снимает реакцию. Возвращает (переписка, сводка) или None."""
    with _lock:
        row = _connection.execute(
            "SELECT conversation_id FROM messages"
            " WHERE id = ? AND deleted = 0 AND kind != 'avatar'",
            (message_id,)).fetchone()
        if row is None or row[0] is None:
            return None

        existing = _connection.execute(
            "SELECT 1 FROM reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
            (message_id, user_id, emoji)).fetchone()

        if existing:
            _connection.execute(
                "DELETE FROM reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
                (message_id, user_id, emoji))
        else:
            _connection.execute(
                "INSERT INTO reactions (message_id, user_id, emoji) VALUES (?, ?, ?)",
                (message_id, user_id, emoji))
        _connection.commit()

    return row[0], _reactions_sync([message_id]).get(message_id, {})


def _reactions_sync(message_ids):
    """Сводка реакций: сообщение -> {смайлик: [кто поставил]}."""
    if not message_ids:
        return {}
    marks = ",".join("?" * len(message_ids))
    with _lock:
        rows = _connection.execute(
            "SELECT message_id, emoji, user_id FROM reactions"
            " WHERE message_id IN (" + marks + ")",
            list(message_ids)).fetchall()

    summary = {}
    for message_id, emoji, user_id in rows:
        summary.setdefault(message_id, {}).setdefault(emoji, []).append(user_id)
    return summary


def _row_to_item(row):
    """Одна строка из базы — в то, что понимает клиент."""
    (message_id, nickname, text, created_at, kind, media_id, media_name,
     media_size, deleted, reply_to, user_id, profile_name, avatar_id,
     forwarded, edited_at) = row

    item = {"id": message_id, "nick": profile_name or nickname, "at": created_at,
            "kind": kind or "text", "user": user_id}
    if edited_at:
        item["edited"] = edited_at
    if avatar_id:
        item["avatar"] = avatar_id
    if reply_to:
        item["reply_to"] = reply_to
    if forwarded:
        item["forwarded"] = forwarded

    if deleted:
        item["kind"] = "deleted"
        return item

    if item["kind"] == "text":
        item["text"] = text
    else:
        item["media"] = media_id
        item["name"] = media_name
        item["size"] = media_size
    return item


MESSAGE_FIELDS = ("m.id, m.nickname, m.text, m.created_at, m.kind, m.media_id,"
                  " m.media_name, m.media_size, m.deleted, m.reply_to, m.user_id,"
                  " u.name, u.avatar_id, m.forwarded, m.edited_at")


def _messages_sync(conversation_id, limit, before):
    """Последние сообщения переписки; before подгружает те, что старше."""
    condition = "AND id < ?" if before else ""
    parameters = [conversation_id]
    if before:
        parameters.append(before)
    parameters.append(limit)

    with _lock:
        rows = _connection.execute(
            "SELECT " + MESSAGE_FIELDS + " FROM ("
            "  SELECT * FROM messages"
            "  WHERE conversation_id = ? AND kind != 'avatar' " + condition +
            "  ORDER BY id DESC LIMIT ?"
            ") m LEFT JOIN users u ON u.id = m.user_id ORDER BY m.id ASC",
            parameters,
        ).fetchall()
    return [_row_to_item(row) for row in rows]


def _messages_by_ids_sync(message_ids):
    """Кто автор этих сообщений и в какой они переписке."""
    if not message_ids:
        return []
    marks = ",".join("?" * len(message_ids))
    with _lock:
        rows = _connection.execute(
            "SELECT id, user_id, conversation_id FROM messages WHERE id IN ("
            + marks + ")", list(message_ids)).fetchall()
    return [{"id": row[0], "user": row[1], "conversation": row[2]} for row in rows]


def _quoted_sync(message_ids):
    """Выжимки сообщений, на которые отвечают."""
    if not message_ids:
        return {}
    marks = ",".join("?" * len(message_ids))
    with _lock:
        rows = _connection.execute(
            "SELECT " + MESSAGE_FIELDS + " FROM messages m"
            " LEFT JOIN users u ON u.id = m.user_id"
            " WHERE m.id IN (" + marks + ")",
            list(message_ids),
        ).fetchall()
    return {row[0]: _row_to_item(row) for row in rows}


def _delete_message_sync(message_id, user_id):
    """Прячет своё сообщение. Возвращает переписку или None."""
    with _lock:
        row = _connection.execute(
            "SELECT conversation_id, media_id FROM messages"
            " WHERE id = ? AND user_id = ? AND deleted = 0",
            (message_id, user_id)).fetchone()
        if row is None:
            return None
        _connection.execute(
            "UPDATE messages SET deleted = 1, text = '' WHERE id = ?", (message_id,))
        _connection.commit()

    # Вложение с диска тоже убираем: держать его больше незачем
    if row[1]:
        for path in _media_dir.glob(str(row[1]) + "*"):
            path.unlink(missing_ok=True)
    return row[0]


def _search_sync(user_id, query, limit):
    """Ищет текст в переписках, доступных этому пользователю."""
    pattern = "%" + query + "%"
    with _lock:
        rows = _connection.execute(
            "SELECT " + MESSAGE_FIELDS + ", m.conversation_id FROM messages m"
            " LEFT JOIN users u ON u.id = m.user_id"
            " JOIN conversations c ON c.id = m.conversation_id"
            " WHERE m.deleted = 0 AND m.kind = 'text' AND m.text LIKE ?"
            "   AND c.id IN (SELECT conversation_id FROM members WHERE user_id = ?)"
            " ORDER BY m.id DESC LIMIT ?",
            (pattern, user_id, limit),
        ).fetchall()

    found = []
    for row in rows:
        # Последний столбец — переписка, остальное разбирает общий помощник
        item = _row_to_item(row[:-1])
        item["conversation"] = row[13]
        found.append(item)
    return found


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


def _checkpoint_sync():
    """Сливает журнал в саму базу и обрезает его.

    В режиме WAL свежие записи копятся отдельным файлом velix.db-wal, а сам
    velix.db может месяцами оставаться заготовкой в одну страницу. Копия
    через sqlite3.backup читает и журнал, ей это не мешает, — но стоит
    кому-нибудь скопировать руками один velix.db, и он увезёт пустоту,
    будучи уверенным, что увёз переписку.

    Возвращает True, если слить удалось. Если базу в эту секунду читают,
    SQLite отвечает «занято» — не беда, сольётся в следующий раз.
    """
    with _lock:
        if _connection is None:
            return False
        try:
            занято, _, _ = _connection.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        except sqlite3.Error:
            return False
        return not занято


def _close_sync():
    global _connection

    with _lock:
        if _connection is not None:
            try:
                _connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                # Закрыться важнее, чем слить журнал
                pass
            _connection.close()
            _connection = None


# ------------------------------------------------------- асинхронная обёртка

async def init(path=DB_PATH, media_dir=MEDIA_DIR):
    """Открывает базу, создаёт таблицы и каталог для вложений."""
    await asyncio.to_thread(_init_sync, path, media_dir)


async def direct_id(first, second):
    """Личная переписка двоих, создаётся при первом обращении."""
    return await asyncio.to_thread(_direct_id_sync, first, second)


async def conversation_for(conversation_id, user_id):
    """Одна переписка глазами участника — как строчка в его списке."""
    rows = await asyncio.to_thread(_conversations_sync, user_id, conversation_id)
    return rows[0] if rows else None


async def conversations(user_id):
    """Переписки пользователя с последним сообщением в каждой."""
    return await asyncio.to_thread(_conversations_sync, user_id)


async def is_member(conversation_id, user_id):
    return await asyncio.to_thread(_is_member_sync, conversation_id, user_id)


async def members(conversation_id):
    return await asyncio.to_thread(_members_sync, conversation_id)


async def people():
    """Все зарегистрированные пользователи."""
    return await asyncio.to_thread(_people_sync)


def _touch_user_sync(user_id):
    """Отмечает, что человек был здесь только что. Возвращает эту отметку."""
    stamp = now()
    with _lock:
        _connection.execute("UPDATE users SET last_seen = ? WHERE id = ?",
                            (stamp, user_id))
        _connection.commit()
    return stamp


def _edit_message_sync(message_id, user_id, text):
    """Меняет текст своего сообщения. Возвращает (переписка, когда) или None.

    Чужое и удалённое не трогаем, вложение тоже: у него правится разве что
    подпись, а её у нас нет.
    """
    stamp = now()
    with _lock:
        row = _connection.execute(
            "SELECT conversation_id, user_id, kind, deleted FROM messages"
            " WHERE id = ?", (message_id,)).fetchone()
        if row is None or row[1] != user_id or row[2] != "text" or row[3]:
            return None
        _connection.execute(
            "UPDATE messages SET text = ?, edited_at = ? WHERE id = ?",
            (text, stamp, message_id))
        _connection.commit()
    return row[0], stamp


def _media_of_sync(conversation_id, limit=300):
    """Все вложения переписки, от свежих к старым.

    Лента показывает только последние сообщения, а фотографии ищут по всей
    переписке: «где та карта с прошлого лета» — это не про листание вверх.
    """
    with _lock:
        rows = _connection.execute(
            f"SELECT {MESSAGE_FIELDS} FROM messages m"
            " LEFT JOIN users u ON u.id = m.user_id"
            " WHERE m.conversation_id = ? AND m.deleted = 0"
            "   AND m.media_id IS NOT NULL AND m.media_id != ''"
            " ORDER BY m.id DESC LIMIT ?",
            (conversation_id, limit)).fetchall()
    return [_row_to_item(row) for row in rows]


def _first_user_sync():
    with _lock:
        row = _connection.execute("SELECT MIN(id) FROM users").fetchone()
    return row[0] if row else None


async def touch_user(user_id):
    """Отметка «был здесь»: ставится, когда человек уходит из сети."""
    return await asyncio.to_thread(_touch_user_sync, user_id)


async def edit_message(message_id, user_id, text):
    """Правит своё текстовое сообщение."""
    return await asyncio.to_thread(_edit_message_sync, message_id, user_id, text)


async def media_of(conversation_id, limit=300):
    """Вложения переписки — для вкладки «медиа»."""
    return await asyncio.to_thread(_media_of_sync, conversation_id, limit)


async def first_user():
    """Кто завёл аккаунт раньше всех из ныне живущих."""
    return await asyncio.to_thread(_first_user_sync)


async def add_invite(code, note=""):
    """Заводит код приглашения."""
    return await asyncio.to_thread(_add_invite_sync, code, note)


async def invite_exists(code):
    """Есть ли такой неиспользованный код."""
    return await asyncio.to_thread(_invite_exists_sync, code)


async def take_invite(code, user_id):
    """Забирает код за пользователем. False, если код уже потрачен."""
    return await asyncio.to_thread(_take_invite_sync, code, user_id)


async def list_invites():
    return await asyncio.to_thread(_list_invites_sync)


async def recovery_row(login):
    """Номер человека и хеш его кода восстановления."""
    return await asyncio.to_thread(_recovery_row_sync, login)


async def set_recovery(user_id, recovery_hash):
    """Кладёт новый код восстановления."""
    await asyncio.to_thread(_set_recovery_sync, user_id, recovery_hash)


async def set_password(user_id, password_hash):
    """Меняет пароль и сбрасывает все сессии."""
    await asyncio.to_thread(_set_password_sync, user_id, password_hash)


async def create_user(login, password_hash, name, recovery_hash=None):
    """Заводит пользователя. Возвращает профиль или None, если логин занят."""
    return await asyncio.to_thread(_create_user_sync, login, password_hash, name,
                                   recovery_hash)


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


async def message(message_id):
    """Одно сообщение целиком."""
    return await asyncio.to_thread(_message_sync, message_id)


async def conversation_of(message_id):
    """В какой переписке лежит сообщение."""
    return await asyncio.to_thread(_conversation_of_sync, message_id)


async def pin(conversation_id, message_id):
    """Закрепляет сообщение в переписке; None снимает закрепление."""
    await asyncio.to_thread(_pin_sync, conversation_id, message_id)


async def save_existing_media(user_id, nickname, kind, name, size, media_id,
                              conversation_id, forwarded):
    """Записывает пересылку вложения, не трогая сам файл."""
    return await asyncio.to_thread(_save_existing_media_sync, user_id, nickname,
                                   kind, name, size, media_id, conversation_id,
                                   forwarded)


async def save_message(user_id, nickname, text, conversation_id=GENERAL_ID,
                       reply_to=None, forwarded=None):
    """Сохраняет текстовое сообщение, возвращает (номер, время в UTC)."""
    return await asyncio.to_thread(_save_message_sync, user_id, nickname, text,
                                   conversation_id, reply_to, forwarded)


async def settings():
    """Настройки сервера, которые меняются на ходу."""
    return await asyncio.to_thread(_settings_sync)


async def set_setting(name, value):
    """Запоминает настройку."""
    await asyncio.to_thread(_set_setting_sync, name, value)


async def media_path(media_id):
    """Путь к файлу вложения или None."""
    return await asyncio.to_thread(_media_path_sync, media_id)


def _media_described_sync(media_id):
    """Вид, имя и путь вложения — всё, что нужно, чтобы его отдать."""
    with _lock:
        row = _connection.execute(
            "SELECT kind, media_name FROM messages WHERE media_id = ?",
            (media_id,)).fetchone()
    if row is None:
        return None

    matches = list(_media_dir.glob(f"{media_id}*"))
    if not matches:
        return None
    return row[0], row[1] or "файл", matches[0]


async def media_described(media_id):
    """Вид, имя и путь вложения или None."""
    return await asyncio.to_thread(_media_described_sync, media_id)


async def save_media_file(user_id, nickname, kind, name, path, size,
                          conversation_id=GENERAL_ID, reply_to=None):
    """Сохраняет вложение, уже лежащее готовым файлом."""
    return await asyncio.to_thread(_save_media_file_sync, user_id, nickname, kind,
                                   name, path, size, conversation_id, reply_to)


async def save_media(user_id, nickname, kind, name, data,
                     conversation_id=GENERAL_ID, reply_to=None):
    """Сохраняет вложение файлом, возвращает (номер, идентификатор, время)."""
    return await asyncio.to_thread(_save_media_sync, user_id, nickname, kind, name,
                                   data, conversation_id, reply_to)


async def add_push(user_id, subscription):
    """Запоминает подписку телефона на уведомления."""
    return await asyncio.to_thread(_add_push_sync, user_id, subscription)


async def pushes_for(user_ids):
    """Подписки перечисленных людей."""
    return await asyncio.to_thread(_pushes_for_sync, list(user_ids))


async def drop_push(endpoint):
    """Забывает протухшую подписку."""
    await asyncio.to_thread(_drop_push_sync, endpoint)


async def create_group(title, member_ids, creator=None):
    """Новая группа с участниками."""
    return await asyncio.to_thread(_create_group_sync, title, list(member_ids),
                                   creator)


async def set_conversation_avatar(conversation_id, media_id, name, data):
    """Ставит фото группы."""
    return await asyncio.to_thread(_set_conversation_avatar_sync, conversation_id,
                                   media_id, name, data)


async def delete_conversation(conversation_id):
    """Удаляет переписку вместе с сообщениями и вложениями."""
    await asyncio.to_thread(_delete_conversation_sync, conversation_id)


async def delete_user(user_id):
    """Удаляет учётную запись, оставляя её сообщения на месте."""
    return await asyncio.to_thread(_delete_user_sync, user_id)


async def stats():
    """Сводка для панели управления."""
    return await asyncio.to_thread(_stats_sync)


async def add_members(conversation_id, member_ids):
    """Дописывает людей в группу."""
    await asyncio.to_thread(_add_members_sync, conversation_id, list(member_ids))


async def conversation(conversation_id):
    """Описание одной переписки."""
    return await asyncio.to_thread(_conversation_sync, conversation_id)


async def mark_receipts(user_id, message_ids, read=False):
    """Отмечает чужие сообщения доставленными или прочитанными."""
    return await asyncio.to_thread(_mark_receipts_sync, user_id,
                                   list(message_ids), read)


async def receipt_state(message_ids):
    """Состояние галочек у перечисленных сообщений."""
    return await asyncio.to_thread(_receipt_state_sync, list(message_ids))


async def toggle_reaction(message_id, user_id, emoji):
    """Ставит или снимает реакцию, возвращает (переписка, сводка)."""
    return await asyncio.to_thread(_toggle_reaction_sync, message_id, user_id, emoji)


async def reactions(message_ids):
    """Сводка реакций для списка сообщений."""
    return await asyncio.to_thread(_reactions_sync, list(message_ids))


async def messages(conversation_id, limit=HISTORY_LIMIT, before=None):
    """Сообщения переписки; before подгружает то, что старше."""
    return await asyncio.to_thread(_messages_sync, conversation_id, limit, before)


async def messages_by_ids(message_ids):
    """Авторы и переписки перечисленных сообщений."""
    return await asyncio.to_thread(_messages_by_ids_sync, list(message_ids))


async def quoted(message_ids):
    """Выжимки сообщений, на которые отвечают."""
    return await asyncio.to_thread(_quoted_sync, list(message_ids))


async def delete_message(message_id, user_id):
    """Прячет своё сообщение, возвращает переписку или None."""
    return await asyncio.to_thread(_delete_message_sync, message_id, user_id)


async def search(user_id, query, limit=50):
    """Ищет текст в доступных пользователю переписках."""
    return await asyncio.to_thread(_search_sync, user_id, query, limit)


async def media_bytes(media_id):
    """Возвращает (вид, имя, содержимое) вложения или None."""
    return await asyncio.to_thread(_media_bytes_sync, media_id)


async def last_messages(limit=HISTORY_LIMIT):
    """Возвращает последние сообщения списком словарей."""
    return await asyncio.to_thread(_last_messages_sync, limit)


async def close():
    """Закрывает соединение с базой."""
    await asyncio.to_thread(_close_sync)


async def checkpoint():
    """Сливает журнал в базу. True, если получилось."""
    return await asyncio.to_thread(_checkpoint_sync)


def format_time(created_at):
    """Время сообщения в местном поясе, как его показывает клиент."""
    return datetime.fromisoformat(created_at).astimezone().strftime("%d.%m %H:%M")
