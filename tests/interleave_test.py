"""Окно: кадр, влезший между кусками вложения, не пропадает.

Та самая беда с боевого сервера. Пока клиент забирал два десятка
фотографий, между кусками одной из них приходила история переписки —
и окно считало её куском файла. История пропадала, переписка оставалась
пустой навсегда.
"""

import asyncio
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import harness

REPO = Path(os.environ.get("VELIX_SRC")
            or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import store  # noqa: E402

уголок = Path(tempfile.mkdtemp(prefix="velix-inter-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import protocol  # noqa: E402
import gui  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


PORT = int(os.environ.get("VELIX_TESTPORT", "8809"))
КУСОК = b"\x89PNG" + bytes(400)
состояние = {}


async def притворщик(websocket):
    """Сервер, который нарочно вклинивает историю между кусками файла."""
    await websocket.recv()          # вход
    await websocket.send(protocol.welcome_message(
        {"id": 1, "login": "gosha", "name": "Гоша"}, "токен"))
    await websocket.send(protocol.conversations_message([
        {"id": 7, "kind": "group", "title": "Поход"},
        {"id": 3, "kind": "direct", "title": "Руслан", "user": 2},
    ]))

    while True:
        сырой = await websocket.recv()
        кадр = protocol.decode(сырой)
        if кадр is None:
            continue

        if кадр.get("type") == "open" and кадр.get("conversation") == 7:
            # Отдаём переписку с одним вложением
            await websocket.send(protocol.history_page(7, [
                {"id": 1, "nick": "Лена", "kind": "image", "media": "abc123",
                 "name": "photo.png", "size": len(КУСОК) * 2,
                 "at": "2026-08-26T09:00:00+00:00", "user": 2},
            ], {}, False))

        elif кадр.get("type") == "fetch":
            состояние["вложение"] = кадр["id"]

        elif кадр.get("type") == "open" and кадр.get("conversation") == 3:
            # Вот она, беда: клиент ждёт историю, а она приходит между
            # кусками вложения — ровно так было на боевом сервере
            состояние["просили"] = состояние.get("просили", 0) + 1
            await websocket.send(protocol.blob_header(
                состояние.get("вложение", "abc123"), "image", "photo.png",
                len(КУСОК) * 2, 2))
            await websocket.send(КУСОК)
            состояние["влезли"] = True
            await websocket.send(protocol.history_page(3, [
                {"id": 2, "nick": "Руслан", "kind": "text", "text": "привет",
                 "at": "2026-08-26T09:01:00+00:00", "user": 2},
            ], {}, False))
            await websocket.send(КУСОК)


def сервер():
    async def run():
        import websockets
        async with websockets.serve(притворщик, "localhost", PORT,
                                    max_size=protocol.MAX_FRAME_SIZE):
            await asyncio.Future()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run())


threading.Thread(target=сервер, daemon=True).start()
harness.дождаться(8809)

app = gui.VelixApp()
harness.тихое_окно(app)
steps = []


def step(function):
    steps.append(function)
    return function


@step
def sign_in():
    app._show_form(register=False)
    app.server_entry.insert(0, f"localhost:{PORT}")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "secret123")
    app._on_primary()


@step
def open_direct():
    check("inter-group-first", app.conversation == 7, app.conversation)
    app._open(3, force=True)


@step
def check_direct():
    check("inter-wedged", состояние.get("влезли", False),
          "притворщик не успел вклиниться")
    тексты = [one.get("text") for one in app.loaded_items]
    check("inter-history-kept", "привет" in тексты, тексты)
    check("inter-not-waiting", app.waiting_for is None, app.waiting_for)


@step
def finish():
    app.destroy()


delay = 900
for function in steps:
    app.after(delay, function)
    delay += 2600

app.mainloop()

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
