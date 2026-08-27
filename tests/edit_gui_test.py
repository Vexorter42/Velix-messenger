"""Окно: своё сообщение правится в той же строке ввода."""

import asyncio
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

уголок = Path(tempfile.mkdtemp(prefix="velix-edit-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import protocol  # noqa: E402
import gui  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


PORT = int(os.environ.get("VELIX_TESTPORT", "8831"))
ЛЕНТА = [
    {"id": 5, "nick": "Гоша", "kind": "text", "text": "привет",
     "at": "2026-08-27T09:00:00+00:00", "user": 1},
]
видано = []


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
            await websocket.send(protocol.history_page(3, ЛЕНТА, {}, False))
        elif кадр.get("type") == "edit":
            видано.append(кадр)
            await websocket.send(protocol.edited_message(
                3, кадр["id"], кадр["text"], "2026-08-27T09:05:00+00:00"))


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
time.sleep(1.2)

app = gui.VelixApp()
app.attributes("-topmost", True)
steps = []


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


@step
def sign_in():
    app._show_form(register=False)
    app.server_entry.insert(0, f"localhost:{PORT}")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "secret123")
    app._on_primary()


@step
def открыть():
    app._open(3, force=True)


@step
def начать_правку():
    check("edit-gui-message-shown", "привет" in подписи(), подписи())
    app._start_edit(ЛЕНТА[0])


@step
def проверить_строку():
    check("edit-gui-text-back", app.message_entry.get() == "привет",
          app.message_entry.get())
    check("edit-gui-marker", "Правим" in app.reply_label.cget("text"),
          app.reply_label.cget("text"))
    check("edit-gui-not-reply", app.reply_to is None, app.reply_to)
    app.message_entry.delete(0, "end")
    app.message_entry.insert(0, "привет, как дела")
    app._on_send()


@step
def проверить_отправку():
    check("edit-gui-sent", видано and видано[-1]["text"] == "привет, как дела",
          видано)
    check("edit-gui-not-new-message",
          all(one.get("type") == "edit" for one in видано), видано)
    check("edit-gui-bar-closed", app.editing is None, app.editing)


@step
def проверить_ленту():
    строки = подписи()
    check("edit-gui-bubble-updated", "привет, как дела" in строки, строки)
    check("edit-gui-marked", any("изменено" in one for one in строки), строки)
    check("edit-gui-no-double", строки.count("привет") == 0, строки)


@step
def finish():
    app.destroy()


delay = 900
for function in steps:
    app.after(delay, function)
    delay += 1600

app.mainloop()

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
