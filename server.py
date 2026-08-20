import asyncio
import os
from http import HTTPStatus
from pathlib import Path

import websockets
from websockets.datastructures import MultipleValuesError

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

# Аватарка — картинка, и большая тут ни к чему
MAX_AVATAR_SIZE = 4 * 1024 * 1024

# Множество для хранения всех активных подключений
connected_clients = set()


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
        return None

    try:
        host_header = request.headers.get("Host", "")
    except MultipleValuesError:
        # Два заголовка Host сразу — нормальный клиент так не делает
        host_header = ""

    if hostname_of(host_header).lower() in ALLOWED_HOSTS:
        return None

    print(f"[Сервер]: Отклонено подключение по имени '{host_header}'")
    return connection.respond(HTTPStatus.FORBIDDEN, "Здесь ничего нет.\n")


def clean_filename(value):
    """Оставляет от присланного имени только сам файл, без путей."""
    name = Path(str(value or "файл")).name.strip()[:120]
    return name or "файл"


async def broadcast(frame, sender, payload=None):
    """Рассылает кадр всем подключенным клиентам, кроме отправителя."""
    # Собираем получателей заранее: пока идёт await, кто-то может подключиться
    # или отвалиться, и множество изменится прямо посреди итерации.
    recipients = [client for client in connected_clients if client is not sender]
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


# --------------------------------------------------------------------- вход

async def handle_register(websocket, message):
    """Заводит учётную запись и сразу впускает в чат."""
    login = str(message.get("login") or "").strip()
    password = message.get("password")

    problem = accounts.check_login(login) or accounts.check_password(password)
    if problem:
        await websocket.send(protocol.authfail_message(problem))
        return None

    name = accounts.clean_name(message.get("name"), login)
    # scrypt считается заметное время и грузит процессор — уводим в поток,
    # чтобы чат не замирал на время регистрации
    password_hash = await asyncio.to_thread(accounts.hash_password, password)

    user = await storage.create_user(login, password_hash, name)
    if user is None:
        await websocket.send(protocol.authfail_message("Такой логин уже занят."))
        return None

    print(f"[Сервер]: Зарегистрирован {login} ({name})")
    return user


async def handle_login(websocket, message):
    """Пускает по логину и паролю."""
    login = str(message.get("login") or "").strip()
    password = message.get("password")

    user, password_hash = await storage.user_with_hash(login)
    if user is None:
        # Пароль всё равно считаем, чтобы по времени ответа нельзя было
        # понять, существует такой логин или нет
        await asyncio.to_thread(accounts.hash_password, str(password or ""))
        await websocket.send(protocol.authfail_message("Неверный логин или пароль."))
        return None

    correct = await asyncio.to_thread(accounts.verify_password,
                                      str(password or ""), password_hash)
    if not correct:
        await websocket.send(protocol.authfail_message("Неверный логин или пароль."))
        return None

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

async def handle_text(websocket, user, message):
    text = str(message.get("text") or "").strip()[:MAX_TEXT]
    if not text:
        return

    print(f"[Лог]: {user['name']}: {text}")
    created_at = await storage.save_message(user["id"], user["name"], text)

    payload = {"type": "text", "nick": user["name"], "text": text, "at": created_at}
    if user.get("avatar"):
        payload["avatar"] = user["avatar"]
    await broadcast(protocol.encode(payload), websocket)


async def handle_media(websocket, user, message):
    """Принимает описание вложения и следом за ним двоичный кадр."""
    name = clean_filename(message.get("name"))

    payload = await websocket.recv()
    if not isinstance(payload, (bytes, bytearray)):
        await websocket.send(protocol.error_message("Ожидались данные файла."))
        return

    if len(payload) > protocol.MAX_MEDIA_SIZE:
        await websocket.send(protocol.error_message(
            f"Файл больше {protocol.human_size(protocol.MAX_MEDIA_SIZE)}, не приняли."))
        return

    # Вид вложения определяем сами, присланному на слово не верим
    kind = protocol.kind_of(name)

    # Картинку ужимаем до отправки в хранилище: на диске малины лежит уже
    # сжатая, оригинал не нужен. Работа с картинкой упирается в процессор,
    # поэтому уводим её в отдельный поток.
    original_size = len(payload)
    name, packed = await asyncio.to_thread(media.compress, kind, name, bytes(payload))

    media_id, created_at = await storage.save_media(user["id"], user["name"],
                                                    kind, name, packed)
    print(f"[Лог]: {user['name']} прислал {kind} '{name}' "
          f"({protocol.human_size(len(packed))}, {media.describe(original_size, len(packed))})")

    # Остальным уходит только описание — содержимое они запросят сами,
    # когда дойдут до отрисовки
    frame = {"type": "media", "id": media_id, "nick": user["name"], "kind": kind,
             "name": name, "size": len(packed), "at": created_at}
    if user.get("avatar"):
        frame["avatar"] = user["avatar"]
    await broadcast(protocol.encode(frame), websocket)


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
        await websocket.send(protocol.welcome_message(user, token))
        # Историю отдаём до регистрации в connected_clients, иначе новые
        # сообщения чата могли бы вклиниться в середину выгрузки.
        await websocket.send(protocol.history_message(await storage.last_messages()))
    except websockets.exceptions.ConnectionClosed:
        return

    connected_clients.add(websocket)
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
            elif kind == "profile":
                await handle_profile(websocket, user, message)
            elif kind == "avatar":
                await handle_avatar(websocket, user, message)
            elif kind == "logout":
                await storage.forget_token(token)
                await websocket.close()
                return

    except websockets.exceptions.ConnectionClosed:
        # Срабатывает, если клиент закрыл окно или пропал интернет
        pass
    finally:
        connected_clients.discard(websocket)
        print(f"[Сервер]: Вышел {user['login']}. Активных: {len(connected_clients)}")


async def main():
    await storage.init()
    print(f"[Сервер]: База лежит в {storage.DB_PATH}")
    print(f"[Сервер]: Вложения складываются в {storage.MEDIA_DIR}")

    try:
        # Запускаем WebSocket-сервер. max_size поднят: в кадр должен помещаться
        # файл целиком, а по умолчанию библиотека рвёт связь уже на мегабайте.
        async with websockets.serve(chat_handler, HOST, PORT,
                                    process_request=check_host,
                                    max_size=protocol.MAX_FRAME_SIZE):
            print("--- Сервер Velix запущен ---")
            print(f"Слушаем подключения на порту {PORT} (например, ws://localhost:{PORT})...")
            if ALLOWED_HOSTS:
                print(f"Пускаем только по именам: {', '.join(sorted(ALLOWED_HOSTS))}")

            # future() работает как бесконечный цикл, не давая серверу завершить работу
            await asyncio.Future()
    finally:
        await storage.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Сервер]: Работа завершена.")
