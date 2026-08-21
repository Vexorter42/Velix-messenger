import asyncio
import os
import ssl
import time
from http import HTTPStatus
from pathlib import Path

import websockets
from websockets.http11 import Response
from websockets.datastructures import Headers, MultipleValuesError

import accounts
import media
import protocol
import storage

# Адрес и порт, на которых сервер принимает подключения.
# None — все сетевые интерфейсы и IPv4, и IPv6; иначе подключиться смог бы
# только клиент с этой же машины. Именно None, а не "0.0.0.0": с "0.0.0.0"
# сервер слушает только IPv4, и клиент, набравший "localhost", сначала
# пробует IPv6-адрес ::1 и ждёт таймаута несколько секунд.
HOST = None

# Порт можно сменить переменной VELIX_PORT: на одной машине так уживаются
# боевой сервер и тестовый, каждый со своей базой
PORT = int(os.environ.get("VELIX_PORT") or 8765)

# Имена, по которым разрешено подключаться, через запятую в переменной
# окружения VELIX_ALLOWED_HOSTS. Пустое значение — пускать всех.
ALLOWED_HOSTS = {
    name.strip().lower()
    for name in os.environ.get("VELIX_ALLOWED_HOSTS", "").split(",")
    if name.strip()
}

MAX_TEXT = 4000

# Регистрация по коду приглашения. VELIX_OPEN_REGISTRATION=1 открывает её
# всем подряд — по умолчанию в чат попадают только по приглашению.
OPEN_REGISTRATION = os.environ.get("VELIX_OPEN_REGISTRATION") == "1"

# Защита от перебора пароля: сколько промахов подряд терпим и насколько
# запираем дверь после этого
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 300

# Логин -> (число промахов, время последнего)
_failures = {}

# Сертификат и ключ для wss://. Если оба заданы и файлы на месте, сервер
# поднимается по TLS: пароли и переписка перестают ходить открытым текстом.
CERT_FILE = os.environ.get("VELIX_CERT")
KEY_FILE = os.environ.get("VELIX_KEY")

# Свежая сборка для кнопки «Обновить» в клиенте: рядом кладутся Velix.exe и
# version.txt с номером версии. Каталог можно увести переменной VELIX_UPDATES.
UPDATES_DIR = Path(os.environ.get("VELIX_UPDATES")
                   or Path(__file__).with_name("updates"))

# Аватарка — картинка, и большая тут ни к чему
MAX_AVATAR_SIZE = 4 * 1024 * 1024

# Множество для хранения всех активных подключений
connected_clients = set()

# Кто сейчас в сети: номер пользователя -> его подключения. У одного человека
# их может быть несколько — окно на компьютере и вкладка на телефоне.
online = {}


def register_online(user_id, websocket):
    """Добавляет подключение. True, если человек только что появился в сети."""
    sockets = online.setdefault(user_id, set())
    sockets.add(websocket)
    return len(sockets) == 1


def forget_online(user_id, websocket):
    """Убирает подключение. True, если человек ушёл совсем."""
    sockets = online.get(user_id)
    if not sockets:
        return False
    sockets.discard(websocket)
    if sockets:
        return False
    online.pop(user_id, None)
    return True


# Каталог с мобильным веб-клиентом: те же страницы отдаются с того же порта,
# что и чат, поэтому отдельный веб-сервер и второй проброс порта не нужны.
WEB_DIR = Path(os.environ.get("VELIX_WEB") or Path(__file__).with_name("web"))

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
}


def serve_file(connection, path):
    """Отдаёт страницу веб-клиента обычным HTTP-ответом."""
    relative = path.split("?", 1)[0].lstrip("/") or "index.html"

    # Никаких прогулок по файловой системе: только то, что лежит в web/
    target = (WEB_DIR / relative).resolve()
    try:
        target.relative_to(WEB_DIR.resolve())
    except ValueError:
        return connection.respond(HTTPStatus.FORBIDDEN, "Нельзя.")

    if not target.is_file():
        return connection.respond(HTTPStatus.NOT_FOUND, "Не найдено.")

    body = target.read_bytes()
    headers = Headers({
        "Content-Type": CONTENT_TYPES.get(target.suffix.lower(),
                                          "application/octet-stream"),
        "Content-Length": str(len(body)),
        # Страницу не кешируем, чтобы обновления доезжали сразу
        "Cache-Control": "no-cache",
    })
    return Response(200, "OK", headers, body)


def serve_if_browser(connection, request):
    """Браузеру отдаём страницу, клиенту чата — пропускаем рукопожатие."""
    try:
        upgrade = request.headers.get("Upgrade", "")
    except MultipleValuesError:
        upgrade = ""

    if upgrade.lower() == "websocket":
        return None  # это подключение к чату, дальше разбирается библиотека

    if WEB_DIR.is_dir():
        return serve_file(connection, request.path)
    return connection.respond(HTTPStatus.NOT_FOUND, "Здесь только чат.")


def hostname_of(host_header):
    """Отрезает порт от заголовка Host: "velix.example.org:8765" -> "velix.example.org"."""
    if host_header.startswith("["):  # IPv6 в скобках
        return host_header.partition("]")[0] + "]"
    if host_header.count(":") == 1:
        return host_header.partition(":")[0]
    return host_header


def check_host(connection, request):
    """Отклоняет подключение, если клиент пришёл не по разрешённому имени.

    Имя берётся из заголовка Host, который клиент отправляет при рукопожатии.
    Защитой это не является: заголовок ничего не стоит подделать вручную.
    Но случайный сканер, который стучится по голому IP-адресу, до чата не
    доберётся — он не знает имени.
    """
    if not ALLOWED_HOSTS:
        return serve_if_browser(connection, request)

    try:
        host_header = request.headers.get("Host", "")
    except MultipleValuesError:
        # Два заголовка Host сразу — нормальный клиент так не делает
        host_header = ""

    if hostname_of(host_header).lower() in ALLOWED_HOSTS:
        return serve_if_browser(connection, request)

    print(f"[Сервер]: Отклонено подключение по имени '{host_header}'")
    return connection.respond(HTTPStatus.FORBIDDEN, "Здесь ничего нет.\n")


def available_update():
    """Что за версия лежит в каталоге обновлений. None, если ничего нет."""
    build = UPDATES_DIR / "Velix.exe"
    marker = UPDATES_DIR / "version.txt"
    if not build.exists() or not marker.exists():
        return None
    try:
        version = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not version:
        return None
    return {"version": version, "size": build.stat().st_size}


async def handle_update(websocket):
    """Отдаёт клиенту свежую сборку."""
    update = available_update()
    if update is None:
        await websocket.send(protocol.error_message("Обновление недоступно."))
        return

    data = await asyncio.to_thread((UPDATES_DIR / "Velix.exe").read_bytes)
    print(f"[Сервер]: Отдаём обновление {update['version']} "
          f"({protocol.human_size(len(data))})")
    await websocket.send(protocol.update_header(update["version"], len(data)))
    await websocket.send(data)


async def handle_open(websocket, user, message):
    """Открывает переписку или подгружает то, что старше."""
    conversation = conversation_of(message)
    if not await allowed(websocket, user, conversation):
        return
    before = reply_of({"reply_to": message.get("before")})
    await send_history(websocket, user, conversation, before)


async def handle_direct(websocket, user, message):
    """Заводит личную переписку с выбранным человеком."""
    try:
        other = int(message.get("user"))
    except (TypeError, ValueError):
        return

    if other == user["id"]:
        await websocket.send(protocol.error_message("Написать самому себе не выйдет."))
        return
    if await storage.user_by_id(other) is None:
        await websocket.send(protocol.error_message("Такого человека нет."))
        return

    conversation = await storage.direct_id(user["id"], other)
    await websocket.send(protocol.conversations_message(
        await storage.conversations(user["id"])))
    await send_history(websocket, user, conversation)

    # Собеседнику тоже обновляем список: у него появилась новая переписка
    for client in online.get(other, ()):
        await client.send(protocol.conversations_message(
            await storage.conversations(other)))


async def handle_delete(websocket, user, message):
    """Прячет своё сообщение у всех."""
    try:
        message_id = int(message.get("id"))
    except (TypeError, ValueError):
        return

    conversation = await storage.delete_message(message_id, user["id"])
    if conversation is None:
        await websocket.send(protocol.error_message(
            "Удалить можно только своё сообщение."))
        return

    print(f"[Лог]: {user['name']} удалил сообщение {message_id}")
    frame = protocol.deleted_message(conversation, message_id)
    await websocket.send(frame)
    await send_to_conversation(conversation, frame, websocket)


async def handle_search(websocket, user, message):
    """Ищет по переписке."""
    query = str(message.get("query") or "").strip()
    if len(query) < 2:
        await websocket.send(protocol.search_result(query, []))
        return
    found = await storage.search(user["id"], query)
    await websocket.send(protocol.search_result(query, found))


async def handle_typing(websocket, user, message):
    """Сообщает собеседникам, что человек набирает текст."""
    conversation = conversation_of(message)
    if not await storage.is_member(conversation, user["id"]):
        return
    await send_to_conversation(conversation, protocol.encode(
        {"type": "typing", "conversation": conversation,
         "user": user["id"], "nick": user["name"]}), websocket)


async def send_people(websocket):
    """Список участников с отметкой, кто сейчас в сети."""
    await websocket.send(protocol.people_message(await storage.people(),
                                                 sorted(online)))


async def announce_presence(user_id, is_online, sender=None):
    """Всем остальным — что человек появился или ушёл.

    Самому себе не шлём: он и так знает, что подключился, а лишний кадр
    только путался бы под ногами у истории.
    """
    frame = protocol.presence_message(user_id, is_online)
    await deliver_to([client for client in connected_clients if client is not sender],
                     frame)


def clean_filename(value):
    """Оставляет от присланного имени только сам файл, без путей."""
    name = Path(str(value or "файл")).name.strip()[:120]
    return name or "файл"


async def deliver_to(recipients, frame, payload=None):
    """Отправляет кадр списку подключений, не спотыкаясь на мёртвых."""
    if not recipients:
        return

    async def deliver(client):
        await client.send(frame)
        if payload is not None:
            await client.send(payload)

    # return_exceptions=True — один умерший клиент не должен обрывать
    # рассылку всем остальным.
    await asyncio.gather(*(deliver(client) for client in recipients),
                         return_exceptions=True)


async def broadcast(frame, sender, payload=None):
    """Рассылает кадр всем подключенным клиентам, кроме отправителя."""
    # Собираем получателей заранее: пока идёт await, кто-то может подключиться
    # или отвалиться, и множество изменится прямо посреди итерации.
    await deliver_to([client for client in connected_clients if client is not sender],
                     frame, payload)


async def send_to_conversation(conversation_id, frame, sender=None, payload=None):
    """Отправляет кадр участникам переписки.

    В общий чат уходит всем, в личную — только её двоим, где бы они ни сидели.
    """
    if conversation_id == storage.GENERAL_ID:
        await broadcast(frame, sender, payload)
        return

    recipients = []
    for user_id in await storage.members(conversation_id):
        for client in online.get(user_id, ()):  # у человека может быть и окно, и телефон
            if client is not sender:
                recipients.append(client)
    await deliver_to(recipients, frame, payload)


async def allowed(websocket, user, conversation_id):
    """Проверяет, что человеку вообще есть дело до этой переписки."""
    if await storage.is_member(conversation_id, user["id"]):
        return True
    await websocket.send(protocol.error_message("Эта переписка вам недоступна."))
    return False


async def send_history(websocket, user, conversation_id, before=None):
    """Отдаёт кусок истории вместе с цитатами, на которые она ссылается."""
    items = await storage.messages(conversation_id, before=before)
    quotes = await storage.quoted({item["reply_to"] for item in items
                                   if item.get("reply_to")})
    # Если пришло ровно столько, сколько просили, вероятно есть и постарше
    more = len(items) >= storage.HISTORY_LIMIT
    await websocket.send(protocol.history_page(
        conversation_id, items, {str(key): value for key, value in quotes.items()},
        more, before))


# --------------------------------------------------------------------- вход

def locked_for(login):
    """Сколько секунд ещё нельзя пробовать этот логин."""
    attempts, last = _failures.get(login, (0, 0.0))
    if attempts < MAX_ATTEMPTS:
        return 0
    left = LOCKOUT_SECONDS - (time.monotonic() - last)
    if left <= 0:
        _failures.pop(login, None)
        return 0
    return int(left) + 1


def note_failure(login):
    attempts, _ = _failures.get(login, (0, 0.0))
    _failures[login] = (attempts + 1, time.monotonic())


def note_success(login):
    _failures.pop(login, None)


async def handle_register(websocket, message):
    """Заводит учётную запись и сразу впускает в чат."""
    login = str(message.get("login") or "").strip()
    password = message.get("password")

    problem = accounts.check_login(login) or accounts.check_password(password)
    if problem:
        await websocket.send(protocol.authfail_message(problem))
        return None

    invite = accounts.clean_invite(message.get("invite"))
    if not OPEN_REGISTRATION:
        if not invite:
            await websocket.send(protocol.authfail_message(
                "Нужен код приглашения — попросите его у того, кто держит чат."))
            return None
        if not await storage.invite_exists(invite):
            await websocket.send(protocol.authfail_message(
                "Код приглашения не подошёл: его либо нет, либо им уже воспользовались."))
            return None

    name = accounts.clean_name(message.get("name"), login)
    # scrypt считается заметное время и грузит процессор — уводим в поток,
    # чтобы чат не замирал на время регистрации
    password_hash = await asyncio.to_thread(accounts.hash_password, password)

    user = await storage.create_user(login, password_hash, name)
    if user is None:
        await websocket.send(protocol.authfail_message("Такой логин уже занят."))
        return None

    if not OPEN_REGISTRATION and not await storage.take_invite(invite, user["id"]):
        # Кто-то успел воспользоваться кодом, пока считался хеш пароля
        await websocket.send(protocol.authfail_message(
            "Код приглашения только что заняли. Попросите новый."))
        return None

    print(f"[Сервер]: Зарегистрирован {login} ({name})")
    return user


async def handle_login(websocket, message):
    """Пускает по логину и паролю."""
    login = str(message.get("login") or "").strip()
    password = message.get("password")

    waiting = locked_for(login.lower())
    if waiting:
        await websocket.send(protocol.authfail_message(
            f"Слишком много неудачных попыток. Попробуйте через {waiting // 60 + 1} мин."))
        return None

    user, password_hash = await storage.user_with_hash(login)
    if user is None:
        # Пароль всё равно считаем, чтобы по времени ответа нельзя было
        # понять, существует такой логин или нет
        await asyncio.to_thread(accounts.hash_password, str(password or ""))
        note_failure(login.lower())
        await websocket.send(protocol.authfail_message("Неверный логин или пароль."))
        return None

    correct = await asyncio.to_thread(accounts.verify_password,
                                      str(password or ""), password_hash)
    if not correct:
        note_failure(login.lower())
        print(f"[Сервер]: Неудачный вход в {login}")
        await websocket.send(protocol.authfail_message("Неверный логин или пароль."))
        return None

    note_success(login.lower())
    return user


async def authenticate(websocket):
    """Ждёт вход и возвращает (профиль, токен) либо None, если не вышло."""
    while True:
        frame = await websocket.recv()
        message = protocol.decode(frame)

        if message is None:
            await websocket.send(protocol.error_message(
                "Клиент устарел: обновите Velix, этот сервер говорит на новом языке."))
            continue

        kind = message.get("type")
        user = None

        if kind == "register":
            user = await handle_register(websocket, message)
        elif kind == "login":
            user = await handle_login(websocket, message)
        elif kind == "auth":
            user = await storage.user_by_token(str(message.get("token") or ""))
            if user is None:
                await websocket.send(protocol.authfail_message(
                    "Сессия больше не действует, войдите заново."))
        else:
            # Сюда попадает и клиент прошлой версии: он сразу шлёт сообщение,
            # не зная, что теперь нужно войти
            await websocket.send(protocol.authfail_message(
                "Сначала нужно войти в аккаунт. Если у вас старая версия"
                " Velix — обновите её."))

        if user is None:
            continue

        if kind == "auth":
            token = message["token"]
        else:
            token = accounts.new_token()
            await storage.remember_token(token, user["id"])

        return user, token


# ---------------------------------------------------------------- сообщения

def conversation_of(message):
    try:
        return int(message.get("conversation") or storage.GENERAL_ID)
    except (TypeError, ValueError):
        return storage.GENERAL_ID


def reply_of(message):
    try:
        value = message.get("reply_to")
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


async def handle_text(websocket, user, message):
    text = str(message.get("text") or "").strip()[:MAX_TEXT]
    if not text:
        return

    conversation = conversation_of(message)
    if not await allowed(websocket, user, conversation):
        return

    reply_to = reply_of(message)
    message_id, created_at = await storage.save_message(
        user["id"], user["name"], text, conversation, reply_to)
    print(f"[Лог]: {user['name']} в переписку {conversation}: {text}")

    payload = {"type": "text", "id": message_id, "nick": user["name"], "text": text,
               "at": created_at, "conversation": conversation, "user": user["id"]}
    if reply_to:
        payload["reply_to"] = reply_to
    if user.get("avatar"):
        payload["avatar"] = user["avatar"]
    await send_to_conversation(conversation, protocol.encode(payload), websocket)


async def handle_media(websocket, user, message):
    """Принимает описание вложения и следом за ним двоичный кадр."""
    name = clean_filename(message.get("name"))
    conversation = conversation_of(message)
    reply_to = reply_of(message)

    payload = await websocket.recv()
    if not isinstance(payload, (bytes, bytearray)):
        await websocket.send(protocol.error_message("Ожидались данные файла."))
        return

    if len(payload) > protocol.MAX_MEDIA_SIZE:
        await websocket.send(protocol.error_message(
            f"Файл больше {protocol.human_size(protocol.MAX_MEDIA_SIZE)}, не приняли."))
        return

    if not await allowed(websocket, user, conversation):
        return

    # Вид вложения определяем сами, присланному на слово не верим
    kind = protocol.kind_of(name)

    # Картинку ужимаем до отправки в хранилище: на диске малины лежит уже
    # сжатая, оригинал не нужен. Работа с картинкой упирается в процессор,
    # поэтому уводим её в отдельный поток.
    original_size = len(payload)
    name, packed = await asyncio.to_thread(media.compress, kind, name, bytes(payload))

    message_id, media_id, created_at = await storage.save_media(
        user["id"], user["name"], kind, name, packed, conversation, reply_to)
    print(f"[Лог]: {user['name']} прислал {kind} '{name}' "
          f"({protocol.human_size(len(packed))}, {media.describe(original_size, len(packed))})")

    # Остальным уходит только описание — содержимое они запросят сами,
    # когда дойдут до отрисовки
    frame = {"type": "media", "id": message_id, "media": media_id,
             "nick": user["name"], "kind": kind, "name": name,
             "size": len(packed), "at": created_at, "conversation": conversation,
             "user": user["id"]}
    if reply_to:
        frame["reply_to"] = reply_to
    if user.get("avatar"):
        frame["avatar"] = user["avatar"]
    await send_to_conversation(conversation, protocol.encode(frame), websocket)


async def handle_fetch(websocket, message):
    """Отдаёт содержимое вложения по запросу клиента."""
    found = await storage.media_bytes(str(message.get("id") or ""))
    if found is None:
        await websocket.send(protocol.error_message("Вложение не найдено."))
        return

    kind, name, data = found
    await websocket.send(protocol.blob_header(message["id"], kind, name))
    await websocket.send(data)


async def handle_profile(websocket, user, message):
    """Сохраняет имя и рассказ о себе."""
    name = accounts.clean_name(message.get("name"), user["name"])
    bio = accounts.clean_bio(message.get("bio"))

    updated = await storage.update_profile(user["id"], name, bio)
    user.update(updated)
    print(f"[Сервер]: {user['login']} обновил профиль ({name})")
    await websocket.send(protocol.profile_message_result(updated))
    # Имя и фото видны в списке участников, поэтому обновляем его у всех
    await deliver_to(list(connected_clients), protocol.people_message(
        await storage.people(), sorted(online)))


async def handle_avatar(websocket, user, message):
    """Принимает картинку профиля следующим кадром."""
    name = clean_filename(message.get("name"))

    payload = await websocket.recv()
    if not isinstance(payload, (bytes, bytearray)):
        await websocket.send(protocol.error_message("Ожидались данные картинки."))
        return

    if len(payload) > MAX_AVATAR_SIZE:
        await websocket.send(protocol.error_message(
            f"Аватарка больше {protocol.human_size(MAX_AVATAR_SIZE)}, не приняли."))
        return

    # Аватарку сжимаем той же дорогой, что и обычные картинки
    name, packed = await asyncio.to_thread(media.compress, "image", name, bytes(payload))
    avatar_id = await storage.set_avatar(user["id"], name, packed)
    user["avatar"] = avatar_id

    print(f"[Сервер]: {user['login']} сменил аватарку "
          f"({protocol.human_size(len(packed))})")
    await websocket.send(protocol.profile_message_result(
        await storage.user_by_id(user["id"])))


# ------------------------------------------------------------- подключение

async def chat_handler(websocket):
    """Функция обрабатывает каждое новое подключение."""
    try:
        user, token = await authenticate(websocket)
    except websockets.exceptions.ConnectionClosed:
        return

    try:
        await websocket.send(protocol.welcome_message(user, token,
                                                      available_update()))
        # Историю отдаём до регистрации в connected_clients, иначе новые
        # сообщения чата могли бы вклиниться в середину выгрузки.
        await websocket.send(protocol.conversations_message(
            await storage.conversations(user["id"])))
        await send_history(websocket, user, storage.GENERAL_ID)
    except websockets.exceptions.ConnectionClosed:
        return

    connected_clients.add(websocket)
    appeared = register_online(user["id"], websocket)

    try:
        # Список участников отправляем уже после отметки в сети, иначе человек
        # не увидит в нём самого себя
        await send_people(websocket)
    except websockets.exceptions.ConnectionClosed:
        connected_clients.discard(websocket)
        forget_online(user["id"], websocket)
        return

    if appeared:
        # Рассылаем список целиком, а не только отметку о присутствии: человек
        # мог только что зарегистрироваться, и остальные его ещё не знают
        await deliver_to([client for client in connected_clients
                          if client is not websocket],
                         protocol.people_message(await storage.people(),
                                                 sorted(online)))
    print(f"[Сервер]: Вошёл {user['login']} ({user['name']}). "
          f"Активных: {len(connected_clients)}")

    try:
        while True:
            message = protocol.decode(await websocket.recv())
            if message is None:
                continue

            kind = message.get("type")
            if kind == "text":
                await handle_text(websocket, user, message)
            elif kind == "media":
                await handle_media(websocket, user, message)
            elif kind == "fetch":
                await handle_fetch(websocket, message)
            elif kind == "open":
                await handle_open(websocket, user, message)
            elif kind == "direct":
                await handle_direct(websocket, user, message)
            elif kind == "delete":
                await handle_delete(websocket, user, message)
            elif kind == "search":
                await handle_search(websocket, user, message)
            elif kind == "typing":
                await handle_typing(websocket, user, message)
            elif kind == "people":
                await send_people(websocket)
            elif kind == "profile":
                await handle_profile(websocket, user, message)
            elif kind == "avatar":
                await handle_avatar(websocket, user, message)
            elif kind == "update":
                await handle_update(websocket)
            elif kind == "logout":
                await storage.forget_token(token)
                await websocket.close()
                return

    except websockets.exceptions.ConnectionClosed:
        # Срабатывает, если клиент закрыл окно или пропал интернет
        pass
    finally:
        connected_clients.discard(websocket)
        if forget_online(user["id"], websocket):
            await announce_presence(user["id"], False)
        print(f"[Сервер]: Вышел {user['login']}. Активных: {len(connected_clients)}")


def build_ssl_context():
    """Готовит TLS, если указаны сертификат и ключ."""
    if not CERT_FILE or not KEY_FILE:
        return None

    certificate, key = Path(CERT_FILE), Path(KEY_FILE)
    if not certificate.exists() or not key.exists():
        print(f"[Сервер]: Сертификат не найден ({certificate}), поднимаюсь без шифрования")
        return None

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # Ниже TLS 1.2 не опускаемся: всё, что старше, давно дырявое
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, key)
    return context


async def main():
    await storage.init()
    print(f"[Сервер]: База лежит в {storage.DB_PATH}")
    print(f"[Сервер]: Вложения складываются в {storage.MEDIA_DIR}")

    context = build_ssl_context()

    try:
        # Запускаем WebSocket-сервер. max_size поднят: в кадр должен помещаться
        # файл целиком, а по умолчанию библиотека рвёт связь уже на мегабайте.
        async with websockets.serve(chat_handler, HOST, PORT,
                                    process_request=check_host,
                                    max_size=protocol.MAX_FRAME_SIZE,
                                    ssl=context):
            scheme = "wss" if context else "ws"
            print("--- Сервер Velix запущен ---")
            print(f"Слушаем подключения на порту {PORT} ({scheme}://)...")
            if context is None:
                print("ВНИМАНИЕ: шифрования нет, пароли идут открытым текстом.")
            if ALLOWED_HOSTS:
                print(f"Пускаем только по именам: {', '.join(sorted(ALLOWED_HOSTS))}")
            print("Регистрация: по коду приглашения" if not OPEN_REGISTRATION
                  else "ВНИМАНИЕ: регистрация открыта всем подряд")
            if WEB_DIR.is_dir():
                print(f"Веб-клиент раздаётся из {WEB_DIR}")
            update = available_update()
            print(f"Раздаём обновление {update['version']}" if update
                  else "Обновление для раздачи не найдено")

            # future() работает как бесконечный цикл, не давая серверу завершить работу
            await asyncio.Future()
    finally:
        await storage.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Сервер]: Работа завершена.")
