import asyncio
import os
import ssl
import time
import uuid
from http import HTTPStatus
from pathlib import Path

import websockets
from websockets.http11 import Response
from websockets.datastructures import Headers, MultipleValuesError

import accounts
import i18n
import media
import protocol
import push
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
# Панель управления доступна одному человеку: по умолчанию тому, кто
# завёл сервер и зарегистрировался первым.
ADMIN_LOGIN = os.environ.get("VELIX_ADMIN", "").strip().lower()

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
        await websocket.send(protocol.error_message("Обновление недоступно.", "update_unavailable"))
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
        await websocket.send(protocol.error_message("Написать самому себе не выйдет.", "self_dm"))
        return
    if await storage.user_by_id(other) is None:
        await websocket.send(protocol.error_message("Такого человека нет.", "no_such_person"))
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
            "Удалить можно только своё сообщение.", "not_your_message"))
        return

    print(f"[Лог]: {user['name']} удалил сообщение {message_id}")
    frame = protocol.deleted_message(conversation, message_id)
    await websocket.send(frame)
    await send_to_conversation(conversation, frame, websocket)


async def notify_absent(conversation_id, sender_id, title, body):
    """Шлёт уведомления тем участникам, кого сейчас нет в сети.

    Тем, кто сидит в чате, ничего не отправляем: они и так всё видят.
    """
    if not push.available():
        return

    if conversation_id == storage.GENERAL_ID:
        people = [person["id"] for person in await storage.people()]
    else:
        people = await storage.members(conversation_id)

    absent = [user_id for user_id in people
              if user_id != sender_id and user_id not in online]
    for user_id, subscription in await storage.pushes_for(absent):
        # Язык подписчик прислал вместе с подпиской: на телефоне
        # уведомление должно быть на том же языке, что и приложение
        spoken = i18n.in_language(subscription.get("language"), body)
        problem = await asyncio.to_thread(push.send, subscription, title, spoken,
                                          str(conversation_id))
        if problem == "gone":
            await storage.drop_push(subscription.get("endpoint"))
        elif problem:
            print(f"[Сервер]: Уведомление не ушло: {problem}")


async def handle_push_subscribe(websocket, user, message):
    """Запоминает подписку телефона."""
    subscription = message.get("subscription")
    if not isinstance(subscription, dict):
        return
    if await storage.add_push(user["id"], subscription):
        print(f"[Сервер]: {user['login']} подписался на уведомления")


async def handle_react(websocket, user, message):
    """Ставит или снимает реакцию на сообщение."""
    try:
        message_id = int(message.get("id"))
    except (TypeError, ValueError):
        return

    emoji = str(message.get("emoji") or "").strip()[:8]
    if not emoji:
        return

    result = await storage.toggle_reaction(message_id, user["id"], emoji)
    if result is None:
        await websocket.send(protocol.error_message("Сообщение не найдено.", "message_not_found"))
        return

    conversation, summary = result
    if not await storage.is_member(conversation, user["id"]):
        await websocket.send(protocol.error_message("Эта переписка вам недоступна.", "no_access"))
        return

    frame = protocol.reactions_message(conversation, message_id, summary)
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
    """Отправляет кадр участникам переписки, где бы они ни сидели.

    Возвращает тех, до кого кадр дошёл: по ним считаются галочки о доставке.
    """
    recipients = []
    reached = []
    for user_id in await storage.members(conversation_id):
        for client in online.get(user_id, ()):  # у человека может быть и окно, и телефон
            if client is not sender:
                recipients.append(client)
                reached.append(user_id)
    await deliver_to(recipients, frame, payload)
    return sorted(set(reached))


async def note_delivery(message_id, author_id, reached):
    """Отмечает доставку и, если галочка изменилась, говорит об этом автору."""
    for user_id in reached:
        await storage.mark_receipts(user_id, [message_id])
    await tell_author(author_id, [message_id])


async def tell_author(author_id, message_ids):
    """Сообщает автору, что стало с его сообщениями."""
    clients = list(online.get(author_id, ()))
    if not clients or not message_ids:
        return
    state = await storage.receipt_state(message_ids)
    if not state:
        return
    await deliver_to(clients, protocol.receipts_message(
        {str(key): value for key, value in state.items()}))


async def handle_read(websocket, user, message):
    """Человек открыл переписку и увидел эти сообщения."""
    conversation = conversation_of(message)
    if not await storage.is_member(conversation, user["id"]):
        return

    try:
        ids = [int(item) for item in (message.get("ids") or [])][:200]
    except (TypeError, ValueError):
        return

    changed = await storage.mark_receipts(user["id"], ids, read=True)
    if not changed:
        return

    # Разбираем по авторам: каждому — только про его сообщения
    by_author = {}
    for item in await storage.messages_by_ids(changed):
        by_author.setdefault(item["user"], []).append(item["id"])
    for author_id, message_ids in by_author.items():
        await tell_author(author_id, message_ids)


async def handle_pin(websocket, user, message):
    """Закрепляет сообщение переписки — или снимает закрепление."""
    conversation = conversation_of(message)
    if not await allowed(websocket, user, conversation):
        return

    message_id = reply_of({"reply_to": message.get("id")})
    item = None
    if message_id is not None:
        item = await storage.message(message_id)
        if item is None or await storage.conversation_of(message_id) != conversation:
            await websocket.send(protocol.error_message(
                "Сообщение не найдено.", "message_not_found"))
            return

    await storage.pin(conversation, message_id)
    frame = protocol.pinned_message(conversation, item)
    await websocket.send(frame)
    await send_to_conversation(conversation, frame, websocket)


async def handle_forward(websocket, user, message):
    """Пересылает сообщение в другую переписку."""
    target = conversation_of(message)
    source_id = reply_of({"reply_to": message.get("id")})
    if source_id is None:
        return

    item = await storage.message(source_id)
    if item is None:
        await websocket.send(protocol.error_message(
            "Сообщение не найдено.", "message_not_found"))
        return

    # Взять можно только оттуда, где состоишь, и положить только туда же
    where = await storage.conversation_of(source_id)
    if not await storage.is_member(where, user["id"]):
        await websocket.send(protocol.error_message(
            "Эта переписка вам недоступна.", "no_access"))
        return
    if not await allowed(websocket, user, target):
        return

    author = item.get("nick") or ""
    if item.get("kind", "text") == "text":
        message_id, created_at = await storage.save_message(
            user["id"], user["name"], item.get("text", ""), target, None, author)
        payload = {"type": "text", "id": message_id, "nick": user["name"],
                   "text": item.get("text", ""), "at": created_at,
                   "conversation": target, "user": user["id"],
                   "forwarded": author}
    else:
        message_id, created_at = await storage.save_existing_media(
            user["id"], user["name"], item["kind"], item.get("name", ""),
            item.get("size") or 0, item.get("media"), target, author)
        payload = {"type": "media", "id": message_id, "media": item.get("media"),
                   "nick": user["name"], "kind": item["kind"],
                   "name": item.get("name", ""), "size": item.get("size") or 0,
                   "at": created_at, "conversation": target, "user": user["id"],
                   "forwarded": author}

    if user.get("avatar"):
        payload["avatar"] = user["avatar"]

    print(f"[Лог]: {user['name']} переслал сообщение {source_id} в {target}")
    await websocket.send(protocol.encode(payload))
    reached = await send_to_conversation(target, protocol.encode(payload), websocket)
    await note_delivery(message_id, user["id"], reached)


async def handle_members(websocket, user, message):
    """Дописывает людей в группу, где состоит сам зовущий."""
    conversation = conversation_of(message)
    if not await allowed(websocket, user, conversation):
        return

    item = await storage.conversation(conversation)
    if (item or {}).get("kind") != "group":
        await websocket.send(protocol.error_message(
            "Позвать можно только в группу.", "group_only"))
        return

    try:
        chosen = {int(one) for one in (message.get("members") or [])}
    except (TypeError, ValueError):
        return

    known = {person["id"] for person in await storage.people()}
    already = set(await storage.members(conversation))
    fresh = (chosen & known) - already
    if not fresh:
        return

    await storage.add_members(conversation, fresh)
    print(f"[Лог]: {user['name']} позвал в «{item['title']}» ещё {len(fresh)}")

    # Новым — сама переписка, старым — обновлённый список участников
    for member in fresh:
        for client in online.get(member, ()):
            await client.send(protocol.conversation_message(item))
    await send_people_to(await storage.members(conversation))


async def send_people_to(user_ids):
    """Освежает список участников у перечисленных людей."""
    people = await storage.people()
    frame = protocol.people_message(people, sorted(online))
    await deliver_to([client for user_id in user_ids
                      for client in online.get(user_id, ())], frame)


async def handle_group(websocket, user, message):
    """Заводит группу и показывает её всем, кого туда позвали."""
    title = str(message.get("title") or "").strip()[:60]
    if not title:
        await websocket.send(protocol.error_message(
            "У группы должно быть название.", "group_needs_title"))
        return

    try:
        chosen = {int(item) for item in (message.get("members") or [])}
    except (TypeError, ValueError):
        return

    known = {person["id"] for person in await storage.people()}
    members = (chosen & known) | {user["id"]}
    if len(members) < 2:
        await websocket.send(protocol.error_message(
            "Выберите, кого позвать в группу.", "group_needs_members"))
        return

    conversation = await storage.create_group(title, members, user["id"])
    print(f"[Лог]: {user['name']} завёл группу «{title}» на {len(members)} человек")

    item = await storage.conversation(conversation)
    for member in members:
        for client in online.get(member, ()):
            await client.send(protocol.conversation_message(item))
    await send_history(websocket, user, conversation)


async def allowed(websocket, user, conversation_id):
    """Проверяет, что человеку вообще есть дело до этой переписки."""
    if await storage.is_member(conversation_id, user["id"]):
        return True
    await websocket.send(protocol.error_message("Эта переписка вам недоступна.", "no_access"))
    return False


async def send_history(websocket, user, conversation_id, before=None):
    """Отдаёт кусок истории вместе с цитатами, на которые она ссылается."""
    items = await storage.messages(conversation_id, before=before)
    quotes = await storage.quoted({item["reply_to"] for item in items
                                   if item.get("reply_to")})
    marks = await storage.reactions([item["id"] for item in items])

    # Раз человек забрал историю, чужие сообщения из неё до него дошли
    delivered = await storage.mark_receipts(user["id"],
                                            [item["id"] for item in items])
    for author_id in {item["user"] for item in items
                      if item["id"] in set(delivered)}:
        await tell_author(author_id, [item["id"] for item in items
                                      if item["user"] == author_id])

    # Свои сообщения показываем с галочками
    states = await storage.receipt_state([item["id"] for item in items
                                          if item.get("user") == user["id"]])
    for item in items:
        if item["id"] in states:
            item["state"] = states[item["id"]]
    # Если пришло ровно столько, сколько просили, вероятно есть и постарше
    more = len(items) >= storage.HISTORY_LIMIT
    await websocket.send(protocol.history_page(
        conversation_id, items, {str(key): value for key, value in quotes.items()},
        more, before, {str(key): value for key, value in marks.items()}))


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
        code, args = accounts.code_for(problem)
        await websocket.send(protocol.authfail_message(problem, code, **args))
        return None, None

    invite = accounts.clean_invite(message.get("invite"))
    if not OPEN_REGISTRATION:
        if not invite:
            await websocket.send(protocol.authfail_message(
                "Нужен код приглашения — попросите его у того, кто держит чат.",
                "invite_required"))
            return None, None
        if not await storage.invite_exists(invite):
            await websocket.send(protocol.authfail_message(
                "Код приглашения не подошёл: его либо нет, либо им уже воспользовались.",
                "invite_bad"))
            return None, None

    name = accounts.clean_name(message.get("name"), login)
    # scrypt считается заметное время и грузит процессор — уводим в поток,
    # чтобы чат не замирал на время регистрации
    password_hash = await asyncio.to_thread(accounts.hash_password, password)

    # Код восстановления показываем один раз: на сервере остаётся только хеш
    recovery = accounts.new_recovery()
    recovery_hash = await asyncio.to_thread(accounts.hash_password, recovery)

    user = await storage.create_user(login, password_hash, name, recovery_hash)
    if user is None:
        await websocket.send(protocol.authfail_message("Такой логин уже занят.", "login_taken"))
        return None, None

    if not OPEN_REGISTRATION and not await storage.take_invite(invite, user["id"]):
        # Кто-то успел воспользоваться кодом, пока считался хеш пароля
        await websocket.send(protocol.authfail_message(
            "Код приглашения только что заняли. Попросите новый.", "invite_taken"))
        return None, None

    print(f"[Сервер]: Зарегистрирован {login} ({name})")
    return user, recovery


async def handle_recover(websocket, message):
    """Меняет пароль по коду восстановления и выдаёт новый код."""
    login = str(message.get("login") or "").strip()
    code = accounts.clean_invite(message.get("code"))
    password = message.get("password")

    waiting = locked_for(login.lower())
    if waiting:
        await websocket.send(protocol.authfail_message(
            f"Слишком много неудачных попыток. Попробуйте через {waiting // 60 + 1} мин.",
            "locked_out", minutes=waiting // 60 + 1))
        return None, None

    problem = accounts.check_password(password)
    if problem:
        name, args = accounts.code_for(problem)
        await websocket.send(protocol.authfail_message(problem, name, **args))
        return None, None

    user_id, stored = await storage.recovery_row(login)
    if user_id is None or not stored:
        # Считаем хеш вхолостую: по времени ответа не должно быть заметно,
        # есть такой логин или нет
        await asyncio.to_thread(accounts.hash_password, str(password or ""))
        note_failure(login.lower())
        await websocket.send(protocol.authfail_message(
            "Код восстановления не подошёл.", "recovery_bad"))
        return None, None

    if not await asyncio.to_thread(accounts.verify_password, code, stored):
        note_failure(login.lower())
        await websocket.send(protocol.authfail_message(
            "Код восстановления не подошёл.", "recovery_bad"))
        return None, None

    note_success(login.lower())
    password_hash = await asyncio.to_thread(accounts.hash_password, password)
    await storage.set_password(user_id, password_hash)

    # Код одноразовый: взамен использованного сразу выдаём новый
    fresh = accounts.new_recovery()
    await storage.set_recovery(user_id,
                               await asyncio.to_thread(accounts.hash_password, fresh))

    print(f"[Сервер]: {login} сменил пароль по коду восстановления")
    return await storage.user_by_id(user_id), fresh


async def handle_login(websocket, message):
    """Пускает по логину и паролю."""
    login = str(message.get("login") or "").strip()
    password = message.get("password")

    waiting = locked_for(login.lower())
    if waiting:
        await websocket.send(protocol.authfail_message(
            f"Слишком много неудачных попыток. Попробуйте через {waiting // 60 + 1} мин.",
            "locked_out", minutes=waiting // 60 + 1))
        return None

    user, password_hash = await storage.user_with_hash(login)
    if user is None:
        # Пароль всё равно считаем, чтобы по времени ответа нельзя было
        # понять, существует такой логин или нет
        await asyncio.to_thread(accounts.hash_password, str(password or ""))
        note_failure(login.lower())
        await websocket.send(protocol.authfail_message("Неверный логин или пароль.", "bad_credentials"))
        return None

    correct = await asyncio.to_thread(accounts.verify_password,
                                      str(password or ""), password_hash)
    if not correct:
        note_failure(login.lower())
        print(f"[Сервер]: Неудачный вход в {login}")
        await websocket.send(protocol.authfail_message("Неверный логин или пароль.", "bad_credentials"))
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
                "Клиент устарел: обновите Velix, этот сервер говорит на новом языке.",
                "client_too_old"))
            continue

        kind = message.get("type")
        user = None
        recovery = None

        if kind == "register":
            user, recovery = await handle_register(websocket, message)
        elif kind == "recover":
            user, recovery = await handle_recover(websocket, message)
        elif kind == "login":
            user = await handle_login(websocket, message)
        elif kind == "auth":
            user = await storage.user_by_token(str(message.get("token") or ""))
            if user is None:
                await websocket.send(protocol.authfail_message(
                    "Сессия больше не действует, войдите заново.", "session_expired"))
        else:
            # Сюда попадает и клиент прошлой версии: он сразу шлёт сообщение,
            # не зная, что теперь нужно войти
            await websocket.send(protocol.authfail_message(
                "Сначала нужно войти в аккаунт. Если у вас старая версия"
                " Velix — обновите её.", "login_required"))

        if user is None:
            continue

        if kind == "auth":
            token = message["token"]
        else:
            token = accounts.new_token()
            await storage.remember_token(token, user["id"])

        return user, token, recovery


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

    # Отправителю — номер сообщения: по нему он поставит галочки, а потом
    # сможет его удалить или отметить реакцией, не перезаходя в чат
    await websocket.send(protocol.ack_message(message.get("local"), message_id,
                                              created_at))
    reached = await send_to_conversation(conversation, protocol.encode(payload),
                                         websocket)
    await note_delivery(message_id, user["id"], reached)
    await notify_absent(conversation, user["id"], user["name"], text[:120])


async def handle_media(websocket, user, message):
    """Принимает описание вложения и следом за ним двоичный кадр."""
    name = clean_filename(message.get("name"))
    conversation = conversation_of(message)
    reply_to = reply_of(message)

    payload = await websocket.recv()
    if not isinstance(payload, (bytes, bytearray)):
        await websocket.send(protocol.error_message("Ожидались данные файла.", "expected_file"))
        return

    if len(payload) > protocol.MAX_MEDIA_SIZE:
        await websocket.send(protocol.error_message(
            f"Файл больше {protocol.human_size(protocol.MAX_MEDIA_SIZE)}, не приняли.",
            "file_too_big",
            limit=protocol.human_size(protocol.MAX_MEDIA_SIZE)))
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
    await websocket.send(protocol.ack_message(message.get("local"), message_id,
                                              created_at, media_id))
    reached = await send_to_conversation(conversation, protocol.encode(frame),
                                         websocket)
    await note_delivery(message_id, user["id"], reached)
    await notify_absent(conversation, user["id"], user["name"],
                        "прислал вложение")


async def handle_fetch(websocket, message):
    """Отдаёт содержимое вложения по запросу клиента."""
    found = await storage.media_bytes(str(message.get("id") or ""))
    if found is None:
        await websocket.send(protocol.error_message("Вложение не найдено.", "attachment_missing"))
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


async def is_admin(user):
    """Хозяин чата: либо названный в VELIX_ADMIN, либо первый по счёту."""
    if ADMIN_LOGIN:
        return str(user.get("login", "")).lower() == ADMIN_LOGIN
    return user.get("id") == 1


async def handle_delete_group(websocket, user, message):
    """Удаляет группу — по просьбе её создателя или хозяина чата."""
    conversation = conversation_of(message)
    item = await storage.conversation(conversation)
    if item is None or item.get("kind") != "group":
        await websocket.send(protocol.error_message(
            "Удалить можно только группу.", "group_only_delete"))
        return

    if item.get("owner") != user["id"] and not await is_admin(user):
        await websocket.send(protocol.error_message(
            "Удалить группу может тот, кто её завёл.", "not_group_owner"))
        return

    members = await storage.members(conversation)
    await storage.delete_conversation(conversation)
    print(f"[Лог]: {user['name']} удалил группу «{item.get('title')}»")

    # Каждому участнику — обновлённый список переписок
    for member in members:
        for client in online.get(member, ()):
            await client.send(protocol.conversations_message(
                await storage.conversations(member)))


async def handle_admin(websocket, user, message):
    """Панель управления: сводка, удаление людей и переписок."""
    if not await is_admin(user):
        await websocket.send(protocol.error_message(
            "Панель доступна только хозяину чата.", "not_admin"))
        return

    what = str(message.get("what") or "stats")

    if what == "drop_user":
        try:
            victim = int(message.get("user"))
        except (TypeError, ValueError):
            return
        if victim == user["id"]:
            await websocket.send(protocol.error_message(
                "Себя удалить нельзя.", "admin_self"))
            return

        # Выкидываем из сети: его токены только что перестали существовать
        for client in list(online.get(victim, ())):
            await client.close()
        login = await storage.delete_user(victim)
        print(f"[Лог]: {user['name']} удалил учётную запись {login}")

    elif what == "drop_room":
        try:
            room = int(message.get("conversation"))
        except (TypeError, ValueError):
            return
        members = await storage.members(room)
        await storage.delete_conversation(room)
        print(f"[Лог]: {user['name']} удалил переписку {room}")
        for member in members:
            for client in online.get(member, ()):
                await client.send(protocol.conversations_message(
                    await storage.conversations(member)))

    await websocket.send(protocol.admin_message(await storage.stats()))


async def handle_avatar(websocket, user, message):
    """Принимает картинку профиля следующим кадром."""
    name = clean_filename(message.get("name"))

    payload = await websocket.recv()
    if not isinstance(payload, (bytes, bytearray)):
        await websocket.send(protocol.error_message("Ожидались данные картинки.", "expected_image"))
        return

    if len(payload) > MAX_AVATAR_SIZE:
        await websocket.send(protocol.error_message(
            f"Аватарка больше {protocol.human_size(MAX_AVATAR_SIZE)}, не приняли.",
            "avatar_too_big", limit=protocol.human_size(MAX_AVATAR_SIZE)))
        return

    # Аватарку сжимаем той же дорогой, что и обычные картинки
    name, packed = await asyncio.to_thread(media.compress, "image", name, bytes(payload))

    # С номером переписки это фото группы, без него — своё
    if message.get("conversation"):
        conversation = conversation_of(message)
        if not await allowed(websocket, user, conversation):
            return

        item = await storage.conversation(conversation)
        if (item or {}).get("kind") != "group":
            await websocket.send(protocol.error_message(
                "Фото ставится только группе.", "group_only_photo"))
            return

        await storage.set_conversation_avatar(conversation, uuid.uuid4().hex,
                                              name, packed)
        fresh = await storage.conversation(conversation)
        print(f"[Сервер]: {user['login']} сменил фото группы «{fresh.get('title')}»")

        frame = protocol.conversation_message(fresh)
        await websocket.send(frame)
        await send_to_conversation(conversation, frame, websocket)
        return

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
        user, token, recovery = await authenticate(websocket)
    except websockets.exceptions.ConnectionClosed:
        return

    try:
        await websocket.send(protocol.welcome_message(
            user, token, available_update(), recovery, await is_admin(user)))
        # Историю отдаём до регистрации в connected_clients, иначе новые
        # сообщения чата могли бы вклиниться в середину выгрузки.
        items = await storage.conversations(user["id"])
        await websocket.send(protocol.conversations_message(items))
        # Общего чата больше нет: открываем первую переписку человека,
        # а если его никуда не звали — ленту показывать нечем
        if items:
            await send_history(websocket, user, items[0]["id"])
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
            elif kind == "group":
                await handle_group(websocket, user, message)
            elif kind == "members":
                await handle_members(websocket, user, message)
            elif kind == "sync":
                # Клиент вернулся к жизни и просит свежие списки
                await websocket.send(protocol.conversations_message(
                    await storage.conversations(user["id"])))
                await send_people(websocket)
            elif kind == "delete_group":
                await handle_delete_group(websocket, user, message)
            elif kind == "admin":
                await handle_admin(websocket, user, message)
            elif kind == "read":
                await handle_read(websocket, user, message)
            elif kind == "pin":
                await handle_pin(websocket, user, message)
            elif kind == "forward":
                await handle_forward(websocket, user, message)
            elif kind == "delete":
                await handle_delete(websocket, user, message)
            elif kind == "react":
                await handle_react(websocket, user, message)
            elif kind == "push_key":
                await websocket.send(protocol.push_key_message(push.public_key()))
            elif kind == "push_subscribe":
                await handle_push_subscribe(websocket, user, message)
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
        # Телефон в кармане отвечает на пинг не сразу: со стандартными
        # двадцатью секундами связь рвалась на ровном месте, и клиенты
        # переподключались по кругу.
        async with websockets.serve(chat_handler, HOST, PORT,
                                    process_request=check_host,
                                    max_size=protocol.MAX_FRAME_SIZE,
                                    ping_interval=30, ping_timeout=90,
                                    close_timeout=10,
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
            print("Уведомления на телефон: включены" if push.public_key()
                  else "Уведомления на телефон: недоступны")
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
