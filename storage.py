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
    "conversation_id": "INTEGER",
    "reply_to": "INTEGER",
    "deleted": "INTEGER NOT NULL DEFAULT 0",
}

# Общий чат существует всегда и лежит под первым номером
GENERAL_ID = 1
GENERAL_TITLE = "Общий чат"


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
            CREATE TABLE IF NOT EXISTS invites (
                code       TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                note       TEXT NOT NULL DEFAULT '',
                used_by    INTEGER,
                used_at    TEXT
            )
            """
        )
        # База могла остаться от прежней версии — дописываем недостающие столбцы
        existing = {row[1] for row in _connection.execute("PRAGMA table_info(messages)")}
        for column, definition in MESSAGE_COLUMNS.items():
            if column not in existing:
                _connection.execute(f"ALTER TABLE messages ADD COLUMN {column} {definition}")
        # Общий чат заводим сразу: он есть у всех и всегда
        _connection.execute(
            "INSERT OR IGNORE INTO conversations (id, kind, title, created_at)"
            " VALUES (?, 'room', ?, ?)", (GENERAL_ID, GENERAL_TITLE, now()))

        # Сообщения из прежних версий жили без переписок — считаем их общими
        _connection.execute(
            "UPDATE messages SET conversation_id = ? WHERE conversation_id IS NULL",
            (GENERAL_ID,))
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


def _conversations_sync(user_id):
    """Список переписок пользователя: общий чат плюс его личные."""
    with _lock:
        rows = _connection.execute(
            """
            SELECT c.id, c.kind, c.title
            FROM conversations c
            WHERE c.kind = 'room'
               OR c.id IN (SELECT conversation_id FROM members WHERE user_id = ?)
            ORDER BY c.kind DESC, c.id ASC
            """,
            (user_id,),
        ).fetchall()

        result = []
        for conversation_id, kind, title in rows:
            item = {"id": conversation_id, "kind": kind, "title": title}

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


def _is_member_sync(conversation_id, user_id):
    """Пускать ли пользователя в эту переписку."""
    with _lock:
        row = _connection.execute(
            "SELECT kind FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if row is None:
            return False
        if row[0] == "room":
            return True  # общий чат открыт всем, кто вошёл
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

def _save_message_sync(user_id, nickname, text, conversation_id, reply_to):
    created_at = now()
    with _lock:
        cursor = _connection.execute(
            "INSERT INTO messages (nickname, text, created_at, kind, user_id,"
            " conversation_id, reply_to) VALUES (?, ?, ?, 'text', ?, ?, ?)",
            (nickname, text, created_at, user_id, conversation_id, reply_to),
        )
        _connection.commit()
    return cursor.lastrowid, created_at


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
     media_size, deleted, reply_to, user_id, profile_name, avatar_id) = row

    item = {"id": message_id, "nick": profile_name or nickname, "at": created_at,
            "kind": kind or "text", "user": user_id}
    if avatar_id:
        item["avatar"] = avatar_id
    if reply_to:
        item["reply_to"] = reply_to

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
                  " u.name, u.avatar_id")


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
            "   AND (c.kind = 'room'"
            "        OR c.id IN (SELECT conversation_id FROM members WHERE user_id = ?))"
            " ORDER BY m.id DESC LIMIT ?",
            (pattern, user_id, limit),
        ).fetchall()

    found = []
    for row in rows:
        item = _row_to_item(row[:13])
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


async def direct_id(first, second):
    """Личная переписка двоих, создаётся при первом обращении."""
    return await asyncio.to_thread(_direct_id_sync, first, second)


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


async def save_message(user_id, nickname, text, conversation_id=GENERAL_ID,
                       reply_to=None):
    """Сохраняет текстовое сообщение, возвращает (номер, время в UTC)."""
    return await asyncio.to_thread(_save_message_sync, user_id, nickname, text,
                                   conversation_id, reply_to)


async def save_media(user_id, nickname, kind, name, data,
                     conversation_id=GENERAL_ID, reply_to=None):
    """Сохраняет вложение файлом, возвращает (номер, идентификатор, время)."""
    return await asyncio.to_thread(_save_media_sync, user_id, nickname, kind, name,
                                   data, conversation_id, reply_to)


async def toggle_reaction(message_id, user_id, emoji):
    """Ставит или снимает реакцию, возвращает (переписка, сводка)."""
    return await asyncio.to_thread(_toggle_reaction_sync, message_id, user_id, emoji)


async def reactions(message_ids):
    """Сводка реакций для списка сообщений."""
    return await asyncio.to_thread(_reactions_sync, list(message_ids))


async def messages(conversation_id, limit=HISTORY_LIMIT, before=None):
    """Сообщения переписки; before подгружает то, что старше."""
    return await asyncio.to_thread(_messages_sync, conversation_id, limit, before)


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


def format_time(created_at):
    """Время сообщения в местном поясе, как его показывает клиент."""
    return datetime.fromisoformat(created_at).astimezone().strftime("%d.%m %H:%M")
