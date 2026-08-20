import asyncio
import threading
import websockets

PORT = 8765


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


def stdin_reader(loop, queue):
    """Читает строки с клавиатуры в отдельном потоке и кладёт их в очередь.

    Поток помечен как daemon, поэтому процесс сможет завершиться, даже если
    input() в этот момент ждёт Enter (иначе выход из клиента после обрыва
    связи повис бы до первого нажатия клавиши).
    """
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            line = None

        try:
            loop.call_soon_threadsafe(queue.put_nowait, line)
        except RuntimeError:
            # Цикл событий уже закрыт — клиент завершается, читать больше некому.
            return

        if line is None:
            return


async def receive_messages(websocket, stop_event):
    """Фоновая задача для получения сообщений от сервера."""
    try:
        async for message in websocket:
            # Выводим то, что пришло (никнейм уже внутри сообщения)
            print(f"\r{message}\n> ", end="", flush=True)
    except websockets.exceptions.ConnectionClosed:
        print("\n[Система]: Соединение с сервером потеряно.")
    finally:
        # Из корутины нельзя просто выйти из процесса: вторая задача ждёт ввод
        # в другом потоке. Поэтому сообщаем main(), что пора завершаться.
        stop_event.set()


async def send_messages(websocket, nickname, queue, stop_event):
    """Задача для отправки на сервер того, что пользователь набрал."""
    try:
        while True:
            message = await queue.get()

            # None приходит, когда stdin закончился (Ctrl+D / конец файла)
            if message is None or message.lower() in ["/exit", "/quit"]:
                print("Выход из Velix...")
                return

            if message.strip():
                # Прикрепляем никнейм к сообщению перед отправкой
                formatted_message = f"[{nickname}]: {message}"
                try:
                    await websocket.send(formatted_message)
                except websockets.exceptions.ConnectionClosed:
                    print("\n[Система]: Сообщение не отправлено, соединение закрыто.")
                    return
    finally:
        stop_event.set()


async def main():
    print("--- Добро пожаловать в Velix ---")

    # 1. Запрашиваем никнейм.
    # Квадратные скобки вырезаем: сообщение уходит на сервер как
    # "[ник]: текст", и скобка внутри ника сбивала бы разбор на сервере.
    nickname = input("Введите ваш никнейм: ").strip().replace("[", "").replace("]", "")
    if not nickname:
        nickname = "Аноним"  # Если пользователь просто нажал Enter

    # 2. Запрашиваем IP сервера
    server_ip = input("Адрес сервера, можно с портом (Enter — localhost): ").strip()
    if not server_ip:
        server_ip = "localhost"

    uri = build_uri(server_ip)
    print(f"Подключение к {uri}...")

    try:
        async with websockets.connect(uri) as websocket:
            print(f"[Система]: Успешно подключено как {nickname}! Можно писать сообщения. (для выхода введите /exit)\n")

            stop_event = asyncio.Event()
            queue = asyncio.Queue()

            # Ввод читаем в потоке-демоне, чтобы он не держал завершение клиента
            threading.Thread(
                target=stdin_reader,
                args=(asyncio.get_running_loop(), queue),
                daemon=True,
            ).start()

            receive_task = asyncio.create_task(receive_messages(websocket, stop_event))
            send_task = asyncio.create_task(send_messages(websocket, nickname, queue, stop_event))

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
