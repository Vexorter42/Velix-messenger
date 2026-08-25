"""Вложения, сложенные на диск, чтобы не качать их заново.

Картинки не меняются: у каждой свой неповторимый номер, и если она уже
однажды пришла, спрашивать её у сервера незачем. Без этого каждое
открытие переписки с двумя десятками фотографий — это мегабайты по сети
и несколько секунд ожидания, причём при каждом запуске программы.

Кэш живёт рядом с настройками, знает свой предел и, переполнившись,
выбрасывает то, к чему дольше всего не обращались.
"""

import os
import time
from pathlib import Path

import store

# Больше этого одно вложение в кэш не кладём: смысла держать на диске
# второй раз то же самое видео нет, а место оно займёт заметное
BIGGEST_ITEM = 50 * 1024 * 1024

# Сколько кэшу позволено занимать всего
LIMIT = 500 * 1024 * 1024


def cache_dir():
    """Где лежит кэш. VELIX_CACHE пригождается проверкам и переносу."""
    свой = os.environ.get("VELIX_CACHE")
    return Path(свой) if свой else store.config_dir() / "media"


def _path(media_id):
    # Номер вложения приходит с сервера, поэтому в имя файла пускаем только
    # то, из чего его и делают: шестнадцатеричные цифры
    clean = "".join(letter for letter in str(media_id or "")
                    if letter in "0123456789abcdefABCDEF")[:64]
    return cache_dir() / clean if clean else None


def get(media_id):
    """Содержимое вложения, если оно уже лежит на диске."""
    path = _path(media_id)
    if path is None or not path.exists():
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None

    # Отмечаем, что вложение сегодня пригодилось: выбрасывать будем не его
    try:
        os.utime(path, None)
    except OSError:
        pass
    return data


def put(media_id, data):
    """Кладёт вложение на диск. Слишком большое пропускаем."""
    path = _path(media_id)
    if path is None or not data or len(data) > BIGGEST_ITEM:
        return False

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Пишем через временное имя: оборвись запись на середине, в кэше
        # осталась бы половина картинки, и она бы такой и показывалась
        temporary = path.with_suffix(".part")
        temporary.write_bytes(data)
        temporary.replace(path)
    except OSError:
        return False

    prune()
    return True


def _thumb_path(media_id):
    path = _path(media_id)
    return None if path is None else path.with_name(path.name + ".th")


def get_thumb(media_id):
    """Уменьшенная копия картинки, если её уже готовили."""
    path = _thumb_path(media_id)
    if path is None or not path.exists():
        return None
    try:
        data = path.read_bytes()
        os.utime(path, None)
        return data
    except OSError:
        return None


def put_thumb(media_id, data):
    """Запоминает уменьшенную копию: разбирать снимок заново дорого."""
    path = _thumb_path(media_id)
    if path is None or not data:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return True
    except OSError:
        return False


def size():
    """Сколько кэш занимает сейчас."""
    folder = cache_dir()
    if not folder.exists():
        return 0
    return sum(item.stat().st_size for item in folder.iterdir() if item.is_file())


def prune(limit=LIMIT):
    """Ужимает кэш до предела, начиная с самого залежавшегося."""
    folder = cache_dir()
    if not folder.exists():
        return 0

    items = []
    total = 0
    for item in folder.iterdir():
        if not item.is_file():
            continue
        try:
            stat = item.stat()
        except OSError:
            continue
        items.append((stat.st_mtime, stat.st_size, item))
        total += stat.st_size

    if total <= limit:
        return 0

    выброшено = 0
    for _, item_size, item in sorted(items):
        if total <= limit:
            break
        try:
            item.unlink()
        except OSError:
            continue
        total -= item_size
        выброшено += 1
    return выброшено


def forget():
    """Стирает кэш целиком — например, когда человек выходит из аккаунта."""
    folder = cache_dir()
    if not folder.exists():
        return
    for item in folder.iterdir():
        if item.is_file():
            try:
                item.unlink()
            except OSError:
                pass


def touched_recently(media_id, within=1.0):
    """Служебное: давно ли к вложению обращались. Нужно проверкам."""
    path = _path(media_id)
    if path is None or not path.exists():
        return False
    return time.time() - path.stat().st_mtime <= within
