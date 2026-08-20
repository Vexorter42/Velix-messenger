"""Протокол Velix: как клиент и сервер разговаривают.

Каждое сообщение — это кадр с JSON. Файлы передаются двумя кадрами подряд:
сначала JSON с описанием, сразу за ним двоичный кадр с содержимым. Так
картинка не раздувается на треть, как было бы при base64.
"""

import json
from pathlib import Path

VERSION = 3

# Больше этого файлы не принимаем — и сокет не забьётся, и малина цела
MAX_MEDIA_SIZE = 25 * 1024 * 1024

# Запас поверх лимита: в кадр кроме файла попадают и служебные байты
MAX_FRAME_SIZE = MAX_MEDIA_SIZE + 1024 * 1024

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

def text_message(nickname, text):
    return encode({"type": "text", "nick": nickname, "text": text})


def media_header(nickname, kind, name, size):
    return encode({"type": "media", "nick": nickname, "kind": kind,
                   "name": name, "size": size})


def fetch_request(media_id):
    return encode({"type": "fetch", "id": media_id})


# --- вход и профиль ---

def register_message(login, password, name):
    return encode({"type": "register", "login": login, "password": password,
                   "name": name})


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


def welcome_message(user, token):
    return encode({"type": "welcome", "user": user, "token": token})


def authfail_message(text):
    return encode({"type": "authfail", "text": text})


def profile_message_result(user):
    return encode({"type": "profile", "user": user})


# --- то, что отправляет сервер ---

def system_message(text):
    return encode({"type": "system", "text": text})


def error_message(text):
    return encode({"type": "error", "text": text})


def history_message(items):
    return encode({"type": "history", "items": items})


def blob_header(media_id, kind, name):
    return encode({"type": "blob", "id": media_id, "kind": kind, "name": name})


def human_size(size):
    """Размер файла человеческими словами: 1.4 МБ."""
    if size < 1024:
        return f"{size} Б"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} КБ"
    return f"{size / (1024 * 1024):.1f} МБ"
