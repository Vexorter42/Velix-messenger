"""Окно: под названием переписки видно, что с собеседником.

В сети ли он, печатает ли и когда заходил в последний раз.
"""

import asyncio
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(os.environ.get("VELIX_SRC")
            or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import store  # noqa: E402

уголок = Path(tempfile.mkdtemp(prefix="velix-seen-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import protocol  # noqa: E402
import gui  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


PORT = int(os.environ.get("VELIX_TESTPORT", "8817"))
ВЧЕРА = (datetime.now().astimezone() - timedelta(days=1)).replace(
    hour=21, minute=15, second=0, microsecond=0)
приказы = asyncio.Queue()


async def притворщик(websocket):
    await websocket.recv()
    await websocket.send(protocol.welcome_message(
        {"id": 1, "login": "gosha", "name": "Гоша"}, "токен"))
    await websocket.send(protocol.conversations_message([
        {"id": 7, "kind": "group", "title": "Поход", "members": [1, 2, 3]},
        {"id": 3, "kind": "direct", "title": "Руслан", "user": 2},
    ]))
    await websocket.send(protocol.people_message([
        {"id": 1, "login": "gosha", "name": "Гоша", "seen": None},
        {"id": 2, "login": "ruslan", "name": "Руслан",
         "seen": ВЧЕРА.isoformat()},
    ], []))

    async def слушать():
        while True:
            кадр = protocol.decode(await websocket.recv())
            if кадр and кадр.get("type") == "open":
                await websocket.send(protocol.history_page(
                    кадр["conversation"], [], {}, False))

    asyncio.create_task(слушать())
    while True:
        кадр = await приказы.get()
        await websocket.send(кадр)


def сервер():
    async def run():
        import websockets
        async with websockets.serve(притворщик, "localhost", PORT,
                                    max_size=protocol.MAX_FRAME_SIZE):
            await asyncio.Future()

    global петля
    петля = asyncio.new_event_loop()
    asyncio.set_event_loop(петля)
    петля.run_until_complete(run())


петля = None
threading.Thread(target=сервер, daemon=True).start()
time.sleep(1.2)

app = gui.VelixApp()
app.attributes("-topmost", True)
steps = []


def step(function):
    steps.append(function)
    return function


def сказать(кадр):
    """Просит притворщика отправить кадр окну. Ответ придёт к следующему шагу."""
    asyncio.run_coroutine_threadsafe(приказы.put(кадр), петля).result(5)


def подпись():
    return app.header_subtitle.cget("text")


@step
def sign_in():
    app._show_form(register=False)
    app.server_entry.insert(0, f"localhost:{PORT}")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "secret123")
    app._on_primary()


@step
def open_direct():
    app._open(3, force=True)


@step
def check_offline():
    видно = подпись()
    print("      подпись:", видно)
    check("seen-gui-yesterday", "вчера в 21:15" in видно, видно)


@step
def they_come_online():
    сказать(protocol.presence_message(2, True))


@step
def check_online():
    check("seen-gui-online", подпись().endswith("в сети"), подпись())


@step
def they_type():
    сказать(protocol.encode({"type": "typing", "conversation": 3,
                             "user": 2, "nick": "Руслан"}))


@step
def check_typing():
    check("seen-gui-typing", "печатает" in подпись(), подпись())


@step
def check_typing_fades():
    # Прошло больше трёх секунд — подпись вернулась к присутствию
    check("seen-gui-typing-fades", подпись().endswith("в сети"), подпись())


@step
def they_leave():
    сказать(protocol.presence_message(
        2, False, datetime.now().astimezone().isoformat()))


@step
def check_just_now():
    check("seen-gui-just-now", "только что" in подпись(), подпись())


@step
def group_shows_members():
    app._open(7, force=True)


@step
def check_group():
    check("seen-gui-group-members", "участников: 3" in подпись(), подпись())


@step
def finish():
    app.destroy()


delay = 900
паузы = {"check_typing": 3600}      # «печатает» гаснет через три секунды
for function in steps:
    app.after(delay, function)
    delay += паузы.get(function.__name__, 1800)

app.mainloop()

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
