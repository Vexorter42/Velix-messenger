"""Протокол Velix: как клиент и сервер разговаривают.

Каждое сообщение — это кадр с JSON. Файлы передаются двумя кадрами подряд:
сначала JSON с описанием, сразу за ним двоичный кадр с содержимым. Так
картинка не раздувается на треть, как было бы при base64.
"""

import json
from pathlib import Path

VERSION = 5

# Первая группа заведена на сервере первой; общего чата больше нет
GENERAL_ID = 1

# Больше этого файлы не принимаем — и сокет не забьётся, и малина цела
MAX_MEDIA_SIZE = 25 * 1024 * 1024

# Обновление приложения приезжает одним куском и весит заметно больше
# вложения, поэтому запас считаем по нему
MAX_UPDATE_SIZE = 80 * 1024 * 1024

# Запас поверх лимита: в кадр кроме файла попадают и служебные байты
MAX_FRAME_SIZE = max(MAX_MEDIA_SIZE, MAX_UPDATE_SIZE) + 1024 * 1024

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
GIF_SUFFIXES = {".gif"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}


def kind_of(filename):
    """Определяет вид вложения по расширению имени файла."""
    suffix = Path(filename).suffix.lower()
    if suffix in GIF_SUFFIXES:
        return "gif"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    return "file"


def encode(payload):
    """Собирает кадр для отправки."""
    return json.dumps({"v": VERSION, **payload}, ensure_ascii=False)


def decode(frame):
    """Разбирает пришедший кадр.

    Возвращает словарь или None, если это не наш JSON — например, стучится
    клиент старой версии, который слал простой текст.
    """
    if not isinstance(frame, str):
        return None
    try:
        message = json.loads(frame)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(message, dict) or "type" not in message:
        return None
    return message


# --- то, что отправляет клиент ---

def text_message(nickname, text, conversation=1, reply_to=None, local=None):
    """Сообщение в переписку.

    local — номер, который отправитель дал сообщению у себя. Сервер
    вернёт его в ack вместе с настоящим номером: до этого у сообщения
    на экране ещё нет ни номера, ни галочек.
    """
    return encode({"type": "text", "nick": nickname, "text": text,
                   "conversation": conversation, "reply_to": reply_to,
                   "local": local})


def media_header(nickname, kind, name, size, conversation=1, reply_to=None,
                 local=None):
    return encode({"type": "media", "nick": nickname, "kind": kind,
                   "name": name, "size": size, "conversation": conversation,
                   "reply_to": reply_to, "local": local})


def fetch_request(media_id):
    return encode({"type": "fetch", "id": media_id})


# --- переписки ---

def open_request(conversation, before=None):
    """Открыть переписку; before подгружает то, что старше."""
    return encode({"type": "open", "conversation": conversation, "before": before})


def group_request(title, members):
    """Завести группу с перечисленными участниками."""
    return encode({"type": "group", "title": title, "members": list(members)})


def members_request(conversation, members):
    """Позвать людей в уже заведённую группу."""
    return encode({"type": "members", "conversation": conversation,
                   "members": list(members)})


def pin_request(conversation, message_id):
    """Закрепить сообщение; message_id=None снимает закрепление."""
    return encode({"type": "pin", "conversation": conversation, "id": message_id})


def pinned_message(conversation, item):
    """Что сейчас закреплено в переписке (item=None — ничего)."""
    return encode({"type": "pinned", "conversation": conversation, "item": item})


def forward_request(message_id, conversation):
    """Переслать сообщение в другую переписку."""
    return encode({"type": "forward", "id": message_id,
                   "conversation": conversation})


def read_request(conversation, message_ids):
    """Сообщить серверу, что эти сообщения прочитаны."""
    return encode({"type": "read", "conversation": conversation,
                   "ids": list(message_ids)})


def direct_request(user_id):
    """Начать личную переписку с этим человеком."""
    return encode({"type": "direct", "user": user_id})


def delete_request(message_id):
    return encode({"type": "delete", "id": message_id})


def search_request(query):
    return encode({"type": "search", "query": query})


def push_key_request():
    return encode({"type": "push_key"})


def push_key_message(key):
    return encode({"type": "push_key", "key": key})


def push_subscribe(subscription):
    return encode({"type": "push_subscribe", "subscription": subscription})


def react_request(message_id, emoji):
    return encode({"type": "react", "id": message_id, "emoji": emoji})


def reactions_message(conversation, message_id, summary):
    return encode({"type": "reactions", "conversation": conversation,
                   "id": message_id, "reactions": summary})


def typing_message(conversation):
    return encode({"type": "typing", "conversation": conversation})


def history_page(conversation, items, quotes, more, before=None, reactions=None):
    """Кусок истории переписки."""
    return encode({"type": "history", "conversation": conversation,
                   "items": items, "quotes": quotes, "more": more,
                   "before": before, "reactions": reactions or {}})


def ack_message(local, message_id, at):
    """Сервер принял сообщение: вот его настоящий номер и время."""
    return encode({"type": "ack", "local": local, "id": message_id, "at": at})


def receipts_message(items):
    """Состояние галочек: {номер сообщения: sent | delivered | read}."""
    return encode({"type": "receipts", "items": items})


def conversation_message(item):
    """Одна новая переписка — например, только что созданная группа."""
    return encode({"type": "conversation", "item": item})


def conversations_message(items):
    return encode({"type": "conversations", "items": items})


def people_message(items, online):
    return encode({"type": "people", "items": items, "online": online})


def presence_message(user_id, online):
    return encode({"type": "presence", "user": user_id, "online": online})


def deleted_message(conversation, message_id):
    return encode({"type": "deleted", "conversation": conversation,
                   "id": message_id})


def search_result(query, items):
    return encode({"type": "search", "query": query, "items": items})


# --- вход и профиль ---

def recover_request(login, code, password):
    """Сменить пароль по коду восстановления."""
    return encode({"type": "recover", "login": login, "code": code,
                   "password": password})


def register_message(login, password, name, invite=""):
    return encode({"type": "register", "login": login, "password": password,
                   "name": name, "invite": invite})


def login_message(login, password):
    return encode({"type": "login", "login": login, "password": password})


def auth_message(token):
    return encode({"type": "auth", "token": token})


def logout_message():
    return encode({"type": "logout"})


def profile_message(name, bio):
    return encode({"type": "profile", "name": name, "bio": bio})


def avatar_header(name, size):
    return encode({"type": "avatar", "name": name, "size": size})


def welcome_message(user, token, update=None, recovery=None):
    """Приветствие после входа.

    recovery появляется один раз — при регистрации и после смены пароля:
    показать код можно только тогда, дальше на сервере лежит лишь его хеш.
    """
    payload = {"type": "welcome", "user": user, "token": token}
    if update:
        payload["update"] = update
    if recovery:
        payload["recovery"] = recovery
    return encode(payload)


def update_request():
    return encode({"type": "update"})


def update_header(version, size):
    return encode({"type": "update_blob", "version": version, "size": size})


def _trouble(kind, text, code, args):
    """Кадр с ошибкой.

    Текст остаётся русским — его поймёт и старый клиент. Новый смотрит
    на код и подставляет свой перевод.
    """
    frame = {"type": kind, "text": text}
    if code:
        frame["code"] = code
        if args:
            frame["args"] = args
    return encode(frame)


def authfail_message(text, code=None, **args):
    return _trouble("authfail", text, code, args)


def profile_message_result(user):
    return encode({"type": "profile", "user": user})


# --- то, что отправляет сервер ---

def system_message(text):
    return encode({"type": "system", "text": text})


def error_message(text, code=None, **args):
    return _trouble("error", text, code, args)


def blob_header(media_id, kind, name):
    return encode({"type": "blob", "id": media_id, "kind": kind, "name": name})


def human_size(size):
    """Размер файла человеческими словами: 1.4 МБ."""
    if size < 1024:
        return f"{size} Б"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} КБ"
    return f"{size / (1024 * 1024):.1f} МБ"
