"""Окно: недописанное не пропадает, а написанное без связи доходит потом."""

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

уголок = Path(tempfile.mkdtemp(prefix="velix-draft-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import protocol  # noqa: E402
import gui  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


PORT = int(os.environ.get("VELIX_TESTPORT", "8833"))
пришло = []
живой = {"сокет": None}


async def притворщик(websocket):
    живой["сокет"] = websocket
    await websocket.recv()
    await websocket.send(protocol.welcome_message(
        {"id": 1, "login": "gosha", "name": "Гоша"}, "токен"))
    await websocket.send(protocol.conversations_message([
        {"id": 3, "kind": "direct", "title": "Лена", "user": 2},
        {"id": 4, "kind": "direct", "title": "Руслан", "user": 5},
    ]))

    while True:
        кадр = protocol.decode(await websocket.recv())
        if кадр is None:
            continue
        if кадр.get("type") == "open":
            await websocket.send(protocol.history_page(
                кадр["conversation"], [], {}, False))
        elif кадр.get("type") == "text":
            пришло.append(кадр)


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


@step
def sign_in():
    app._show_form(register=False)
    app.server_entry.insert(0, f"localhost:{PORT}")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "secret123")
    app._on_primary()


# ------------------------------------------------------------ черновики

@step
def набрать_черновик():
    app._open(3, force=True)
    app.message_entry.delete(0, "end")
    app.message_entry.insert(0, "недописанное письмо")


@step
def уйти_в_другую():
    app._open(4, force=True)


@step
def проверить_чистоту():
    check("draft-other-chat-empty", app.message_entry.get() == "",
          app.message_entry.get())
    app._open(3, force=True)


@step
def проверить_возврат():
    check("draft-comes-back", app.message_entry.get() == "недописанное письмо",
          app.message_entry.get())
    check("draft-saved-to-config",
          "недописанное письмо" in str(store.load().get("drafts")),
          store.load().get("drafts"))


@step
def отправить_черновик():
    app._on_send()


@step
def проверить_очистку():
    check("draft-cleared-after-send", 3 not in app.drafts, app.drafts)
    check("draft-sent-text", пришло and пришло[-1]["text"] == "недописанное письмо",
          пришло[-1] if пришло else None)


# --------------------------------------------------------- очередь

@step
def оборвать_связь():
    # Как будто выдернули провод: сокет прячем, но потом вернём тот же самый
    живой["клиентский"] = app.network.websocket
    app.network.websocket = None


@step
def написать_без_связи():
    app.message_entry.delete(0, "end")
    app.message_entry.insert(0, "письмо без связи")
    app._on_send()


@step
def проверить_очередь():
    check("outbox-queued", len(app.outbox) == 1, app.outbox)
    check("outbox-not-sent-yet",
          all(one.get("text") != "письмо без связи" for one in пришло), пришло)
    тексты = [one.get("text") for one in app.loaded_items]
    check("outbox-shown-in-feed", "письмо без связи" in тексты, тексты)
    check("outbox-marked-waiting",
          app.states.get(app.outbox[0][0]) == "waiting" if app.outbox else False,
          app.states)


@step
def вернуть_связь():
    app.network.websocket = живой["клиентский"]
    app._flush_outbox()


@step
def проверить_досылку():
    check("outbox-flushed", not app.outbox, app.outbox)
    check("outbox-arrived",
          any(one.get("text") == "письмо без связи" for one in пришло), пришло)


@step
def finish():
    app.destroy()


delay = 900
for function in steps:
    app.after(delay, function)
    delay += 1400

app.mainloop()

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
