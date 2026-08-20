import asyncio
import os
from http import HTTPStatus
from pathlib import Path

import websockets
from websockets.datastructures import MultipleValuesError

import protocol
import storage

# Адрес и порт, на которых сервер принимает подключения.
# None — все сетевые интерфейсы и IPv4, и IPv6; иначе подключиться смог бы
# только клиент с этой же машины. Именно None, а не "0.0.0.0": с "0.0.0.0"
# сервер слушает только IPv4, и клиент, набравший "localhost", сначала
# пробует IPv6-адрес ::1 и ждёт таймаута несколько секунд.
HOST = None
PORT = 8765

# Имена, по которым разрешено подключаться, через запятую в переменной
# окружения VELIX_ALLOWED_HOSTS. Пустое значение — пускать всех.
ALLOWED_HOSTS = {
    name.strip().lower()
    for name in os.environ.get("VELIX_ALLOWED_HOSTS", "").split(",")
    if name.strip()
}

MAX_NICKNAME = 32
MAX_TEXT = 4000

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


def clean_nickname(value):
    """Приводит присланный никнейм к виду, пригодному для показа."""
    nickname = str(value or "").strip().replace("\n", " ")[:MAX_NICKNAME]
    return nickname or "Аноним"


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


async def handle_text(websocket, message):
    nickname = clean_nickname(message.get("nick"))
    text = str(message.get("text") or "").strip()[:MAX_TEXT]
    if not text:
        return

    print(f"[Лог]: {nickname}: {text}")
    created_at = await storage.save_message(nickname, text)
    await broadcast(protocol.encode({"type": "text", "nick": nickname,
                                     "text": text, "at": created_at}), websocket)


async def handle_media(websocket, message):
    """Принимает описание вложения и следом за ним двоичный кадр."""
    nickname = clean_nickname(message.get("nick"))
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
    media_id, created_at = await storage.save_media(nickname, kind, name, bytes(payload))
    print(f"[Лог]: {nickname} прислал {kind} '{name}' "
          f"({protocol.human_size(len(payload))})")

    # Остальным уходит только описание — содержимое они запросят сами,
    # когда дойдут до отрисовки
    await broadcast(protocol.encode({"type": "media", "id": media_id, "nick": nickname,
                                     "kind": kind, "name": name, "size": len(payload),
                                     "at": created_at}), websocket)


async def handle_fetch(websocket, message):
    """Отдаёт содержимое вложения по запросу клиента."""
    found = await storage.media_bytes(str(message.get("id") or ""))
    if found is None:
        await websocket.send(protocol.error_message("Вложение не найдено."))
        return

    kind, name, data = found
    await websocket.send(protocol.blob_header(message["id"], kind, name))
    await websocket.send(data)


async def chat_handler(websocket):
    """Функция обрабатывает каждое новое подключение."""
    try:
        # Историю отдаём до регистрации в connected_clients, иначе новые
        # сообщения чата могли бы вклиниться в середину выгрузки.
        await websocket.send(protocol.history_message(await storage.last_messages()))
    except websockets.exceptions.ConnectionClosed:
        return

    # Регистрируем нового клиента
    connected_clients.add(websocket)
    print(f"[Сервер]: Новое подключение! Активных пользователей: {len(connected_clients)}")

    try:
        while True:
            message = protocol.decode(await websocket.recv())
            if message is None:
                await websocket.send(protocol.error_message(
                    "Клиент устарел: обновите Velix, этот сервер говорит на новом языке."))
                continue

            kind = message.get("type")
            if kind == "text":
                await handle_text(websocket, message)
            elif kind == "media":
                await handle_media(websocket, message)
            elif kind == "fetch":
                await handle_fetch(websocket, message)

    except websockets.exceptions.ConnectionClosed:
        # Срабатывает, если клиент закрыл окно или пропал интернет
        pass
    finally:
        # При отключении клиента удаляем его из множества
        connected_clients.discard(websocket)
        print(f"[Сервер]: Клиент отключился. Активных пользователей: {len(connected_clients)}")


async def main():
    await storage.init()
    print(f"[Сервер]: История сообщений хранится в {storage.DB_PATH}")
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
