"""Окно: переписка читается без связи — из сохранённого на диске."""

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(os.environ.get("VELIX_SRC")
            or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import store  # noqa: E402

уголок = Path(tempfile.mkdtemp(prefix="velix-offline-"))
store.CONFIG_PATH = уголок / "velix.json"
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import localcache  # noqa: E402

# Сохранённое должно лежать рядом с настройками проверки, а не в настоящем
# профиле: однажды проверка уже записала свою переписку хозяину
check_isolated = localcache.cache_dir()
import protocol  # noqa: E402
import gui  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


PORT = int(os.environ.get("VELIX_TESTPORT", "8843"))
СЕРВЕР = f"localhost:{PORT}"
ЛЕНТА = [
    {"id": 1, "nick": "Лена", "kind": "text", "text": "первое сохранённое",
     "at": "2026-08-27T09:00:00+00:00", "user": 2},
    {"id": 2, "nick": "Гоша", "kind": "text", "text": "второе сохранённое",
     "at": "2026-08-27T09:01:00+00:00", "user": 1},
]

# Настройки как после прошлого входа: аккаунт сохранён, а сети нет
store.save({
    "settings": {"language": "ru"},
    "accounts": [{"login": "gosha", "name": "Гоша", "server": СЕРВЕР,
                  "token": "токен"}],
    "last": f"gosha@{СЕРВЕР}",
    "last_room": {СЕРВЕР: 3},
})

localcache.save_rooms(СЕРВЕР, {"id": 1, "login": "gosha", "name": "Гоша"},
                      [{"id": 3, "kind": "direct", "title": "Лена", "user": 2},
                       {"id": 4, "kind": "direct", "title": "Руслан", "user": 5}])
localcache.save_history(СЕРВЕР, 3, ЛЕНТА)

app = gui.VelixApp()
app.attributes("-topmost", True)
steps = []
запомнили = {}


def step(function):
    steps.append(function)
    return function


def подписи():
    найдено = []

    def обход(widget):
        for child in widget.winfo_children():
            if isinstance(child, gui.ctk.CTkLabel) and child.cget("text"):
                найдено.append(str(child.cget("text")))
            обход(child)

    обход(app.messages)
    return найдено


# ------------------------------------------------ вход, когда сети нет вовсе

@step
def войти_без_сети():
    аккаунт = store.load()["accounts"][0]
    app._enter_saved(аккаунт)


@step
def проверить_показанное():
    check("offline-cache-isolated",
          str(check_isolated).startswith(os.environ["VELIX_CACHE"])
          or str(check_isolated).startswith(str(уголок)),
          f"кэш ушёл мимо песочницы: {check_isolated}")
    check("offline-chat-shown", app.chat_view.winfo_ismapped(),
          "окно осталось на экране входа")
    check("offline-rooms-listed", len(app.conversations) == 2, app.conversations)
    check("offline-opened-last", app.conversation == 3, app.conversation)
    строки = подписи()
    check("offline-history-drawn", "первое сохранённое" in строки
          and "второе сохранённое" in строки, строки)
    check("offline-says-so", any("сохранённое" in one.lower() for one in строки),
          строки)
    check("offline-marked", app.from_cache is True, app.from_cache)


# ------------------------------------------- писать можно: уйдёт потом

@step
def написать_без_сети():
    app.message_entry.delete(0, "end")
    app.message_entry.insert(0, "пишу из метро")
    app._on_send()


@step
def проверить_очередь():
    check("offline-can-write", len(app.outbox) == 1, app.outbox)
    check("offline-write-shown", "пишу из метро" in подписи(), подписи())


# --------------------------------- сеть появилась: живое вытесняет сохранённое

@step
def поднять_сервер():
    async def притворщик(websocket):
        await websocket.recv()
        await websocket.send(protocol.welcome_message(
            {"id": 1, "login": "gosha", "name": "Гоша"}, "токен"))
        await websocket.send(protocol.conversations_message([
            {"id": 3, "kind": "direct", "title": "Лена", "user": 2},
        ]))
        while True:
            кадр = protocol.decode(await websocket.recv())
            if кадр is None:
                continue
            if кадр.get("type") == "open":
                await websocket.send(protocol.history_page(
                    кадр["conversation"], ЛЕНТА + [
                        {"id": 3, "nick": "Лена", "kind": "text",
                         "text": "свежее с сервера",
                         "at": "2026-08-27T10:00:00+00:00", "user": 2}],
                    {}, False))

    def сервер():
        async def run():
            import websockets
            async with websockets.serve(притворщик, "localhost", PORT,
                                        max_size=protocol.MAX_FRAME_SIZE):
                await asyncio.Future()

        петля = asyncio.new_event_loop()
        asyncio.set_event_loop(петля)
        петля.run_until_complete(run())

    threading.Thread(target=сервер, daemon=True).start()
    time.sleep(1.0)
    app.network.connect(gui.connection_uris(СЕРВЕР))


@step
def подождать():
    pass


@step
def проверить_живое():
    check("offline-live-takes-over", app.from_cache is False, app.from_cache)
    строки = подписи()
    check("offline-fresh-shown", "свежее с сервера" in строки, строки)


@step
def проверить_сохранение():
    # Свежая история должна была лечь на диск поверх прежней
    сохранено = [one.get("text") for one
                 in localcache.load_history(СЕРВЕР, 3)]
    check("offline-cache-updated", "свежее с сервера" in сохранено, сохранено)


@step
def finish():
    app.destroy()


delay = 900
паузы = {"подождать": 3000}
for function in steps:
    app.after(delay, function)
    delay += паузы.get(function.__name__, 1600)

app.mainloop()

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
