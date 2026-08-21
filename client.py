"""Консольный клиент Velix.

Показывает переписку в терминале. Вложения тут не нарисуешь, поэтому картинки
и видео отображаются строкой с именем и размером — смотреть их нужно в оконном
клиенте.
"""

import asyncio
import getpass
import ssl
import sys
import threading
from datetime import datetime

import websockets

import protocol

PORT = 8765

KIND_LABEL = {"image": "фото", "gif": "гифку", "video": "видео", "file": "файл"}


def host_and_port(address):
    """Разбирает то, что ввёл пользователь, на хост и порт.

    Порт можно дописать через двоеточие — "vexorter.duckdns.org:9000".
    Без него подставляется стандартный 8765.
    """
    address = address.strip() or "localhost"

    if address.startswith("["):  # IPv6 в скобках: [::1] или [::1]:8765
        host, _, rest = address.partition("]")
        if rest.startswith(":") and rest[1:].isdigit():
            return f"{host}]", rest[1:]
        return f"{host}]", str(PORT)

    if address.count(":") == 1:
        host, _, port = address.partition(":")
        if port.isdigit() and host:
            return host, port

    if address.count(":") > 1:  # голый IPv6 без порта
        return f"[{address}]", str(PORT)

    return address, str(PORT)


def connection_uris(address):
    """Адреса для попыток: сначала защищённый wss://, потом обычный ws://."""
    address = address.strip() or "localhost"

    for scheme in ("wss://", "ws://"):
        if address.lower().startswith(scheme):
            host, port = host_and_port(address[len(scheme):])
            return [f"{scheme}{host}:{port}"]

    host, port = host_and_port(address)
    return [f"wss://{host}:{port}", f"ws://{host}:{port}"]


def build_uri(address):
    """Один адрес — первый из списка попыток."""
    return connection_uris(address)[0]


async def open_connection(address):
    """Открывает соединение, пробуя адреса по очереди."""
    uris = connection_uris(address)
    problems = []
    for uri in uris:
        try:
            websocket = await websockets.connect(uri, max_size=protocol.MAX_FRAME_SIZE)
            if not uri.startswith("wss://"):
                print("[Система]: соединение без шифрования, переписку можно перехватить.")
            return websocket
        except (OSError, ssl.SSLError, websockets.exceptions.WebSocketException) as error:
            problems.append(error)

    # Ошибка защищённой попытки понятнее: она прямо говорит, что имя в
    # сертификате другое, а обычная спотыкается уже о невнятный ответ
    for error in problems:
        if isinstance(error, ssl.SSLCertVerificationError):
            raise error
    raise problems[-1]


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
            elif kind == "profile":
                show("[Система]: профиль обновлён")
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


def ask_password(prompt="Пароль: "):
    """Спрашивает пароль, не показывая его на экране.

    Когда ввод перенаправлен из файла или конвейера, getpass на Windows всё
    равно читает прямо с консоли и виснет насмерть — в этом случае читаем
    обычным input().
    """
    if sys.stdin is None or not sys.stdin.isatty():
        return input(prompt)
    return getpass.getpass(prompt)


async def sign_in(websocket, login, password, name, register, invite=""):
    """Входит в аккаунт. Возвращает профиль или None, если не пустили."""
    if register:
        await websocket.send(protocol.register_message(login, password, name, invite))
    else:
        await websocket.send(protocol.login_message(login, password))

    while True:
        answer = protocol.decode(await websocket.recv())
        if answer is None:
            continue
        if answer.get("type") == "welcome":
            return answer["user"]
        if answer.get("type") in ("authfail", "error"):
            print(f"\n[Ошибка]: {answer.get('text', 'не пустили в чат')}")
            return None


async def main():
    print("--- Добро пожаловать в Velix ---")

    server_ip = input("Адрес сервера, можно с портом (Enter — localhost): ").strip()
    if not server_ip:
        server_ip = "localhost"

    register = input("Создать новый аккаунт? (y/N): ").strip().lower() in ("y", "д", "да")
    login = input("Логин: ").strip()
    password = ask_password()
    name = input("Как вас зовут: ").strip() if register else ""
    invite = input("Код приглашения: ").strip() if register else ""

    uri = build_uri(server_ip)
    print(f"Подключение к {server_ip}...")

    try:
        async with await open_connection(server_ip) as websocket:
            user = await sign_in(websocket, login, password, name, register, invite)
            if user is None:
                return
            nickname = user.get("name", login)
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
    except ssl.SSLCertVerificationError:
        print("\n[Ошибка]: Сертификат сервера выписан на другое имя. "
              "Проверьте, правильно ли введён адрес.")
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
