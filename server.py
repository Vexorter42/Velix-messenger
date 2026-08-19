import asyncio
import re

import websockets

import storage

# Адрес и порт, на которых сервер принимает подключения.
# None — все сетевые интерфейсы и IPv4, и IPv6; иначе подключиться смог бы
# только клиент с этой же машины. Именно None, а не "0.0.0.0": с "0.0.0.0"
# сервер слушает только IPv4, и клиент, набравший "localhost", сначала
# пробует IPv6-адрес ::1 и ждёт таймаута несколько секунд.
HOST = None
PORT = 8765

# Клиент присылает сообщения в виде "[никнейм]: текст".
# Ленивая (.*?) группа для ника: так "]: " внутри самого текста сообщения
# не будет принято за границу никнейма.
MESSAGE_PATTERN = re.compile(r"^\[(.*?)\]: (.*)$", re.DOTALL)

# Множество для хранения всех активных подключений
connected_clients = set()


def parse_message(raw_message):
    """Разбирает "[ник]: текст" на никнейм и текст.

    Если формат не совпал (например, подключились не нашим клиентом),
    сохраняем строку целиком, а автора помечаем как неизвестного.
    """
    match = MESSAGE_PATTERN.match(raw_message)
    if match is None:
        return "?", raw_message
    return match.group(1), match.group(2)


async def send_history(websocket):
    """Отправляет новому клиенту последние сообщения из базы."""
    history = await storage.last_messages()
    if not history:
        return

    lines = ["[Система]: последние сообщения:"]
    lines += [storage.format_line(*row) for row in history]
    lines.append("[Система]: --- конец истории ---")

    # Отправляем историю одним сообщением, а не построчно: полсотни отдельных
    # отправок упираются в буфер клиента, и медленный или неотвечающий клиент
    # задерживает обработчик своего подключения на всё время таймаута.
    await websocket.send("\n".join(lines))


async def broadcast(message, sender):
    """Пересылает сообщение всем подключенным клиентам, кроме отправителя."""
    # Собираем получателей заранее: пока идёт await, кто-то может подключиться
    # или отвалиться, и множество изменится прямо посреди итерации.
    recipients = [client for client in connected_clients if client is not sender]
    if not recipients:
        return

    # return_exceptions=True — один умерший клиент не должен обрывать
    # рассылку всем остальным.
    await asyncio.gather(
        *(client.send(message) for client in recipients),
        return_exceptions=True,
    )


async def chat_handler(websocket):
    """Функция обрабатывает каждое новое подключение."""
    try:
        # Историю отдаём до регистрации в connected_clients, иначе новые
        # сообщения чата могли бы вклиниться в середину выгрузки.
        await send_history(websocket)
    except websockets.exceptions.ConnectionClosed:
        return

    # Регистрируем нового клиента
    connected_clients.add(websocket)
    print(f"[Сервер]: Новое подключение! Активных пользователей: {len(connected_clients)}")

    try:
        # Бесконечный цикл прослушивания сообщений от этого клиента
        async for raw_message in websocket:
            print(f"[Лог]: Получено сообщение -> '{raw_message}'")

            nickname, text = parse_message(raw_message)
            await storage.save_message(nickname, text)

            await broadcast(raw_message, websocket)

    except websockets.exceptions.ConnectionClosed:
        # Срабатывает, если клиент закрыл терминал или пропал интернет
        pass
    finally:
        # При отключении клиента удаляем его из множества
        connected_clients.discard(websocket)
        print(f"[Сервер]: Клиент отключился. Активных пользователей: {len(connected_clients)}")


async def main():
    await storage.init()
    print(f"[Сервер]: История сообщений хранится в {storage.DB_PATH}")

    try:
        # Запускаем WebSocket-сервер
        async with websockets.serve(chat_handler, HOST, PORT):
            print("--- Сервер Velix запущен ---")
            print(f"Слушаем подключения на порту {PORT} (например, ws://localhost:{PORT})...")

            # future() работает как бесконечный цикл, не давая серверу завершить работу
            await asyncio.Future()
    finally:
        await storage.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Сервер]: Работа завершена.")
