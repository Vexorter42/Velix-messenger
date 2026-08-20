"""Консольный клиент Velix.

Показывает переписку в терминале. Вложения тут не нарисуешь, поэтому картинки
и видео отображаются строкой с именем и размером — смотреть их нужно в оконном
клиенте.
"""

import asyncio
import queue
import sys
import threading
from datetime import datetime

import websockets

import protocol

PORT = 8765

KIND_LABEL = {"image": "фото", "gif": "гифку", "video": "видео", "file": "файл"}


def build_uri(address):
    """Собирает адрес подключения из того, что ввёл пользователь.

    Порт можно дописать через двоеточие — "vexorter.duckdns.org:9000".
    Без него подставляется стандартный 8765.
    """
    address = address.strip() or "localhost"

    if address.startswith("["):  # IPv6 в скобках: [::1] или [::1]:8765
        host, _, rest = address.partition("]")
        if rest.startswith(":") and rest[1:].isdigit():
            return f"ws://{address}"
        return f"ws://{host}]:{PORT}"

    if address.count(":") == 1:
        host, _, port = address.partition(":")
        if port.isdigit() and host:
            return f"ws://{host}:{port}"

    if address.count(":") > 1:  # голый IPv6 без порта
        return f"ws://[{address}]:{PORT}"

    return f"ws://{address}:{PORT}"


def format_item(item):
    """Собирает строку для показа в терминале."""
    try:
        stamp = datetime.fromisoformat(item["at"]).astimezone().strftime("%H:%M")
    except (KeyError, TypeError, ValueError):
        stamp = datetime.now().strftime("%H:%M")

    nickname = item.get("nick", "?")
    if item.get("kind", "text") == "text":
        return f"[{stamp}] {nickname}: {item.get('text', '')}"

    label = KIND_LABEL.get(item.get("kind"), "файл")
    size = protocol.human_size(item.get("size") or 0)
    return (f"[{stamp}] {nickname} прислал {label}: {item.get('name', 'без имени')}"
            f" ({size}) — открыть можно в оконном клиенте")


def show(line):
    """Печатает строку, не затирая набранное приглашение ввода."""
    print(f"\r{line}\n> ", end="", flush=True)


def stdin_reader(loop, outgoing):
    """Читает строки с клавиатуры в отдельном потоке и кладёт их в очередь.

    Поток помечен как daemon, поэтому процесс сможет завершиться, даже если
    input() в этот момент ждёт Enter.
    """
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            line = None

        try:
            loop.call_soon_threadsafe(outgoing.put_nowait, line)
        except RuntimeError:
            # Цикл событий уже закрыт — клиент завершается, читать больше некому.
            return

        if line is None:
            return


async def receive_messages(websocket, stop_event):
    """Фоновая задача для получения сообщений от сервера."""
    try:
        while True:
            message = protocol.decode(await websocket.recv())
            if message is None:
                continue

            kind = message.get("type")
            if kind == "history":
                items = message.get("items", [])
                if items:
                    show("--- последние сообщения ---")
                    for item in items:
                        show(format_item(item))
                    show("--- конец истории ---")
            elif kind in ("text", "media"):
                show(format_item(message))
            elif kind in ("system", "error"):
                show(f"[Система]: {message.get('text', '')}")
            elif kind == "blob":
                # Содержимое вложений консольному клиенту не нужно,
                # но кадр с данными идёт следом и его надо вычитать
                await websocket.recv()

    except websockets.exceptions.ConnectionClosed:
        print("\n[Система]: Соединение с сервером потеряно.")
    finally:
        # Из корутины нельзя просто выйти из процесса: вторая задача ждёт ввод
        # в другом потоке. Поэтому сообщаем main(), что пора завершаться.
        stop_event.set()


async def send_messages(websocket, nickname, outgoing, stop_event):
    """Задача для отправки на сервер того, что пользователь набрал."""
    try:
        while True:
            message = await outgoing.get()

            # None приходит, когда stdin закончился (Ctrl+D / конец файла)
            if message is None or message.lower() in ["/exit", "/quit"]:
                print("Выход из Velix...")
                return

            if message.strip():
                try:
                    await websocket.send(protocol.text_message(nickname, message))
                except websockets.exceptions.ConnectionClosed:
                    print("\n[Система]: Сообщение не отправлено, соединение закрыто.")
                    return
    finally:
        stop_event.set()


async def main():
    print("--- Добро пожаловать в Velix ---")

    # 1. Запрашиваем никнейм
    nickname = input("Введите ваш никнейм: ").strip()
    if not nickname:
        nickname = "Аноним"  # Если пользователь просто нажал Enter

    # 2. Запрашиваем адрес сервера
    server_ip = input("Адрес сервера, можно с портом (Enter — localhost): ").strip()
    if not server_ip:
        server_ip = "localhost"

    uri = build_uri(server_ip)
    print(f"Подключение к {uri}...")

    try:
        async with websockets.connect(uri, max_size=protocol.MAX_FRAME_SIZE) as websocket:
            print(f"[Система]: Успешно подключено как {nickname}! Можно писать сообщения. (для выхода введите /exit)\n")

            stop_event = asyncio.Event()
            outgoing = asyncio.Queue()

            # Ввод читаем в потоке-демоне, чтобы он не держал завершение клиента
            threading.Thread(
                target=stdin_reader,
                args=(asyncio.get_running_loop(), outgoing),
                daemon=True,
            ).start()

            receive_task = asyncio.create_task(receive_messages(websocket, stop_event))
            send_task = asyncio.create_task(send_messages(websocket, nickname, outgoing, stop_event))

            # Ждём, пока любая из задач не решит, что пора закругляться
            await stop_event.wait()

            for task in (receive_task, send_task):
                task.cancel()
            await asyncio.gather(receive_task, send_task, return_exceptions=True)

    except ConnectionRefusedError:
        print("\n[Ошибка]: Сервер недоступен. Проверьте, запущен ли он.")
    except OSError as error:
        # Неверный адрес, недоступная сеть и прочие сетевые проблемы
        print(f"\n[Ошибка]: Не удалось подключиться к {uri}: {error}")
    except websockets.exceptions.InvalidStatus as error:
        # Сервер может пускать только по определённому имени и отвечать 403
        if error.response.status_code == 403:
            print("\n[Ошибка]: Сервер не принимает подключение по этому адресу."
                  " Проверьте, что он введён точно.")
        else:
            print(f"\n[Ошибка]: Сервер ответил кодом {error.response.status_code}.")
    except websockets.exceptions.WebSocketException as error:
        print(f"\n[Ошибка]: Не удалось установить WebSocket-соединение: {error}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nВыход из Velix...")
